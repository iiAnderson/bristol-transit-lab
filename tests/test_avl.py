import datetime as dt
import io
import json
import random
import shutil
import zipfile

import pyarrow.parquet as pq
import pytest
import yaml

from lab.config import LabConfig
from lab.supply import avl

UTC = dt.timezone.utc
BOX = (-3.0, 51.0, -2.0, 52.0)
BASE = "https://archive.test/sirivm/"


def siri(resp: str, vehicles: list[tuple]) -> bytes:
    """vehicles: (operator, vehicle, recorded_at, lon, lat)."""
    va = "".join(
        f"<VehicleActivity><RecordedAtTime>{rec}</RecordedAtTime>"
        f"<MonitoredVehicleJourney><LineRef>1</LineRef><DirectionRef>outbound</DirectionRef>"
        f"<FramedVehicleJourneyRef><DataFrameRef>2026-09-23</DataFrameRef>"
        f"<DatedVehicleJourneyRef>j1</DatedVehicleJourneyRef></FramedVehicleJourneyRef>"
        f"<OperatorRef>{op}</OperatorRef><VehicleLocation><Longitude>{lon}</Longitude>"
        f"<Latitude>{lat}</Latitude></VehicleLocation><Bearing>90</Bearing>"
        f"<VehicleRef>{veh}</VehicleRef></MonitoredVehicleJourney>"
        f"<Extensions><VehicleJourney><Operational><TicketMachine><JourneyCode>0700"
        f"</JourneyCode></TicketMachine></Operational></VehicleJourney></Extensions>"
        f"</VehicleActivity>"
        for op, veh, rec, lon, lat in vehicles)
    return (f'<Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery>'
            f"<ResponseTimestamp>{resp}</ResponseTimestamp><VehicleMonitoringDelivery>"
            f"{va}</VehicleMonitoringDelivery></ServiceDelivery></Siri>").encode()


def zipped(xml: bytes) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("siri.xml", xml)
    return b.getvalue()


# ---------------------------------------------------------------- parsing and dedup

def test_parse_clips_to_box_and_counts_invalid():
    xml = siri("2026-09-23T06:00:00+00:00", [
        ("FB", "1", "2026-09-23T05:59:50+00:00", -2.5, 51.5),
        ("FB", "2", "2026-09-23T05:59:50+00:00", -1.0, 51.5),      # outside
        ("FB", "3", "", -2.5, 51.5),                                 # no time
    ])
    s = avl.parse_siri(io.BytesIO(xml), BOX)
    assert (s.n_total, s.n_invalid, len(s.rows)) == (3, 1, 1)
    r = s.rows[0]
    assert r["vehicle_ref"] == "1" and r["journey_code"] == "0700"
    assert r["dated_vehicle_journey_ref"] == "j1" and r["bearing"] == 90.0
    assert s.response_ts == dt.datetime(2026, 9, 23, 6, tzinfo=UTC)


def test_dedup_drops_repeats_and_flags_stale():
    d = avl.Deduper(nominal_s=30)
    a = avl.parse_siri(io.BytesIO(siri("2026-09-23T06:00:00Z", [
        ("FB", "1", "2026-09-23T05:59:50Z", -2.5, 51.5),
        ("FB", "2", "2026-09-22T18:00:00Z", -2.5, 51.5)])), BOX)
    kept, st = d.apply(a, source="archive", snapshot="s1")
    assert st["n_kept"] == 2 and st["n_stale"] == 1                 # 12 h old: stale
    b = avl.parse_siri(io.BytesIO(siri("2026-09-23T06:00:30Z", [
        ("FB", "1", "2026-09-23T05:59:50Z", -2.5, 51.5),              # repeat
        ("FB", "1", "2026-09-23T06:00:20Z", -2.5, 51.5),              # new
        ("FB", "2", "2026-09-22T18:00:00Z", -2.5, 51.5)])), BOX)      # repeat
    kept, st = d.apply(b, source="archive", snapshot="s2")
    assert st["n_repeat"] == 2 and [k["vehicle_ref"] for k in kept] == ["1"]
    assert kept[0]["age_s"] == 10 and kept[0]["stale"] is False


def test_dedup_seeded_from_parts_skips_what_was_kept():
    d = avl.Deduper(nominal_s=30)
    s = avl.parse_siri(io.BytesIO(siri("2026-09-23T06:00:00Z", [
        ("FB", "1", "2026-09-23T05:59:50Z", -2.5, 51.5)])), BOX)
    kept, _ = d.apply(s, source="archive", snapshot="s1")
    d2 = avl.Deduper(nominal_s=30)
    d2.seed(avl.to_table(kept), s.response_ts)
    _, st = d2.apply(s, source="archive", snapshot="s1")
    assert st["n_kept"] == 0 and st["n_repeat"] == 1


def test_window_is_local_time():
    s, e = avl.window_utc(dt.date(2026, 9, 23), ["07:00", "16:00"], "Europe/London")
    assert s == dt.datetime(2026, 9, 23, 6, tzinfo=UTC)
    assert e == dt.datetime(2026, 9, 23, 15, tzinfo=UTC)


