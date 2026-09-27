"""
B3 (plans/P2.md §5): WebTRIS site speeds by period and direction, matched to SRN segments.

* Sites: active WebTRIS sites in the clip box on a main carriageway — MIDAS loops, and
  TMU/TAME sites described as "between" two junctions. Slip roads and junction
  interiors ("exit", "entry", "within") are excluded, as are carriageway connectors.
* Speed per site × period: the flow-weighted harmonic (space-mean) speed,
  Σ volume ÷ Σ (volume / speed), the same form as DfT's measure, over 15-minute rows
  with a speed and a positive volume.
* Day filter: weekday periods (AM, IP, PM, off-peak) use neutral days — Tue–Thu in
  school term, not a bank holiday, not August — and the weekend is Sat–Sun, both from
  the same calendar in config.
* Matching: nearest SRN segment of the same road whose bearing agrees with the site's
  direction (within 60°), within ``max_match_m``.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path

import duckdb

ROAD_RE = re.compile(r"\bon (?:link )?((?:[AM]\d+)(?:\(M\))?)\b")
DIR_BEARING = {"Northbound": 0, "Eastbound": 90, "Southbound": 180, "Westbound": 270}


def site_table(sites_json: Path, box: tuple) -> list[dict]:
    out = []
    x0, y0, x1, y1 = box
    for s in json.loads(sites_json.read_text()):
        if s["Status"] != "Active" or not (x0 <= s["Longitude"] <= x1 and y0 <= s["Latitude"] <= y1):
            continue
        desc = s["Description"] or ""
        direction = desc.rsplit(";", 1)[-1].strip()
        if direction not in DIR_BEARING:
            continue
        if desc.startswith("MIDAS"):
            road = s["Name"].split("/")[0]
            kind = "midas"
        else:
            m = ROAD_RE.search(desc)
            if not m or " between " not in desc:
                continue
            road, kind = m.group(1), "tmu"
        out.append({"site_id": s["Id"], "name": s["Name"], "road": road, "kind": kind,
                    "direction": direction, "lon": s["Longitude"], "lat": s["Latitude"]})
    return out


def day_types(calendar: dict, start: dt.date, end: dt.date) -> dict[dt.date, str]:
    """'neutral' (Tue–Thu, term, not a bank holiday, not August), 'weekday_other',
    'weekend'."""
    terms = [(dt.date.fromisoformat(str(a)), dt.date.fromisoformat(str(b)))
             for a, b in calendar["terms"]]
    bank = {dt.date.fromisoformat(str(d)) for d in calendar["bank_holidays"]}
    out = {}
    d = start
    while d <= end:
        if d.weekday() >= 5:
            out[d] = "weekend"
        elif (d.weekday() in (1, 2, 3) and d.month != 8 and d not in bank
              and any(a <= d <= b for a, b in terms)):
            out[d] = "neutral"
        else:
            out[d] = "weekday_other"
        d += dt.timedelta(days=1)
    return out


def _bearing(lon1, lat1, lon2, lat2) -> float:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.cos(math.radians(lon2 - lon1)))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def process(con: duckdb.DuckDBPyConnection, webtris_dir: Path, sites: list[dict],
            days: dict[dt.date, str], periods: dict[str, tuple[str, str]],
            segments: Path, max_match_m: float) -> dict:
    con.execute("CREATE OR REPLACE TEMP TABLE wsite AS SELECT * FROM (VALUES " +
                ",".join(f"('{s['site_id']}','{s['name']}','{s['road']}','{s['kind']}',"
                         f"'{s['direction']}',{s['lon']},{s['lat']})" for s in sites) +
                ") t(site_id, name, road, kind, direction, lon, lat)")
    con.execute("CREATE OR REPLACE TEMP TABLE dtype AS SELECT * FROM (VALUES " +
                ",".join(f"(DATE '{d}', '{t}')" for d, t in days.items()) + ") t(date, day_type)")
    # period of a 15-minute row = period containing its start (time_end − 15 min)
    cases = " ".join(f"WHEN t >= TIME '{a}' AND t < TIME '{b}' THEN '{p}'"
                     for p, (a, b) in periods.items())
    con.execute(f"""CREATE OR REPLACE TEMP TABLE wrow AS
        SELECT r.site_id, r.date, d.day_type, r.avg_mph * 1.609344 kmh, r.volume,
               CASE WHEN d.day_type = 'weekend' THEN 'WE'
                    ELSE coalesce(CASE {cases} END, 'OP') END period
        FROM (SELECT *, (CAST(time_end AS TIME) - INTERVAL 14 MINUTE)::TIME t
              FROM read_parquet('{webtris_dir}/site=*.parquet')) r
        JOIN dtype d USING (date)
        WHERE r.site_id IN (SELECT site_id FROM wsite) AND r.avg_mph > 0 AND r.volume > 0""")
    # weekday peaks only on neutral days; OP on neutral days; WE on weekends
    con.execute("""CREATE OR REPLACE TEMP TABLE wspeed AS
        SELECT site_id, period, count(DISTINCT date) days, sum(volume) volume,
               sum(volume) / sum(volume / kmh) kmh_hmean
        FROM wrow WHERE (period = 'WE') OR (day_type = 'neutral')
        GROUP BY ALL""")
    # match sites to SRN segments: same ref, bearing within 60°, nearest within max_match_m
    segs = con.execute(f"""SELECT way_id, seq, forward, u, v, ref, lon_u, lat_u, lon_v, lat_v
        FROM read_parquet('{segments}') WHERE road_class = 'srn'""").fetchall()
    by_ref: dict[str, list] = {}
    for s in segs:
        by_ref.setdefault(s[5], []).append(s)
    match = []
    for st in sites:
        best = None
        for s in by_ref.get(st["road"], []):
            mlon, mlat = (s[6] + s[8]) / 2, (s[7] + s[9]) / 2
            dm = math.hypot((mlon - st["lon"]) * 111320 * math.cos(math.radians(mlat)),
                            (mlat - st["lat"]) * 111320)
            if dm > max_match_m:
                continue
            b = _bearing(s[6], s[7], s[8], s[9])
            diff = abs((b - DIR_BEARING[st["direction"]] + 180) % 360 - 180)
            if diff > 60:
                continue
            if best is None or dm < best[0]:
                best = (dm, s)
        if best:
            s = best[1]
            match.append((st["site_id"], s[0], s[1], s[2], s[3], s[4], round(best[0], 1)))
    con.execute("CREATE OR REPLACE TEMP TABLE wmatch (site_id VARCHAR, way_id BIGINT, seq INT, "
                "forward BOOLEAN, u BIGINT, v BIGINT, match_m DOUBLE)")
    if match:
        con.executemany("INSERT INTO wmatch VALUES (?,?,?,?,?,?,?)", match)
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    return {
        "sites": len(sites),
        "sites_by_kind": q("SELECT kind, count(*) FROM wsite GROUP BY 1"),
        "sites_matched": len(match),
        "unmatched": q("SELECT name, road, direction FROM wsite WHERE site_id NOT IN "
                       "(SELECT site_id FROM wmatch) ORDER BY 1")[:40],
        "days": q("SELECT day_type, count(*) FROM dtype GROUP BY 1 ORDER BY 1"),
        "speed_by_period_median": q("""SELECT period, count(*), round(median(kmh_hmean), 1),
            round(median(days)) FROM wspeed GROUP BY 1 ORDER BY 1"""),
    }
