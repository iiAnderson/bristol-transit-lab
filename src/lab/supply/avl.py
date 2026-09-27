"""
Bus vehicle locations (AVL) from BODS SIRI-VM (plans/P2.md, A6 as decided).

Two sources, one clipped format:

* **archive** — the National Data Library BODS archive: national ``siri.xml``
  snapshots every ~30 s. Fetched one at a time under a courtesy policy (rate floor,
  backoff, circuit breaker, latency guard), clipped to the extent buffer in memory, and
  never written to disk whole. Calibration data for P2b.
* **live** — the BODS API itself, polled every 10 s over the extent buffer for a few
  days. Used only to measure how 30 s spacing biases link speeds.

Both sources share the parser and the de-duplication: a position is kept only if it is
newer than the last kept position for that vehicle. Positions recorded before the
previous snapshot/poll are *flagged* ``stale`` with their age, not dropped: feed lag
(median ~22 s) is close to the archive's spacing, so dropping them here would lose
late-published positions irreversibly. B2 applies the stale filter.

Nothing here knows a place: the box comes from ``extent`` plus ``supply.clip_buffer_km``,
and days, hours, URLs and the time zone from config/lab.yaml (rule 6).
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import io
import json
import math
import random
import re
import shutil
import statistics
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from .. import __version__, params
from ..config import LabConfig

UTC = dt.timezone.utc
NS = "{http://www.siri.org.uk/siri}"
SNAPSHOT_RE = re.compile(r"sirivm-(\d{8}T\d{6})\.zip")

SCHEMA = pa.schema([
    ("source", pa.string()),                 # archive | live
    ("snapshot", pa.string()),               # archive file stem, or live poll time
    ("response_ts", pa.timestamp("ms", tz="UTC")),
    ("recorded_at", pa.timestamp("ms", tz="UTC")),
    ("age_s", pa.float64()),                 # response_ts − recorded_at
    ("stale", pa.bool_()),                   # recorded before the previous snapshot/poll
    ("operator_ref", pa.string()),
    ("vehicle_ref", pa.string()),
    ("line_ref", pa.string()),
    ("published_line_name", pa.string()),
    ("direction_ref", pa.string()),
    ("dated_vehicle_journey_ref", pa.string()),
    ("data_frame_ref", pa.string()),
    ("origin_ref", pa.string()),
    ("destination_ref", pa.string()),
    ("origin_aimed_departure", pa.timestamp("ms", tz="UTC")),
    ("block_ref", pa.string()),
    ("journey_code", pa.string()),
    ("lon", pa.float64()),
    ("lat", pa.float64()),
    ("bearing", pa.float64()),
])


class AvlError(RuntimeError):
    pass


class Halted(AvlError):
    """The circuit breaker tripped, or a halt is on file. Never cleared automatically."""


class TransientError(Exception):
    """Timeout or connection failure: retried with backoff."""


@dataclass(frozen=True)
class Http:
    status: int
    headers: dict
    body: bytes
    ttfb: float          # seconds to response headers (independent of file size)


Get = Callable[..., Http]


# ---------------------------------------------------------------------------- config

def avl_config(cfg: LabConfig) -> dict:
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    if "avl" not in raw or "avl" not in raw.get("paths", {}):
        raise AvlError("config/lab.yaml has no `avl` block or `paths.avl`")
    a = dict(raw["avl"])
    a["dir"] = cfg.root / raw["paths"]["avl"]
    a["progress_log"] = cfg.root / raw["paths"]["progress_log"]
    for src in ("archive", "live"):
        a[src]["days"] = [d if isinstance(d, dt.date) else dt.date.fromisoformat(d)
                          for d in a[src]["days"]]
    return a


def clip_box(cfg: LabConfig) -> tuple[float, float, float, float]:
    """The extent grown by supply.clip_buffer_km on every side."""
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    km = float(ps["supply.clip_buffer_km"])
    x0, y0, x1, y1 = cfg.extent
    dlat = km / 111.32
    dlon = km / (111.32 * math.cos(math.radians((y0 + y1) / 2)))
    return (x0 - dlon, y0 - dlat, x1 + dlon, y1 + dlat)


def window_utc(day: dt.date, hours: list[str], tz: str) -> tuple[dt.datetime, dt.datetime]:
    """[start, end) of a local-time window on ``day``, in UTC."""
    z = ZoneInfo(tz)
    a, b = (dt.time.fromisoformat(h) for h in hours)
    return (dt.datetime.combine(day, a, z).astimezone(UTC),
            dt.datetime.combine(day, b, z).astimezone(UTC))


# ---------------------------------------------------------------------------- parsing

def _ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.strip())
    if t.tzinfo is None:
        raise AvlError(f"SIRI time without an offset: {s!r}")
    return t.astimezone(UTC)


def _f(s: str | None) -> float | None:
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


@dataclass
class Snapshot:
    response_ts: dt.datetime | None
    n_total: int = 0
    n_invalid: int = 0
    rows: list[dict] = field(default_factory=list)


def parse_siri(f, box: tuple[float, float, float, float]) -> Snapshot:
    """Stream a SIRI-VM document; keep VehicleActivity inside ``box``."""
    x0, y0, x1, y1 = box
    snap = Snapshot(response_ts=None)
    for _, el in ET.iterparse(f, events=("end",)):
        if el.tag == NS + "ResponseTimestamp" and snap.response_ts is None:
            snap.response_ts = _ts(el.text)
        if el.tag != NS + "VehicleActivity":
            continue
        snap.n_total += 1
        mvj = el.find(NS + "MonitoredVehicleJourney")
        loc = mvj.find(NS + "VehicleLocation") if mvj is not None else None
        lon = _f(loc.findtext(NS + "Longitude")) if loc is not None else None
        lat = _f(loc.findtext(NS + "Latitude")) if loc is not None else None
        rec = _ts(el.findtext(NS + "RecordedAtTime"))
        veh = mvj.findtext(NS + "VehicleRef") if mvj is not None else None
        if lon is None or lat is None or rec is None or not veh:
            snap.n_invalid += 1
            el.clear()
            continue
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            fvj = mvj.find(NS + "FramedVehicleJourneyRef")
            snap.rows.append({
                "recorded_at": rec,
                "operator_ref": mvj.findtext(NS + "OperatorRef"),
                "vehicle_ref": veh,
                "line_ref": mvj.findtext(NS + "LineRef"),
                "published_line_name": mvj.findtext(NS + "PublishedLineName"),
                "direction_ref": mvj.findtext(NS + "DirectionRef"),
                "dated_vehicle_journey_ref": (fvj.findtext(NS + "DatedVehicleJourneyRef")
                                              if fvj is not None else None),
                "data_frame_ref": (fvj.findtext(NS + "DataFrameRef")
                                   if fvj is not None else None),
                "origin_ref": mvj.findtext(NS + "OriginRef"),
                "destination_ref": mvj.findtext(NS + "DestinationRef"),
                "origin_aimed_departure": _ts(mvj.findtext(NS + "OriginAimedDepartureTime")),
                "block_ref": mvj.findtext(NS + "BlockRef"),
                "journey_code": el.findtext(f".//{NS}JourneyCode"),
                "lon": lon,
                "lat": lat,
                "bearing": _f(mvj.findtext(NS + "Bearing")),
            })
        el.clear()
    if snap.response_ts is None:
        raise AvlError("SIRI-VM document has no ResponseTimestamp")
    return snap


# ---------------------------------------------------------------------------- de-duplication

class Deduper:
    """Keep a position only if it is newer than the last one kept for that vehicle.

    ``stale`` marks positions recorded before the previous snapshot/poll (before
    ``response_ts − nominal_s`` on the first one of a run)."""

    def __init__(self, nominal_s: float):
        self.nominal_s = nominal_s
        self.last: dict[tuple, dt.datetime] = {}
        self.prev_ts: dt.datetime | None = None

    def seed(self, table: pa.Table | None, prev_ts: dt.datetime | None) -> None:
        if table is not None and table.num_rows:
            for op, veh, rec in zip(table["operator_ref"].to_pylist(),
                                    table["vehicle_ref"].to_pylist(),
                                    table["recorded_at"].to_pylist()):
                k = (op, veh)
                if k not in self.last or rec > self.last[k]:
                    self.last[k] = rec
        self.prev_ts = prev_ts

    def apply(self, snap: Snapshot, *, source: str, snapshot: str) -> tuple[list[dict], dict]:
        cut = self.prev_ts or snap.response_ts - dt.timedelta(seconds=self.nominal_s)
        kept, repeats, stale = [], 0, 0
        for r in sorted(snap.rows, key=lambda r: r["recorded_at"]):
            k = (r["operator_ref"], r["vehicle_ref"])
            last = self.last.get(k)
            if last is not None and r["recorded_at"] <= last:
                repeats += 1
                continue
            self.last[k] = r["recorded_at"]
            is_stale = r["recorded_at"] < cut
            stale += is_stale
            kept.append({**r, "source": source, "snapshot": snapshot,
                         "response_ts": snap.response_ts,
                         "age_s": (snap.response_ts - r["recorded_at"]).total_seconds(),
                         "stale": is_stale})
        self.prev_ts = snap.response_ts
        return kept, {"n_total": snap.n_total, "n_invalid": snap.n_invalid,
                      "n_bbox": len(snap.rows), "n_kept": len(kept),
                      "n_repeat": repeats, "n_stale": stale}


def to_table(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=SCHEMA)


# ---------------------------------------------------------------------------- courtesy policy

class Governor:
    """Rate floor, backoff, circuit breaker and latency guard for the archive fetch."""

    def __init__(self, pol: dict, clock: Callable[[], float]):
        self.p = pol
        self.clock = clock
        self.interval = float(pol["min_interval_s"])
        self.last_start: float | None = None
        self.consecutive = 0
        self.window: deque[tuple[float, bool]] = deque()
        self.ttfb: list[float] = []
        self.baseline: float | None = None
        self.checked_at = 0
        self.halt: str | None = None

    def wait_s(self) -> float:
        if self.last_start is None:
            return 0.0
        return max(0.0, self.last_start + self.interval - self.clock())

    def started(self) -> None:
        self.last_start = self.clock()

    def record(self, failed: bool, ttfb: float | None) -> list[str]:
        """Account for one finished request; return any events worth logging."""
        events = []
        t = self.clock()
        self.window.append((t, failed))
        while self.window and self.window[0][0] < t - self.p["breaker_window_s"]:
            self.window.popleft()
        self.consecutive = self.consecutive + 1 if failed else 0
        n = len(self.window)
        nf = sum(f for _, f in self.window)
        if self.consecutive >= self.p["breaker_consecutive"]:
            self.halt = f"{self.consecutive} consecutive failed requests"
        elif n >= self.p["breaker_min_requests"] and nf / n > self.p["breaker_fail_share"]:
            self.halt = (f"{nf} of {n} requests failed in the last "
                         f"{self.p['breaker_window_s'] / 60:.0f} minutes "
                         f"(> {self.p['breaker_fail_share']:.0%})")
        if not failed and ttfb is not None:
            events += self._latency(ttfb)
        return events

    def _latency(self, ttfb: float) -> list[str]:
        w = self.p["latency_window"]
        self.ttfb.append(ttfb)
        n = len(self.ttfb)
        if n == w:
            self.baseline = statistics.median(self.ttfb)
            return [f"latency baseline: median TTFB of first {w} requests "
                    f"{self.baseline:.3f} s"]
        if n < 2 * w or n - self.checked_at < w:
            return []
        self.checked_at = n
        recent = statistics.median(self.ttfb[-w:])
        if recent <= self.p["latency_factor"] * self.baseline:
            return []
        if self.interval < self.p["max_interval_s"]:
            old = self.interval
            self.interval = min(self.p["max_interval_s"], self.interval * 2)
            return [f"latency guard: median TTFB of last {w} {recent:.3f} s > "
                    f"{self.p['latency_factor']:g} x baseline {self.baseline:.3f} s; "
                    f"interval {old:g} s -> {self.interval:g} s"]
        return [f"latency guard: median TTFB of last {w} {recent:.3f} s still > "
                f"{self.p['latency_factor']:g} x baseline at the maximum interval "
                f"{self.interval:g} s"]

    def backoff_s(self, attempt: int, retry_after: float | None, rng: random.Random) -> float:
        base = min(self.p["backoff_max_s"], self.p["backoff_start_s"] * 2 ** attempt)
        d = min(self.p["backoff_max_s"], base * (1 + rng.uniform(0, 0.1)))
        if retry_after is not None:
            d = max(d, retry_after)
        return max(d, self.interval)


def retry_after_s(headers: dict, now: dt.datetime) -> float | None:
    v = next((headers[k] for k in headers if k.lower() == "retry-after"), None)
    if v is None:
        return None
    try:
        return max(0.0, float(v))
    except ValueError:
        pass
    try:
        return max(0.0, (email.utils.parsedate_to_datetime(v) - now).total_seconds())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------- HTTP

def requests_get(user_agent: str, timeout: float) -> Get:
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = user_agent

    def get(url: str, params: dict | None = None) -> Http:
        try:
            r = s.get(url, params=params, timeout=timeout)
            body = r.content
        except requests.RequestException as e:
            # The message can hold the full URL, query string included: never pass it on.
            raise TransientError(type(e).__name__) from None
        return Http(r.status_code, dict(r.headers), body, r.elapsed.total_seconds())
    return get


# ---------------------------------------------------------------------------- files

def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_parts(d: Path) -> pa.Table | None:
    files = sorted(d.glob("*.parquet")) if d.is_dir() else []
    if not files:
        return None
    return pa.concat_tables([pq.read_table(f, schema=SCHEMA) for f in files])


def close_day(base: Path, day: dt.date, stem: str, extra: dict) -> dict:
    """Merge a day's parts into ``<stem>_<day>.parquet``, hash it, drop the parts."""
    parts = base / "parts" / day.isoformat()
    t = _read_parts(parts) or to_table([])
    t = t.sort_by([("operator_ref", "ascending"), ("vehicle_ref", "ascending"),
                   ("recorded_at", "ascending")])
    out = base / f"{stem}_{day.isoformat()}.parquet"
    pq.write_table(t, out, compression="zstd")
    rec = {"day": day.isoformat(), "file": out.name, "n_rows": t.num_rows,
           "sha256": _sha256(out), "bytes": out.stat().st_size,
           "closed_at": dt.datetime.now(UTC).isoformat(), **extra}
    _append_jsonl(base / "days.jsonl", rec)
    shutil.rmtree(parts, ignore_errors=True)
    return rec