# ---------------------------------------------------------------- policy

POL = dict(min_interval_s=5, max_interval_s=20, timeout_s=60, backoff_start_s=30,
           backoff_max_s=900, breaker_consecutive=5, breaker_window_s=900,
           breaker_fail_share=0.10, breaker_min_requests=20, latency_window=50,
           latency_factor=2.0)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_breaker_on_consecutive_failures():
    c = Clock()
    g = avl.Governor(POL, c)
    for i in range(4):
        g.record(True, None)
        c.t += 30
    assert g.halt is None
    g.record(True, None)
    assert "5 consecutive" in g.halt


def test_breaker_on_failure_share_needs_min_requests():
    c = Clock()
    g = avl.Governor(POL, c)
    g.record(True, None)                  # 1 of 1 failed: too few requests to judge
    assert g.halt is None
    for _ in range(18):
        c.t += 5
        g.record(False, 0.1)
    assert g.halt is None                 # 1 of 19
    c.t += 5
    g.record(True, None)
    c.t += 5
    g.record(True, None)                  # 3 of 21 > 10%
    assert g.halt and "3 of 21" in g.halt


def test_failure_share_window_rolls():
    c = Clock()
    g = avl.Governor(POL, c)
    for _ in range(3):
        g.record(True, None)
        g.record(False, 0.1)
    c.t += 901                            # the early failures leave the window
    for _ in range(30):
        c.t += 5
        g.record(False, 0.1)
    assert g.halt is None


def test_latency_guard_doubles_interval_to_the_cap():
    c = Clock()
    g = avl.Governor(POL, c)
    for _ in range(50):
        g.record(False, 0.1)
    events = []
    for _ in range(150):
        events += g.record(False, 0.5)
    assert g.interval == 20
    assert sum("interval" in e and "->" in e for e in events) == 2


def test_backoff_doubles_with_ceiling_and_honours_retry_after():
    g = avl.Governor(POL, Clock())
    rng = random.Random(1)
    d = [g.backoff_s(k, None, rng) for k in range(8)]
    assert 30 <= d[0] <= 33 and 60 <= d[1] <= 66
    assert d[-1] == 900 and all(x <= 900 for x in d)
    assert g.backoff_s(0, 120, rng) == 120
    now = dt.datetime(2026, 9, 27, 12, tzinfo=UTC)
    assert avl.retry_after_s({"Retry-After": "Sun, 27 Sep 2026 12:02:00 GMT"}, now) == 120


# ---------------------------------------------------------------- archive fetch

