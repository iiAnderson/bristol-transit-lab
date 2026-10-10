import csv
import datetime as dt
import gzip
import io
import zipfile

import pytest

from lab.supply import gtfs_rail as g

BOX = (-3.0, 51.0, -2.0, 52.0)
NS = "http://www.thalesgroup.com/rtti/XmlTimetable/v8"

# A: inside, B: inside, C: outside, J: a junction (no CRS), D: inside but no NaPTAN point
NAPTAN = {"AAA": (-2.5, 51.5, "A", "active"), "BBB": (-2.4, 51.4, "B", "active"),
          "CCC": (-1.0, 51.5, "C", "active"), "EEE": (-2.6, 51.6, "E", "active")}
REF = g.Ref("T1", {"AAA": "AAX", "BBB": "BBX", "CCC": "CCX", "DDD": "DDX", "EEE": "EEX"},
            {"AAA": "Alpha", "BBB": "Beta", "CCC": "Gamma", "DDD": "Delta", "EEE": "Eps"},
            {"XX": ("Test Trains", "https://example.test")})


def journey(rid, calls, ssd="2026-09-23", **attrs):
    a = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return (f'<Journey rid="{rid}" uid="U" trainId="1A00" ssd="{ssd}" toc="XX" {a}>'
            + "".join(calls) + "</Journey>")


def feed(tmp_path, *journeys):
    p = tmp_path / "tt_v8.xml.gz"
    p.write_bytes(gzip.compress(
        f'<PportTimetable timetableID="T1" xmlns="{NS}">{"".join(journeys)}'
        "</PportTimetable>".encode()))
    return p


OR = '<OR tpl="{}" act="TB" ptd="{}" wtd="{}"/>'
IP = '<IP tpl="{}" act="T " pta="{}" ptd="{}" wta="{}" wtd="{}"/>'
DT = '<DT tpl="{}" act="TF" pta="{}" wta="{}"/>'
PP = '<PP tpl="{}" wtp="{}"/>'


def std(rid="r1", **kw):
    return journey(rid, [OR.format("AAA", "23:50", "23:50"), PP.format("ZZZ", "23:55"),
                         IP.format("EEE", "23:58", "00:02", "23:58", "00:02"),
                         DT.format("BBB", "00:10", "00:10")], **kw)


def run(tmp_path, *js, day=dt.date(2026, 9, 23)):
    return g.convert(feed(tmp_path, *js), REF, NAPTAN, day, BOX)


def test_public_calls_only_and_times_past_midnight(tmp_path):
    r = run(tmp_path, std())
    assert r.counts["call_dropped_PP"] == 1
    [t] = r.trips
    assert [(k.tpl, k.arr, k.dep) for k in t.calls] == [
        ("AAA", None, 1430), ("EEE", 1438, 1442), ("BBB", 1450, None)]
    out = g.write_gtfs(r, REF, tmp_path / "rail.zip", "Europe/London")
    with zipfile.ZipFile(out) as z:
        st = list(csv.DictReader(io.StringIO(z.read("stop_times.txt").decode())))
        cal = z.read("calendar_dates.txt").decode()
    assert [s["departure_time"] for s in st] == ["23:50:00", "24:02:00", "24:10:00"]
    assert [(s["pickup_type"], s["drop_off_type"]) for s in st] == [
        ("0", "1"), ("0", "0"), ("1", "0")]
    assert [s["stop_id"] for s in st] == ["AAX", "EEX", "BBX"]
    assert "20260923" in cal


@pytest.mark.parametrize("attrs,reason", [
    ({"isPassengerSvc": "false"}, "excluded_not_passenger"),
    ({"can": "true"}, "excluded_cancelled_journey"),
    ({"trainCat": "BR"}, "excluded_road_BR"),
    ({"trainCat": "BS"}, "excluded_road_BS"),
    ({"status": "5"}, "excluded_road_status5"),
    ({"trainCat": "SS"}, "excluded_water"),
    ({"qtrain": "true"}, "excluded_run_as_required"),
    ({"isCharter": "true"}, "excluded_charter"),
    ({"deleted": "true"}, "excluded_deleted"),
])
def test_exclusions_are_counted(tmp_path, attrs, reason):
    r = run(tmp_path, std(), std("r2", **attrs))
    assert r.counts[reason] == 1 and len(r.trips) == 1


def test_cancelled_calls_and_other_dates(tmp_path):
    j = journey("r3", [OR.format("AAA", "08:00", "08:00"),
                       '<IP tpl="EEE" act="T " pta="08:05" ptd="08:06" wta="0" wtd="0" '
                       'can="true"/>', DT.format("BBB", "08:10", "08:10")])
    r = run(tmp_path, j, std("r4", ssd="2026-09-24"))
    assert r.counts["call_dropped_cancelled"] == 1
    assert r.counts["journeys_other_dates"] == 1
    assert [k.tpl for k in r.trips[0].calls] == ["AAA", "BBB"]