# ---------------------------------------------------------------------------- archive

class ArchiveFetch:
    """Resumable, pausable fetch of archive snapshots under the courtesy policy.

    State lives in ``<avl>/archive/``: ``manifest.jsonl`` (one line per snapshot, done
    or gap), ``requests.jsonl`` (every HTTP attempt and policy event), ``days.jsonl``
    (closed day files with hashes), ``index/<day>.json`` (snapshots listed in the
    window), ``parts/<day>/`` (clipped snapshots of the open day). ``PAUSE`` stops the
    run cleanly before the next request; ``HALTED`` blocks every start until cleared.
    """

    def __init__(self, cfg: LabConfig, get: Get, *, clock=time.monotonic,
                 sleep=time.sleep, now=lambda: dt.datetime.now(UTC),
                 rng: random.Random | None = None):
        self.a = avl_config(cfg)
        self.arc = self.a["archive"]
        self.dir: Path = self.a["dir"] / "archive"
        self.box = clip_box(cfg)
        self.get = get
        self.clock, self.sleep, self.now = clock, sleep, now
        self.rng = rng or random.Random()
        self.gov = Governor(self.arc["policy"], clock)
        self.stop = False           # set by a signal handler
        self.backoff_total = 0.0
        self.n_requests = 0
        self.n_failed = 0

    # paths
    manifest = property(lambda s: s.dir / "manifest.jsonl")
    requests_log = property(lambda s: s.dir / "requests.jsonl")
    halted_file = property(lambda s: s.dir / "HALTED")
    pause_file = property(lambda s: s.dir / "PAUSE")

    def _log(self, **kw) -> None:
        _append_jsonl(self.requests_log, {"t": self.now().isoformat(), **kw})

    def _pausing(self) -> bool:
        return self.stop or self.pause_file.exists()

    def _nap(self, seconds: float) -> None:
        """Sleep in short steps so a pause or signal is honoured promptly."""
        end = self.clock() + seconds
        while not self._pausing():
            left = end - self.clock()
            if left <= 0:
                return
            self.sleep(min(1.0, left))

    def _halt(self, reason: str, progress: str) -> None:
        stamp = self.now()
        self.halted_file.write_text(json.dumps(
            {"halted_at": stamp.isoformat(), "reason": reason, "progress": progress}) + "\n")
        self._log(event="HALT", reason=reason)
        row = (f"| {stamp:%Y-%m-%d} | A6 archive | **HALTED** (automatic stop record, "
               f"{stamp:%H:%M} UTC): {reason}. {progress}. Not restarted; resume only on "
               f"Robbie's decision (`lab supply avl archive --clear-halt`) |\n")
        log = self.a["progress_log"]
        text = log.read_text()
        log.write_text(text if text.endswith("\n") else text + "\n")
        with log.open("a") as f:
            f.write(row)
        raise Halted(reason)

    def _request(self, url: str, kind: str) -> Http | None:
        """One governed request, retried on 429/5xx/timeouts. None if paused."""
        attempt = 0
        while True:
            self._nap(self.gov.wait_s())
            if self._pausing():
                return None
            self.gov.started()
            t_start = self.now().isoformat()     # the rate floor is between starts
            err = None
            try:
                r = self.get(url)
                failed = r.status == 429 or r.status >= 500
            except TransientError as e:
                r, failed, err = None, True, str(e)
            self.n_requests += 1
            self.n_failed += failed
            events = self.gov.record(failed, r.ttfb if r is not None and not failed else None)
            self._log(kind=kind, t_start=t_start, path=url.removeprefix(self.arc["base_url"]),
                      http=r.status if r else None, ttfb=r.ttfb if r else None,
                      bytes=len(r.body) if r else None, error=err, attempt=attempt)
            for e in events:
                self._log(event="POLICY", message=e)
            if self.gov.halt:
                self._halt(self.gov.halt, self._progress())
            if not failed:
                return r
            ra = retry_after_s(r.headers, self.now()) if r is not None else None
            d = self.gov.backoff_s(attempt, ra, self.rng)
            attempt += 1
            self.backoff_total += d
            self._log(event="BACKOFF", seconds=round(d, 1), retry_after=ra, attempt=attempt)
            self._nap(d)

    def _progress(self) -> str:
        m = _read_jsonl(self.manifest)
        return (f"{sum(x['status'] == 'done' for x in m)} snapshots done, "
                f"{sum(x['status'] == 'gap' for x in m)} gaps")

    def _index(self, day: dt.date) -> list[str] | None:
        p = self.dir / "index" / f"{day.isoformat()}.json"
        if p.is_file():
            return json.loads(p.read_text())["in_window"]
        url = f"{self.arc['base_url']}{day:%Y/%m/%d}/"
        r = self._request(url, "listing")
        if r is None:
            return None
        start, end = window_utc(day, self.arc["hours"], self.a["timezone"])
        listed = sorted(set(SNAPSHOT_RE.findall(r.body.decode("utf-8", "replace"))))
        listed = [s for s in listed if s.startswith(f"{day:%Y%m%d}T")]
        stems = []
        for s in listed:
            t = dt.datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            if start <= t < end:
                stems.append(f"sirivm-{s}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"day": day.isoformat(), "http": r.status,
                                 "listed": len(listed), "window_utc": [start.isoformat(),
                                                                       end.isoformat()],
                                 "in_window": stems}, indent=1) + "\n")
        return stems

    def _snapshot(self, day: dt.date, stem: str, ded: Deduper) -> bool:
        url = f"{self.arc['base_url']}{day:%Y/%m/%d}/{stem}.zip"
        r = self._request(url, "snapshot")
        if r is None:
            return False
        base = {"day": day.isoformat(), "stem": stem, "http": r.status,
                "fetched_at": self.now().isoformat()}
        if r.status != 200:                      # other 4xx: a gap, not retried
            _append_jsonl(self.manifest, {**base, "status": "gap"})
            return True
        sha = hashlib.sha256(r.body).hexdigest()
        try:
            with zipfile.ZipFile(io.BytesIO(r.body)) as z:
                xml = [n for n in z.namelist() if n.endswith(".xml")]
                if len(xml) != 1:
                    raise AvlError(f"expected one .xml in {stem}.zip, found {xml}")
                with z.open(xml[0]) as f:
                    snap = parse_siri(f, self.box)
        except (zipfile.BadZipFile, ET.ParseError, AvlError) as e:
            _append_jsonl(self.manifest, {**base, "status": "gap", "sha256": sha,
                                          "error": f"{type(e).__name__}: {e}"})
            return True
        kept, stats = ded.apply(snap, source="archive", snapshot=stem)
        if kept:
            part = self.dir / "parts" / day.isoformat() / f"{stem}.parquet"
            part.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(to_table(kept), part)
        # The national file only ever existed in memory; it goes out of scope here.
        _append_jsonl(self.manifest, {**base, "status": "done", "sha256": sha,
                                      "bytes": len(r.body), "ttfb": r.ttfb,
                                      "response_ts": snap.response_ts.isoformat(), **stats})
        return True

    def _seed(self, day: dt.date, done: list[dict]) -> Deduper:
        ded = Deduper(nominal_s=30)
        mine = [m for m in done if m["day"] == day.isoformat() and m["status"] == "done"]
        prev = _ts(mine[-1]["response_ts"]) if mine else None
        ded.seed(_read_parts(self.dir / "parts" / day.isoformat()), prev)
        return ded

    def run(self, clear_halt: bool = False) -> str:
        """Fetch until done ('ok') or paused ('paused'). Raises Halted."""
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.halted_file.exists():
            if not clear_halt:
                raise Halted(f"halt on file, not cleared: {self.halted_file.read_text().strip()}")
            self._log(event="RESUME", message="halt cleared by operator",
                      halt=self.halted_file.read_text().strip())
            self.halted_file.unlink()
        self._log(event="START", days=[d.isoformat() for d in self.arc["days"]],
                  interval_s=self.gov.interval)
        closed = {d["day"] for d in _read_jsonl(self.dir / "days.jsonl")}
        manifest = _read_jsonl(self.manifest)
        seen = {(m["day"], m["stem"]) for m in manifest}
        for day in self.arc["days"]:
            if day.isoformat() in closed:
                continue
            stems = self._index(day)
            if stems is None:
                return self._paused()
            ded = self._seed(day, manifest)
            for stem in stems:
                if (day.isoformat(), stem) in seen:
                    continue
                if not self._snapshot(day, stem, ded):
                    return self._paused()
            m = [x for x in _read_jsonl(self.manifest) if x["day"] == day.isoformat()]
            rec = close_day(self.dir, day, "sirivm", {
                "n_listed": len(stems), "n_done": sum(x["status"] == "done" for x in m),
                "n_gap": sum(x["status"] == "gap" for x in m)})
            self._log(event="DAY_CLOSED", **rec)
        self._log(event="COMPLETE", **self.counters())
        return "ok"

    def _paused(self) -> str:
        self._log(event="PAUSED", **self.counters())
        return "paused"

    def counters(self) -> dict:
        return {"requests": self.n_requests, "failed": self.n_failed,
                "backoff_s": round(self.backoff_total, 1), "interval_s": self.gov.interval}


