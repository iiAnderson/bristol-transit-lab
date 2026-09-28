"""
BODS bus GTFS for one service date (plans/P2.md A4).

Input: one or more regional BODS GTFS zips (the archived snapshot for the modelled
date). Output: a single-date GTFS zip holding

* trips active on the date (``calendar`` weekday + range, then ``calendar_dates``);
* whole trips that call at least once inside the extent **or inside an internal zone**
  (internal LSOAs follow the centroid rule, so 45 of their polygons reach beyond the
  extent), with calls beyond the clip box cut (and a trip left with fewer than two
  calls dropped);
* no duplicates: trips with the same operator, route name and (stop, time) sequence are
  kept once, across and within feeds (BODS carries superseded registrations). Trips per
  route before and after are reported; a route that loses more than 10% is flagged.

Shapes are not carried (routing does not use them). Nothing here names a place.
"""
from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

import duckdb

TABLES = ["agency", "stops", "routes", "calendar", "calendar_dates", "trips", "stop_times"]


class BusGtfsError(RuntimeError):
    pass


def load(con: duckdb.DuckDBPyConnection, feeds: dict[str, Path]) -> None:
    """Read each feed's tables (all text) into ``<feed>_<table>``."""
    for name, z in feeds.items():
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
            for t in TABLES:
                if f"{t}.txt" not in names:
                    if t in ("calendar", "calendar_dates"):
                        con.execute(f"CREATE OR REPLACE TABLE {name}_{t} AS SELECT "
                                    + ("NULL::VARCHAR service_id, NULL::VARCHAR date, "
                                       "NULL::VARCHAR exception_type"
                                       if t == "calendar_dates" else
                                       "NULL::VARCHAR service_id") + " WHERE false")
                        continue
                    raise BusGtfsError(f"{z.name} has no {t}.txt")
                tmp = z.parent / f".{name}_{t}.txt"
                tmp.write_bytes(zf.read(f"{t}.txt"))
                try:
                    con.execute(f"CREATE OR REPLACE TABLE {name}_{t} AS SELECT * FROM "
                                f"read_csv('{tmp}', all_varchar=true, header=true)")
                finally:
                    tmp.unlink()


