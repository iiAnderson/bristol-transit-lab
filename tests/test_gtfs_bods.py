import csv
import datetime as dt
import io
import zipfile

import duckdb
import pytest

from lab.supply import gtfs_bods as g

EXTENT = (-3.0, 51.0, -2.0, 52.0)
BOX = (-3.1, 50.9, -1.9, 52.1)
DAY = dt.date(2026, 9, 23)          # a Wednesday

STOPS = [("IN1", 51.5, -2.5), ("IN2", 51.6, -2.4), ("BUF", 52.05, -2.5), ("OUT", 53.0, -2.5)]


def gtfs(tmp_path, name, trips, calendar=None, calendar_dates=()):
    """trips: (trip_id, route_id, service_id, [(stop, time), ...])."""
    files = {
        "agency.txt": [["agency_id", "agency_name", "agency_url", "agency_timezone",
                        "agency_noc"], ["A1", "Op", "https://x.test", "Europe/London", "OPX"]],
        "stops.txt": [["stop_id", "stop_name", "stop_lat", "stop_lon"]]
                     + [[s, s, la, lo] for s, la, lo in STOPS],
        "routes.txt": [["route_id", "agency_id", "route_short_name", "route_long_name",
                        "route_type"], ["R1", "A1", "1", "", 3], ["R2", "A1", "2", "", 3]],
        "calendar.txt": [["service_id", "monday", "tuesday", "wednesday", "thursday",
                          "friday", "saturday", "sunday", "start_date", "end_date"]]
                        + (calendar or [["WK", 1, 1, 1, 1, 1, 0, 0, "20260901", "20270101"],
                                        ["SAT", 0, 0, 0, 0, 0, 1, 0, "20260901", "20270101"]]),
        "calendar_dates.txt": [["service_id", "date", "exception_type"], *calendar_dates],
        "trips.txt": [["route_id", "service_id", "trip_id", "trip_headsign", "direction_id"]]
                     + [[r, sv, t, "", 0] for t, r, sv, _ in trips],
        "stop_times.txt": [["trip_id", "arrival_time", "departure_time", "stop_id",
                            "stop_sequence", "pickup_type", "drop_off_type"]]
                          + [[t, tm, tm, s, i + 1, 0, 0]
                             for t, _, _, calls in trips for i, (s, tm) in enumerate(calls)],
    }
    p = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(p, "w") as z:
        for fn, rows in files.items():
            b = io.StringIO()
            csv.writer(b).writerows(rows)
            z.writestr(fn, b.getvalue())
    return p


CALLS = [("IN1", "08:00:00"), ("IN2", "08:10:00"), ("BUF", "08:20:00"), ("OUT", "09:00:00")]


def run(tmp_path, feeds, zones=None):
    con = duckdb.connect()
    g.load(con, {n: gtfs(tmp_path, n, **kw) for n, kw in feeds.items()})
    return con, g.build(con, list(feeds), DAY, EXTENT, BOX, zones)


def test_calendar_selects_the_date_and_honours_exceptions(tmp_path):
    con, r = run(tmp_path, {"a": dict(
        trips=[("t1", "R1", "WK", CALLS), ("t2", "R1", "SAT", CALLS),
               ("t3", "R1", "EXTRA", CALLS), ("t4", "R2", "WK2", CALLS)],
        calendar=[["WK", 1, 1, 1, 1, 1, 0, 0, "20260901", "20270101"],
                  ["SAT", 0, 0, 0, 0, 0, 1, 0, "20260901", "20270101"],
                  ["WK2", 1, 1, 1, 1, 1, 0, 0, "20260901", "20270101"]],
        calendar_dates=[["EXTRA", "20260923", 1], ["WK2", "20260923", 2]])})
    kept = {x[0] for x in con.execute("SELECT src_trip_id FROM trips_on_date").fetchall()}
    assert kept == {"t1", "t3"}