# ---------------------------------------------------------------------------- live

class LiveCollect:
    """Poll the BODS API over the clip box through today's window, if today is listed."""

    def __init__(self, cfg: LabConfig, get: Get, api_key: str, *,
                 sleep=time.sleep, now=lambda: dt.datetime.now(UTC)):
        self.a = avl_config(cfg)
        self.live = self.a["live"]
        self.dir: Path = self.a["dir"] / "live"
        self.box = clip_box(cfg)
        self.get, self.key = get, api_key
        self.sleep, self.now = sleep, now
        self.stop = False

    def _log(self, **kw) -> None:
        _append_jsonl(self.dir / "polls.jsonl", {"t": self.now().isoformat(), **kw})

    def _wait_until(self, t: dt.datetime) -> None:
        while not self.stop and (left := (t - self.now()).total_seconds()) > 0:
            self.sleep(min(1.0, left))

    def run(self) -> str:
        z = ZoneInfo(self.a["timezone"])
        today = self.now().astimezone(z).date()
        if today not in self.live["days"]:
            return f"not a collection day ({today})"
        start, end = window_utc(today, self.live["hours"], self.a["timezone"])
        if self.now() >= end:
            return f"window over for {today}"
        if any(d["day"] == today.isoformat() for d in _read_jsonl(self.dir / "days.jsonl")):
            return f"{today} already closed"
        self._log(event="START", day=today.isoformat(), window_utc=[str(start), str(end)])
        self._wait_until(start)
        parts = self.dir / "parts" / today.isoformat()
        ded = Deduper(nominal_s=self.live["poll_s"])
        ded.seed(_read_parts(parts), None)      # resume within a window after a restart
        buf: list[dict] = []
        polls, fails = 0, 0
        box = ",".join(f"{v:.5f}" for v in self.box)
        while not self.stop and self.now() < end:
            t = self.now()
            try:
                r = self.get(self.live["endpoint"],
                             params={"api_key": self.key, "boundingBox": box})
            except TransientError as e:
                r, err = None, str(e)
            else:
                err = None
            if r is not None and r.status in (401, 403):
                self._log(event="FATAL", http=r.status)
                raise AvlError(f"BODS rejected the API key (HTTP {r.status})")
            if r is not None and r.status == 200:
                fails = 0
                try:
                    snap = parse_siri(io.BytesIO(r.body), self.box)
                except (ET.ParseError, AvlError) as e:
                    self._log(http=200, error=f"{type(e).__name__}", latency=r.ttfb)
                else:
                    kept, stats = ded.apply(snap, source="live",
                                            snapshot=t.strftime("%Y%m%dT%H%M%S"))
                    buf += kept
                    self._log(http=200, latency=r.ttfb, bytes=len(r.body), **stats)
            else:
                fails += 1
                self._log(http=r.status if r else None, error=err)
            polls += 1
            if buf and polls % self.live["flush_every_polls"] == 0:
                self._flush(parts, buf)
                buf = []
            nxt = t + dt.timedelta(seconds=self.live["poll_s"])
            if fails:
                nxt = t + dt.timedelta(seconds=min(300, self.live["poll_s"] * 2 ** fails))
            self._wait_until(min(nxt, end))
        if buf:
            self._flush(parts, buf)
        if self.stop:
            self._log(event="STOPPED", polls=polls)
            return "stopped"
        rec = close_day(self.dir, today, "sirivm_live", {"polls": polls})
        self._log(event="DAY_CLOSED", **rec)
        return "ok"

    def _flush(self, parts: Path, buf: list[dict]) -> None:
        parts.mkdir(parents=True, exist_ok=True)
        pq.write_table(to_table(buf), parts / f"part-{self.now():%H%M%S%f}.parquet")


