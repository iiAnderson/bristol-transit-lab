"""
B2 (plans/P2.md §5): bus moving speeds per car segment × direction × period.

1. Positions: one day of clipped archive (or live) SIRI-VM, non-stale; Metrobus vehicles
   dropped; positions within ``busway_buffer_m`` of a busway or bus-only link dropped.
2. Traces: per vehicle, ordered by time, split where fixes are more than ``trace_gap_s``
   apart; matched with OSRM ``/match`` (car profile, ``match_radius_m``), in chunks.
3. Legs: consecutive matched fixes ≤ ``max_leg_s`` apart. Leg speed = matched path
   distance ÷ elapsed time. A leg is dropped if either end is within ``stop_buffer_m`` of
   a GTFS stop (dwell and acceleration), if it is slower than ``min_moving_kmh`` or faster
   than ``max_kmh``, or if its matching's confidence is below ``match_min_confidence``.
4. Traversals: each kept leg's speed is given to every OSM node pair on its path (the
   keys OSRM's segment speed files use). Period by the leg's local start time.

Output per day: traversals (segment × leg) and a summary of drops, for aggregation
across days with the neutral-day filter.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import osmium
import pyarrow as pa
import pyarrow.parquet as pq
import requests
import requests.adapters
from concurrent.futures import ThreadPoolExecutor


class _Busways(osmium.SimpleHandler):
    """Points every ~10 m along busways and bus-only links."""

    def __init__(self):
        super().__init__()
        self.pts: list[tuple[float, float]] = []

    def way(self, w):
        t = w.tags
        hw = t.get("highway")
        bus_only = hw in ("busway", "bus_guideway") or t.get("busway") == "yes" or (
            t.get("access") in ("no", "private") or t.get("motor_vehicle") == "no"
        ) and (t.get("bus") in ("yes", "designated") or t.get("psv") in ("yes", "designated"))
        if not bus_only:
            return
        nodes = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
        for (x1, y1), (x2, y2) in zip(nodes, nodes[1:]):
            d = math.hypot((x2 - x1) * 111320 * math.cos(math.radians(y1)), (y2 - y1) * 111320)
            k = max(1, int(d // 10))
            for i in range(k + 1):
                self.pts.append((x1 + (x2 - x1) * i / k, y1 + (y2 - y1) * i / k))


def busway_points(pbf: Path, out: Path) -> int:
    h = _Busways()
    h.apply_file(str(pbf), locations=True)
    pq.write_table(pa.table({"lon": [p[0] for p in h.pts], "lat": [p[1] for p in h.pts]}), out)
    return len(h.pts)


def _near(con, table: str, pts_parquet: Path, radius_m: float) -> None:
    """Flag rows of ``table`` (lon, lat) within ``radius_m`` of any point, via a grid."""
    cell = radius_m / 111320
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _p AS
        SELECT lon, lat, floor(lon / ({cell} / 0.62))::BIGINT gx, floor(lat / {cell})::BIGINT gy
        FROM read_parquet('{pts_parquet}')""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _hit AS
        SELECT DISTINCT t.rid FROM (SELECT rid, lon, lat,
            floor(lon / ({cell} / 0.62))::BIGINT gx, floor(lat / {cell})::BIGINT gy FROM {table}) t
        JOIN _p p ON p.gx BETWEEN t.gx - 1 AND t.gx + 1 AND p.gy BETWEEN t.gy - 1 AND t.gy + 1
        WHERE ((p.lon - t.lon) * 111320 * 0.62)^2 + ((p.lat - t.lat) * 111320)^2 <= {radius_m ** 2}""")


def _period(t_local: dt.time, periods: dict[str, tuple[str, str]]) -> str:
    for p, (a, b) in periods.items():
        if dt.time.fromisoformat(a) <= t_local < dt.time.fromisoformat(b):
            return p
    return "OP"