def test_clip_cuts_calls_outside_and_drops_single_call_trips(tmp_path):
    out_in = journey("r5", [OR.format("CCC", "08:00", "08:00"), DT.format("AAA", "09:00", "09")])
    through = journey("r6", [OR.format("CCC", "08:00", "08:00"),
                             IP.format("AAA", "09:00", "09:01", "0", "0"),
                             DT.format("BBB", "09:10", "09:10")])
    r = run(tmp_path, out_in, through)
    assert r.counts["excluded_one_call_in_box"] == 1
    assert [k.tpl for k in r.trips[0].calls] == ["AAA", "BBB"]
    assert r.counts["calls_cut_outside_box"] == 2


def test_junction_with_public_times_is_dropped(tmp_path):
    j = journey("r7", [OR.format("AAA", "08:00", "08:00"),
                       IP.format("JJJ", "08:03", "08:04", "0", "0"),
                       DT.format("BBB", "08:10", "08:10")])
    r = run(tmp_path, j)
    assert r.counts["call_dropped_no_crs"] == 1 and len(r.trips[0].calls) == 2


def test_station_without_coordinate_next_to_the_box_fails(tmp_path):
    j = journey("r8", [OR.format("AAA", "08:00", "08:00"),
                       IP.format("DDD", "08:03", "08:04", "0", "0"),
                       DT.format("BBB", "08:10", "08:10")])
    with pytest.raises(g.RailGtfsError, match="DDD"):
        run(tmp_path, j)


def test_snapshot_not_covering_the_date_fails(tmp_path):
    with pytest.raises(g.RailGtfsError, match="does not cover"):
        run(tmp_path, std(ssd="2026-09-24"))


def test_presence_check(tmp_path):
    r = run(tmp_path, std())
    p = g.presence(r, NAPTAN, BOX, not_open=["EEE"])
    assert p["served_but_not_open"] == ["EEE"] and p["unserved_expected"] == []
    r2 = run(tmp_path, journey("r9", [OR.format("AAA", "08:00", "08:00"),
                                      DT.format("BBB", "08:10", "08:10")]))
    assert g.presence(r2, NAPTAN, BOX, not_open=[])["unserved_expected"] == ["EEE"]


def test_feed_registry_round_trip_and_calendar_check(tmp_cfg, tmp_path):
    from dataclasses import replace
    from lab.supply import feeds
    cfg = replace(tmp_cfg, lab_db=tmp_path / "lab.duckdb")
    f = tmp_path / "x.zip"
    f.write_bytes(b"abc")
    now = dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc)
    feeds.register(cfg, feed_id="x", kind="rail_gtfs", source_url="u", path=f,
                   downloaded_at=now, licence="OGL v3", valid_from=dt.date(2026, 9, 23),
                   valid_to=dt.date(2026, 9, 23))
    row = feeds.get(cfg, "x")
    feeds.require_covers(row, dt.date(2026, 9, 23))
    with pytest.raises(feeds.FeedError, match="does not cover"):
        feeds.require_covers(row, dt.date(2026, 9, 24))
    f.write_bytes(b"abcd")
    with pytest.raises(feeds.FeedError, match="changed"):
        feeds.check_file_unchanged(row)


def test_timing_points_keep_passing_points_and_unfiltered_journeys(tmp_path):
    empty = std("r2", isPassengerSvc="false")
    bus = std("r3", trainCat="BS")
    away = journey("r4", [OR.format("CCC", "10:00", "10:00"), DT.format("CCC", "10:30", "10:30")])
    f = feed(tmp_path, std(), empty, bus, away, std("r5", ssd="2026-09-24"))
    rows = g.timing_points(f, REF, NAPTAN, dt.date(2026, 9, 23), BOX)
    assert {r["rid"] for r in rows} == {"r1", "r2", "r3"}            # r4 has no station in the box
    r1 = [r for r in rows if r["rid"] == "r1"]
    assert [(r["tpl"], r["kind"]) for r in r1] == [("AAA", "OR"), ("ZZZ", "PP"), ("EEE", "IP"), ("BBB", "DT")]
    pp = r1[1]
    assert pp["arr_s"] == pp["dep_s"] == 23 * 3600 + 55 * 60 and not pp["public"]
    assert r1[2]["dep_s"] == 86400 + 120 and r1[3]["arr_s"] == 86400 + 600     # rolled past midnight
    assert {r["exclusion"] for r in rows if r["rid"] == "r2"} == {"not_passenger"}
    secs = g.sections_from_points(rows, REF, NAPTAN, BOX)
    got = {(s["from"], s["to"]): s["trains"] for s in secs}
    # the empty-stock journey counts; the bus does not use track
    assert got == {("AAA", "ZZZ"): 2, ("ZZZ", "EEE"): 2, ("EEE", "BBB"): 2}
    z = next(s for s in secs if s["to"] == "ZZZ")
    assert z["from_station"] and not z["to_station"] and z["timed"]