# ---------------------------------------------------------------------------- status

def archive_status(cfg: LabConfig) -> dict:
    a = avl_config(cfg)
    d = a["dir"] / "archive"
    m = _read_jsonl(d / "manifest.jsonl")
    reqs = _read_jsonl(d / "requests.jsonl")
    closed = {x["day"]: x for x in _read_jsonl(d / "days.jsonl")}
    days = []
    for day in a["archive"]["days"]:
        k = day.isoformat()
        idx = d / "index" / f"{k}.json"
        listed = len(json.loads(idx.read_text())["in_window"]) if idx.is_file() else None
        mm = [x for x in m if x["day"] == k]
        days.append({"day": k, "listed": listed,
                     "done": sum(x["status"] == "done" for x in mm),
                     "gap": sum(x["status"] == "gap" for x in mm),
                     "rows": sum(x.get("n_kept", 0) for x in mm),
                     "closed": k in closed,
                     "sha256": closed.get(k, {}).get("sha256")})
    http = [r for r in reqs if "kind" in r]
    return {
        "state": ("halted" if (d / "HALTED").exists() else
                  "complete" if len(closed) == len(a["archive"]["days"]) else
                  "paused" if (d / "PAUSE").exists() else "incomplete"),
        "halt": (d / "HALTED").read_text().strip() if (d / "HALTED").exists() else None,
        "days": days,
        "requests": len(http),
        "failed": sum(1 for r in http if r["error"] or (r["http"] or 0) in range(500, 600)
                      or r["http"] == 429),
        "backoff_s": round(sum(r.get("seconds", 0) for r in reqs
                               if r.get("event") == "BACKOFF"), 1),
        "policy_events": [r["message"] for r in reqs if r.get("event") == "POLICY"],
    }