def process_day(day_file: Path, osrm_port: int, stops_parquet: Path, busways: Path,
                metrobus: dict, p: dict, periods: dict[str, tuple[str, str]], tz: str,
                out: Path, chunk: int = 100, workers: int = 7, progress=None) -> dict:
    con = duckdb.connect()
    con.execute(f"""CREATE TEMP TABLE pos AS
        SELECT row_number() OVER () rid, operator_ref, vehicle_ref, published_line_name,
               epoch(recorded_at) t, lon, lat
        FROM read_parquet('{day_file}') WHERE NOT stale""")
    n_all = con.execute("SELECT count(*) FROM pos").fetchone()[0]
    lines = ",".join(f"'{x}'" for x in metrobus["lines"])
    n_mb = con.execute(f"""SELECT count(*) FROM pos WHERE operator_ref = '{metrobus['operator']}'
        AND published_line_name IN ({lines})""").fetchone()[0]
    con.execute(f"""DELETE FROM pos WHERE operator_ref = '{metrobus['operator']}'
        AND published_line_name IN ({lines})""")
    _near(con, "pos", busways, p["busway_buffer_m"])
    n_bw = con.execute("SELECT count(*) FROM _hit").fetchone()[0]
    con.execute("DELETE FROM pos WHERE rid IN (SELECT rid FROM _hit)")
    _near(con, "pos", stops_parquet, p["stop_buffer_m"])
    con.execute("CREATE TEMP TABLE near_stop AS SELECT rid FROM _hit")
    rows = con.execute("""SELECT operator_ref || '|' || vehicle_ref, rid, t, lon, lat,
        rid IN (SELECT rid FROM near_stop) FROM pos ORDER BY 1, t""").fetchall()

    # traces
    traces, cur, last_v, last_t = [], [], None, None
    for v, rid, t, lon, lat, ns in rows:
        if v != last_v or (last_t is not None and t - last_t > p["trace_gap_s"]):
            if len(cur) >= 2:
                traces.append(cur)
            cur = []
        cur.append((rid, t, lon, lat, ns))
        last_v, last_t = v, t
    if len(cur) >= 2:
        traces.append(cur)

    zone = ZoneInfo(tz)
    out_u, out_v, out_speed, out_period, out_leg = [], [], [], [], []
    stats = dict(positions=n_all, metrobus_dropped=n_mb, busway_dropped=n_bw,
                 traces=len(traces), requests=0, legs=0, legs_kept=0, drop_unmatched=0,
                 drop_low_conf=0, drop_long=0, drop_stop=0, drop_slow=0, drop_fast=0)
    parts = [tr[i:i + chunk] for tr in traces for i in range(0, len(tr) - 1, chunk - 1)
             if len(tr[i:i + chunk]) >= 2]                 # chunks overlap by one fix
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_maxsize=workers)
    session.mount("http://", adapter)

    def match(part):
        coords = ";".join(f"{x[2]:.6f},{x[3]:.6f}" for x in part)
        r = session.get(f"http://127.0.0.1:{osrm_port}/match/v1/driving/{coords}", params={
            "timestamps": ";".join(str(int(x[1])) for x in part),
            "radiuses": ";".join([str(p["match_radius_m"])] * len(part)),
            "annotations": "nodes,distance", "overview": "false", "gaps": "split",
            "tidy": "false"}, timeout=300)
        return part, r.json()

    leg_id = 0
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for part, j in ex.map(match, parts):
            stats["requests"] += 1
            done += 1
            if progress and done % 2000 == 0:
                progress(f"{done}/{len(parts)} match requests")
            if j.get("code") != "Ok":
                stats["drop_unmatched"] += len(part) - 1
                continue
            tps = j["tracepoints"]
            for mi, m in enumerate(j["matchings"]):
                idx = sorted(((tp["waypoint_index"], k) for k, tp in enumerate(tps)
                              if tp is not None and tp["matchings_index"] == mi))
                idx = [k for _, k in idx]
                for li, leg in enumerate(m["legs"]):
                    stats["legs"] += 1
                    a, b = part[idx[li]], part[idx[li + 1]]
                    dt_s = b[1] - a[1]
                    if m["confidence"] < p["match_min_confidence"]:
                        stats["drop_low_conf"] += 1
                        continue
                    if dt_s <= 0 or dt_s > p["max_leg_s"]:
                        stats["drop_long"] += 1
                        continue
                    if a[4] or b[4]:
                        stats["drop_stop"] += 1
                        continue
                    ann = leg.get("annotation") or {}
                    dist = sum(ann.get("distance", [])) or leg["distance"]
                    kmh = dist / dt_s * 3.6
                    if kmh < p["min_moving_kmh"]:
                        stats["drop_slow"] += 1
                        continue
                    if kmh > p["max_kmh"]:
                        stats["drop_fast"] += 1
                        continue
                    nodes = ann.get("nodes", [])
                    per = _period(dt.datetime.fromtimestamp(a[1], zone).time(), periods)
                    leg_id += 1
                    stats["legs_kept"] += 1
                    for u, v in zip(nodes, nodes[1:]):
                        out_u.append(u)
                        out_v.append(v)
                        out_speed.append(kmh)
                        out_period.append(per)
                        out_leg.append(leg_id)
    t = pa.table({"u": pa.array(out_u, pa.int64()), "v": pa.array(out_v, pa.int64()),
                  "kmh": pa.array(out_speed, pa.float32()), "period": out_period,
                  "leg": pa.array(out_leg, pa.int32())})
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out, compression="zstd")
    stats["traversals"] = t.num_rows
    return stats