def build(con: duckdb.DuckDBPyConnection, feeds: list[str], day: dt.date,
          extent: tuple, box: tuple, zones_geojson: Path | None = None,
          exclude_trips: list[str] | None = None) -> dict:
    d = f"{day:%Y%m%d}"
    wd = day.strftime("%A").lower()
    parts = []
    for f in feeds:
        cal_cols = {r[0] for r in con.execute(f"DESCRIBE {f}_calendar").fetchall()}
        base = (f"SELECT service_id FROM {f}_calendar WHERE {wd}='1' AND start_date<='{d}' "
                f"AND end_date>='{d}'" if wd in cal_cols else
                f"SELECT service_id FROM {f}_calendar WHERE false")
        con.execute(f"""CREATE OR REPLACE TEMP TABLE {f}_active AS
            ({base} EXCEPT SELECT service_id FROM {f}_calendar_dates
                    WHERE date='{d}' AND exception_type='2')
            UNION SELECT service_id FROM {f}_calendar_dates
                  WHERE date='{d}' AND exception_type='1'""")
        parts.append(f"""
            SELECT '{f}' feed, '{f}:' || t.trip_id trip_id, '{f}:' || t.route_id route_id,
                   a.agency_noc noc, r.route_short_name, t.trip_id src_trip_id,
                   t.route_id src_route_id
            FROM {f}_trips t JOIN {f}_active USING (service_id)
            JOIN {f}_routes r USING (route_id) JOIN {f}_agency a USING (agency_id)""")
    con.execute("CREATE OR REPLACE TEMP TABLE trips_on_date AS " + " UNION ALL ".join(parts))

    n_on_date = con.execute("SELECT count(*) FROM trips_on_date").fetchone()[0]
    if n_on_date == 0:
        raise BusGtfsError(f"no bus trips are active on {day}: the feeds do not cover it")

    st = " UNION ALL ".join(f"""
        SELECT '{f}:' || st.trip_id trip_id, st.stop_id, st.stop_sequence::INT seq,
               st.arrival_time, st.departure_time,
               coalesce(nullif(st.pickup_type, ''), '0') pickup_type,
               coalesce(nullif(st.drop_off_type, ''), '0') drop_off_type,
               s.stop_lon::DOUBLE lon, s.stop_lat::DOUBLE lat, s.stop_name
        FROM {f}_stop_times st JOIN {f}_stops s USING (stop_id)
        WHERE '{f}:' || st.trip_id IN (SELECT trip_id FROM trips_on_date)""" for f in feeds)
    con.execute(f"CREATE OR REPLACE TEMP TABLE st_on_date AS {st}")
    x0, y0, x1, y1 = extent
    b0, c0, b1, c1 = box
    in_zone = "false"
    if zones_geojson is not None:
        con.execute("INSTALL spatial; LOAD spatial")
        con.execute(f"""CREATE OR REPLACE TEMP TABLE zone_stops AS
            SELECT DISTINCT s.stop_id FROM (SELECT DISTINCT stop_id, lon, lat FROM st_on_date) s
            JOIN ST_Read('{zones_geojson}') z ON ST_Contains(z.geom, ST_Point(s.lon, s.lat))""")
        in_zone = "stop_id IN (SELECT stop_id FROM zone_stops)"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE st_on_date2 AS SELECT *,
        (lon BETWEEN {x0} AND {x1} AND lat BETWEEN {y0} AND {y1}) OR {in_zone} in_extent,
        lon BETWEEN {b0} AND {b1} AND lat BETWEEN {c0} AND {c1} in_box FROM st_on_date""")
    con.execute("""CREATE OR REPLACE TEMP TABLE trips_extent AS
        SELECT t.* FROM trips_on_date t WHERE trip_id IN
        (SELECT trip_id FROM st_on_date2 WHERE in_extent)""")
    # A trip's identity for de-duplication: operator, route name and its full sequence
    # of (stop, departure time) — computed before clipping.
    con.execute("""CREATE OR REPLACE TEMP TABLE trip_key AS
        SELECT t.trip_id, t.feed, t.noc, t.route_short_name,
               md5(t.noc || '|' || coalesce(t.route_short_name, '') || '|' ||
                   string_agg(s.stop_id || '@' || coalesce(s.departure_time, ''), ','
                              ORDER BY s.seq)) k
        FROM trips_extent t JOIN st_on_date2 s USING (trip_id)
        GROUP BY t.trip_id, t.feed, t.noc, t.route_short_name""")
    # Trips resolved as superseded variants (against vehicle destinations on the day;
    # listed in config with the run that resolved them) are dropped by *pattern*, so an
    # exact-duplicate copy under another trip id goes too.
    n_excluded = 0
    if exclude_trips:
        con.execute("""CREATE OR REPLACE TEMP TABLE excl_k AS SELECT DISTINCT k FROM trip_key
                       WHERE trip_id IN (SELECT unnest(?))""", [exclude_trips])
        n_excluded = con.execute("SELECT count(*) FROM trip_key WHERE k IN "
                                 "(SELECT k FROM excl_k)").fetchone()[0]
        con.execute("DELETE FROM trip_key WHERE k IN (SELECT k FROM excl_k)")
    # Keep the first by feed order, then trip id: deterministic.
    order = " ".join(f"WHEN '{f}' THEN {i}" for i, f in enumerate(feeds))
    con.execute(f"""CREATE OR REPLACE TEMP TABLE trip_keep AS
        SELECT trip_id FROM (SELECT trip_id, row_number() OVER (PARTITION BY k
            ORDER BY CASE feed {order} END, trip_id) rn FROM trip_key) WHERE rn = 1""")
    con.execute("""CREATE OR REPLACE TEMP TABLE st_out AS
        SELECT * FROM st_on_date2 WHERE in_box AND trip_id IN (SELECT trip_id FROM trip_keep)""")
    con.execute("""CREATE OR REPLACE TEMP TABLE short_trips AS
        SELECT trip_id FROM st_out GROUP BY 1 HAVING count(*) < 2""")
    con.execute("DELETE FROM st_out WHERE trip_id IN (SELECT trip_id FROM short_trips)")
    per_route = con.execute("""
        SELECT k.noc, k.route_short_name, count(*) n_before,
               count(*) FILTER (WHERE k.trip_id IN (SELECT trip_id FROM trip_keep)) n_after
        FROM trip_key k GROUP BY ALL ORDER BY n_before DESC""").fetchall()
    flagged = [r for r in per_route if r[2] and (r[2] - r[3]) / r[2] > 0.10]
    # Kept trips of one operator and route that leave the same first stop at the same
    # time but differ later: likely superseded variants that exact matching can't
    # remove. Reported for resolution (e.g. against observed vehicle destinations).
    near = con.execute("""
        WITH f AS (SELECT s.trip_id, t.noc, t.route_short_name,
                          first(s.stop_id ORDER BY s.seq) s0,
                          first(s.departure_time ORDER BY s.seq) d0,
                          last(s.stop_id ORDER BY s.seq) s1
                   FROM st_on_date2 s JOIN trips_extent t USING (trip_id)
                   WHERE s.trip_id IN (SELECT trip_id FROM trip_keep) GROUP BY ALL)
        SELECT noc, route_short_name, s0, d0, list(s1 ORDER BY s1), list(trip_id ORDER BY s1)
        FROM f GROUP BY ALL HAVING count(*) > 1 ORDER BY noc, route_short_name, d0""").fetchall()
    n = lambda q: con.execute(q).fetchone()[0]  # noqa: E731
    return {
        "trips_on_date": n_on_date,
        "excluded_superseded_variants": n_excluded,
        "trips_calling_in_extent": n("SELECT count(*) FROM trips_extent"),
        "duplicates_removed": n("SELECT count(*) FROM trip_key") - n("SELECT count(*) FROM trip_keep"),
        "dropped_fewer_than_2_calls_in_box": n("SELECT count(*) FROM short_trips"),
        "trips_out": n("SELECT count(DISTINCT trip_id) FROM st_out"),
        "stop_times_out": n("SELECT count(*) FROM st_out"),
        "stops_out": n("SELECT count(DISTINCT stop_id) FROM st_out"),
        "calls_cut_outside_box": n("SELECT count(*) FROM st_on_date2 WHERE NOT in_box AND "
                                   "trip_id IN (SELECT trip_id FROM trip_keep)"),
        "per_route": per_route,
        "flagged_routes": flagged,
        "same_start_groups": near,
    }


def write(con: duckdb.DuckDBPyConnection, feeds: list[str], day: dt.date, out: Path,
          version: str) -> Path:
    d = f"{day:%Y%m%d}"
    tmp = out.parent / (out.stem + "_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    agencies = " UNION ALL ".join(
        f"SELECT '{f}:' || agency_id agency_id, agency_name, agency_url, agency_timezone, "
        f"agency_noc FROM {f}_agency" for f in feeds)
    routes = " UNION ALL ".join(
        f"SELECT '{f}:' || route_id route_id, '{f}:' || agency_id agency_id, route_short_name, "
        f"route_long_name, route_type FROM {f}_routes" for f in feeds)
    trips = " UNION ALL ".join(
        f"SELECT '{f}:' || route_id route_id, '{f}:' || trip_id trip_id, "
        f"trip_headsign, direction_id FROM {f}_trips" for f in feeds)
    q = {
        "stop_times": """SELECT trip_id, arrival_time, departure_time, stop_id,
            row_number() OVER (PARTITION BY trip_id ORDER BY seq) stop_sequence,
            pickup_type, drop_off_type FROM st_out ORDER BY trip_id, seq""",
        "trips": f"""SELECT route_id, 'D{d}' service_id, trip_id, trip_headsign, direction_id
            FROM ({trips}) WHERE trip_id IN (SELECT DISTINCT trip_id FROM st_out)""",
        "routes": f"""SELECT * FROM ({routes}) WHERE route_id IN
            (SELECT route_id FROM trips_on_date WHERE trip_id IN (SELECT trip_id FROM st_out))""",
        "agency": f"""SELECT * FROM ({agencies}) WHERE agency_id IN (SELECT agency_id FROM
            ({routes}) WHERE route_id IN (SELECT route_id FROM trips_on_date
            WHERE trip_id IN (SELECT trip_id FROM st_out)))""",
        "stops": """SELECT stop_id, any_value(stop_name) stop_name, any_value(lat) stop_lat,
            any_value(lon) stop_lon FROM st_out GROUP BY stop_id""",
        "calendar_dates": f"SELECT 'D{d}' service_id, '{d}' date, 1 exception_type",
        "feed_info": f"""SELECT 'bristol-transit-lab (from BODS)' feed_publisher_name,
            'https://www.bus-data.dft.gov.uk/' feed_publisher_url, 'en' feed_lang,
            '{d}' feed_start_date, '{d}' feed_end_date, '{version}' feed_version""",
    }
    for name, sql in q.items():
        con.execute(f"COPY ({sql}) TO '{tmp / (name + '.txt')}' (HEADER, DELIMITER ',')")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in q:
            z.write(tmp / f"{name}.txt", f"{name}.txt")
            (tmp / f"{name}.txt").unlink()
    tmp.rmdir()
    return out


def trips_per_route_on(con: duckdb.DuckDBPyConnection, feeds: list[str],
                       day: dt.date) -> dict[tuple, int]:
    """Trips per (operator, route) calling in the extent on another date (check 2)."""
    d = f"{day:%Y%m%d}"
    wd = day.strftime("%A").lower()
    out: dict[tuple, int] = {}
    for f in feeds:
        rows = con.execute(f"""
            WITH act AS (
              (SELECT service_id FROM {f}_calendar WHERE {wd}='1' AND start_date<='{d}'
                 AND end_date>='{d}'
               EXCEPT SELECT service_id FROM {f}_calendar_dates
                 WHERE date='{d}' AND exception_type='2')
              UNION SELECT service_id FROM {f}_calendar_dates
                 WHERE date='{d}' AND exception_type='1')
            SELECT a.agency_noc, r.route_short_name, count(DISTINCT t.trip_id)
            FROM {f}_trips t JOIN act USING (service_id) JOIN {f}_routes r USING (route_id)
            JOIN {f}_agency a USING (agency_id)
            WHERE t.route_id IN (SELECT src_route_id FROM trips_extent WHERE feed = '{f}')
            GROUP BY ALL""").fetchall()
        for noc, rsn, n in rows:
            out[(noc, rsn)] = out.get((noc, rsn), 0) + n
    return out