def live_status(cfg: LabConfig) -> dict:
    a = avl_config(cfg)
    d = a["dir"] / "live"
    polls = _read_jsonl(d / "polls.jsonl")
    closed = {x["day"]: x for x in _read_jsonl(d / "days.jsonl")}
    out = []
    for day in a["live"]["days"]:
        k = day.isoformat()
        start, end = window_utc(day, a["live"]["hours"], a["timezone"])
        ok = sorted(_ts(p["t"]) for p in polls
                    if p.get("http") == 200 and p["t"][:10] == k)
        gap_limit = 3 * a["live"]["poll_s"]
        edges = [start, *ok, end] if ok else [start, end]
        gaps = [(x, y) for x, y in zip(edges, edges[1:])
                if (y - x).total_seconds() > gap_limit]
        out.append({"day": k, "ok_polls": len(ok),
                    "failed_polls": sum(1 for p in polls if p["t"][:10] == k
                                        and "event" not in p and p.get("http") != 200),
                    "rows": sum(p.get("n_kept", 0) for p in polls if p["t"][:10] == k),
                    "gap_minutes": round(sum((y - x).total_seconds() for x, y in gaps) / 60, 1),
                    "gaps": [(x.isoformat(), y.isoformat()) for x, y in gaps][:20],
                    "closed": k in closed, "sha256": closed.get(k, {}).get("sha256")})
    return {"days": out}


def user_agent(cfg: LabConfig, contact: str) -> str:
    a = avl_config(cfg)
    return a["user_agent"].format(version=__version__, contact=contact)