@pytest.fixture
def root(cfg, tmp_path):
    """A lab root in tmp: real params, config pointed at a fake archive and tmp paths."""
    shutil.copytree(cfg.root / "params", tmp_path / "params")
    (tmp_path / "config").mkdir()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    raw["paths"]["avl"] = "avl"
    raw["paths"]["progress_log"] = "P2.md"
    raw["avl"]["archive"]["base_url"] = BASE
    raw["avl"]["archive"]["days"] = ["2026-09-23"]
    (tmp_path / "config" / "lab.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "P2.md").write_text("| Date | Stage | Entry |\n|---|---|---|\n")
    return tmp_path


STEMS = ["sirivm-20260923T055931", "sirivm-20260923T060001", "sirivm-20260923T060031",
         "sirivm-20260923T060101", "sirivm-20260923T150001"]   # first and last outside


class FakeArchive:
    def __init__(self, fail_with=None, missing=()):
        self.calls = []
        self.fail_with = fail_with
        self.missing = set(missing)

    def __call__(self, url, params=None):
        self.calls.append(url)
        if self.fail_with:
            if self.fail_with == "timeout":
                raise avl.TransientError("ReadTimeout")
            return avl.Http(self.fail_with, {}, b"", 0.1)
        if url.endswith("/"):
            body = "".join(f'<a href="{s}.zip">{s}.zip</a>' for s in STEMS)
            return avl.Http(200, {}, body.encode(), 0.1)
        stem = url.rsplit("/", 1)[1][:-4]
        if stem in self.missing:
            return avl.Http(404, {}, b"", 0.1)
        t = dt.datetime.strptime(stem[7:], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        rec = (t - dt.timedelta(seconds=10)).isoformat()
        return avl.Http(200, {}, zipped(siri(t.isoformat(), [("FB", "1", rec, -2.6, 51.45)])),
                        0.1)


def fetcher(root, get):
    c = Clock()
    return avl.ArchiveFetch(LabConfig.load(root), get, clock=c, sleep=c.sleep,
                            rng=random.Random(0)), c


def test_archive_fetch_clips_closes_day_and_records_gaps(root):
    get = FakeArchive(missing={"sirivm-20260923T060031"})
    f, clock = fetcher(root, get)
    assert f.run() == "ok"
    assert len(get.calls) == 4                    # listing + 3 snapshots in the window
    assert clock.t >= 3 * 5                       # rate floor between request starts
    m = [json.loads(x) for x in (root / "avl/archive/manifest.jsonl").read_text().splitlines()]
    assert [x["status"] for x in m] == ["done", "gap", "done"]
    day = json.loads((root / "avl/archive/days.jsonl").read_text())
    assert (day["n_listed"], day["n_done"], day["n_gap"], day["n_rows"]) == (3, 2, 1, 2)
    t = pq.read_table(root / "avl/archive" / day["file"])
    assert t.num_rows == 2
    assert not list((root / "avl/archive/parts").glob("*/*"))
    assert not list(root.rglob("*.zip"))          # national files never touch disk


def test_archive_resume_skips_completed_snapshots(root):
    get = FakeArchive()
    f, _ = fetcher(root, get)
    (root / "avl/archive").mkdir(parents=True)
    (root / "avl/archive/PAUSE").touch()
    assert f.run() == "paused" and get.calls == []
    (root / "avl/archive/PAUSE").unlink()
    # fetch the listing and one snapshot, then pause
    f, _ = fetcher(root, get)
    orig = f._snapshot

    def once(day, stem, ded):
        ok = orig(day, stem, ded)
        (root / "avl/archive/PAUSE").touch()
        return ok
    f._snapshot = once
    assert f.run() == "paused"
    (root / "avl/archive/PAUSE").unlink()
    get2 = FakeArchive()
    f2, _ = fetcher(root, get2)
    assert f2.run() == "ok"
    assert len(get2.calls) == 2                   # no listing, two remaining snapshots
    day = json.loads((root / "avl/archive/days.jsonl").read_text())
    assert (day["n_done"], day["n_rows"]) == (3, 3)


@pytest.mark.parametrize("failure", [503, 429, "timeout"])
def test_archive_halts_after_consecutive_failures_and_stays_halted(root, failure):
    f, clock = fetcher(root, FakeArchive(fail_with=failure))
    with pytest.raises(avl.Halted, match="5 consecutive"):
        f.run()
    assert f.backoff_total >= 30 + 60 + 120 + 240
    assert (root / "avl/archive/HALTED").exists()
    assert "**HALTED**" in (root / "P2.md").read_text().splitlines()[-1]
    get = FakeArchive()
    f2, _ = fetcher(root, get)
    with pytest.raises(avl.Halted, match="not cleared"):
        f2.run()
    assert get.calls == []
    assert f2.run(clear_halt=True) == "ok"


def test_archive_status_summarises(root):
    f, _ = fetcher(root, FakeArchive(missing={"sirivm-20260923T060031"}))
    f.run()
    s = avl.archive_status(LabConfig.load(root))
    assert s["state"] == "complete" and s["requests"] == 4 and s["failed"] == 0
    assert s["days"][0] | {"sha256": None} == {"day": "2026-09-23", "listed": 3, "done": 2,
                                               "gap": 1, "rows": 2, "closed": True,
                                               "sha256": None}


def test_live_http_errors_never_expose_the_key(monkeypatch):
    import requests
    get = avl.requests_get("ua", timeout=0.001)

    def boom(*a, **k):
        raise requests.ConnectionError("https://x/?api_key=SECRET failed")
    monkeypatch.setattr(requests.Session, "get", boom)
    with pytest.raises(avl.TransientError) as e:
        get("https://x/", params={"api_key": "SECRET"})
    assert "SECRET" not in str(e.value) and e.value.__cause__ is None


def test_live_collects_through_the_window_and_closes_the_day(root):
    raw = yaml.safe_load((root / "config" / "lab.yaml").read_text())
    raw["avl"]["live"].update(days=["2026-09-29"], hours=["07:00", "07:01"],
                              flush_every_polls=2)
    (root / "config" / "lab.yaml").write_text(yaml.safe_dump(raw))
    t = [dt.datetime(2026, 9, 29, 5, 59, 30, tzinfo=UTC)]
    seen = []

    def get(url, params=None):
        seen.append(params)
        now = t[0]
        if len(seen) == 3:
            raise avl.TransientError("ConnectTimeout")
        return avl.Http(200, {}, siri(now.isoformat(), [
            ("FB", "1", (now - dt.timedelta(seconds=5)).isoformat(), -2.6, 51.45),
            ("FB", "9", "2026-09-28T18:00:00Z", -2.6, 51.45)]), 0.2)

    def sleep(s):
        t[0] += dt.timedelta(seconds=s)
    c = avl.LiveCollect(LabConfig.load(root), get, "KEY", sleep=sleep, now=lambda: t[0])
    assert c.run() == "ok"
    assert seen[0]["api_key"] == "KEY" and len(seen[0]["boundingBox"].split(",")) == 4
    day = json.loads((root / "avl/live/days.jsonl").read_text())
    tab = pq.read_table(root / "avl/live" / day["file"])
    assert tab.column("vehicle_ref").to_pylist().count("9") == 1          # stale, kept once
    assert tab.num_rows == 1 + (len(seen) - 1)                              # one failed poll
    assert "KEY" not in (root / "avl/live/polls.jsonl").read_text()
    s = avl.live_status(LabConfig.load(root))["days"][0]
    assert s["failed_polls"] == 1 and s["closed"]