def test_clip_keeps_whole_trips_then_cuts_beyond_the_box(tmp_path):
    outside_only = [("OUT", "10:00:00"), ("BUF", "10:30:00")]
    con, r = run(tmp_path, {"a": dict(trips=[("t1", "R1", "WK", CALLS),
                                             ("t2", "R1", "WK", outside_only)])})
    assert r["trips_calling_in_extent"] == 1
    stops = [x[0] for x in con.execute("SELECT stop_id FROM st_out ORDER BY seq").fetchall()]
    assert stops == ["IN1", "IN2", "BUF"] and r["calls_cut_outside_box"] == 1


def test_duplicates_removed_within_and_across_feeds_deterministically(tmp_path):
    later = [(s, t.replace("08:", "09:")) for s, t in CALLS]
    feeds = {"a": dict(trips=[("t1", "R1", "WK", CALLS), ("t1dup", "R1", "WK", CALLS),
                              ("t2", "R1", "WK", later)]),
             "b": dict(trips=[("x1", "R1", "WK", CALLS)])}
    con, r = run(tmp_path, feeds)
    assert r["duplicates_removed"] == 2 and r["trips_out"] == 2
    kept = sorted(x[0] for x in con.execute("SELECT trip_id FROM trip_keep").fetchall())
    assert kept == ["a:t1", "a:t2"]
    assert r["flagged_routes"] == [("OPX", "1", 4, 2)]


def test_same_start_variants_are_reported_not_removed(tmp_path):
    short = CALLS[:2]
    con, r = run(tmp_path, {"a": dict(trips=[("t1", "R1", "WK", CALLS),
                                             ("t2", "R1", "WK", short)])})
    assert r["trips_out"] == 2 and len(r["same_start_groups"]) == 1


def test_no_trips_on_the_date_fails(tmp_path):
    with pytest.raises(g.BusGtfsError, match="do not cover"):
        run(tmp_path, {"a": dict(trips=[("t1", "R1", "SAT", CALLS)])})


def test_written_feed_is_single_date(tmp_path):
    con, r = run(tmp_path, {"a": dict(trips=[("t1", "R1", "WK", CALLS)])})
    out = g.write(con, ["a"], DAY, tmp_path / "bus.zip", "v")
    with zipfile.ZipFile(out) as z:
        assert z.read("calendar_dates.txt").decode().splitlines()[1] == "D20260923,20260923,1"
        st = list(csv.DictReader(io.StringIO(z.read("stop_times.txt").decode())))
    assert [x["stop_sequence"] for x in st] == ["1", "2", "3"]


def test_trips_calling_in_a_zone_beyond_the_extent_are_kept(tmp_path):
    import json
    zones = tmp_path / "zones.geojson"
    # a zone polygon just beyond the extent's north edge, covering stop BUF
    zones.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"LSOA21CD": "Z1"},
        "geometry": {"type": "Polygon", "coordinates": [[[-2.6, 52.01], [-2.4, 52.01],
                                                          [-2.4, 52.09], [-2.6, 52.09],
                                                          [-2.6, 52.01]]]}}]}))
    zone_only = [("BUF", "10:00:00"), ("OUT", "10:30:00"), ("BUF", "11:00:00")]
    _, r0 = run(tmp_path, {"a": dict(trips=[("t1", "R1", "WK", zone_only)])})
    assert r0["trips_calling_in_extent"] == 0
    _, r1 = run(tmp_path, {"a": dict(trips=[("t1", "R1", "WK", zone_only)])}, zones)
    assert r1["trips_calling_in_extent"] == 1


def test_excluding_a_trip_drops_its_exact_duplicates_too(tmp_path):
    con = duckdb.connect()
    feeds = {"a": dict(trips=[("t1", "R1", "WK", CALLS), ("t1dup", "R1", "WK", CALLS),
                              ("t2", "R1", "WK", [(s, t.replace("08:", "09:")) for s, t in CALLS])])}
    g.load(con, {n: gtfs(tmp_path, n, **kw) for n, kw in feeds.items()})
    r = g.build(con, ["a"], DAY, EXTENT, BOX, None, exclude_trips=["a:t1"])
    assert r["excluded_superseded_variants"] == 2 and r["trips_out"] == 1
