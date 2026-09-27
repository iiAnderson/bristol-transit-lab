"""
The car network table for P2b (plans/P2.md §5 "as built").

Every car segment — an OSM node pair on a car-routable way, in each direction it can be
driven — with the attributes calibration needs: way, highway class, ``ref``, parsed
``maxspeed``, oneway, bus-lane/busway tags (B1), and its length. Authority, area type
and direction are attached afterwards in DuckDB (``annotate``).

OSRM's segment speed files are keyed by OSM node pair and direction, so this table is
also what the per-period speed files are written from.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import osmium
import pyarrow as pa
import pyarrow.parquet as pq

CAR_HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "road",
}
BUS_TAGS = ("busway", "busway:left", "busway:right", "busway:both", "bus:lanes",
            "bus:lanes:forward", "bus:lanes:backward", "lanes:bus", "lanes:bus:forward",
            "lanes:bus:backward", "lanes:psv", "lanes:psv:forward", "lanes:psv:backward",
            "psv", "bus", "psv:lanes", "psv:lanes:forward", "psv:lanes:backward")
MPH = 1.609344

SCHEMA = pa.schema([
    ("way_id", pa.int64()), ("seq", pa.int32()), ("u", pa.int64()), ("v", pa.int64()),
    ("forward", pa.bool_()),       # u -> v follows the way's node order
    ("lon_u", pa.float64()), ("lat_u", pa.float64()),
    ("lon_v", pa.float64()), ("lat_v", pa.float64()),
    ("length_m", pa.float64()), ("highway", pa.string()), ("ref", pa.string()),
    ("name", pa.string()), ("maxspeed_kmh", pa.float64()), ("maxspeed_raw", pa.string()),
    ("oneway", pa.string()), ("junction", pa.string()), ("lanes", pa.string()),
    ("bus_tags", pa.string()),     # "k=v;k=v" for any B1 tag present
])


def parse_maxspeed(v: str | None) -> float | None:
    """km/h from an OSM maxspeed value; None for unparsable or non-numeric values."""
    if not v:
        return None
    v = v.strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?", v)
    if not m:
        return None
    x = float(m.group(1))
    return x * MPH if m.group(2) == "mph" else x


def _hav(lon1, lat1, lon2, lat2) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _directions(tags) -> tuple[bool, bool]:
    ow = tags.get("oneway")
    hw = tags.get("highway", "")
    junction = tags.get("junction")
    if ow in ("yes", "true", "1") or junction in ("roundabout", "circular") \
            or (hw in ("motorway", "motorway_link") and ow != "no"):
        return True, False
    if ow == "-1":
        return False, True
    return True, True


class _Handler(osmium.SimpleHandler):
    def __init__(self, box=None, highways=CAR_HIGHWAYS):
        super().__init__()
        self.box, self.highways = box, highways
        self.rows: dict[str, list] = {f.name: [] for f in SCHEMA}

    def way(self, w):
        t = w.tags
        hw = t.get("highway")
        if hw not in self.highways or t.get("area") == "yes":
            return
        if t.get("motor_vehicle") in ("no", "private") or t.get("access") in ("no", "private"):
            if hw not in ("motorway", "trunk", "primary", "secondary", "tertiary"):
                return
        fwd, bwd = _directions(t)
        nodes = [(n.ref, n.lon, n.lat) for n in w.nodes if n.location.valid()]
        if len(nodes) < 2:
            return
        bus = ";".join(f"{k}={t.get(k)}" for k in BUS_TAGS if k in t)
        ms_raw = t.get("maxspeed")
        common = dict(way_id=w.id, highway=hw, ref=t.get("ref"), name=t.get("name"),
                      maxspeed_kmh=parse_maxspeed(ms_raw), maxspeed_raw=ms_raw,
                      oneway=t.get("oneway"), junction=t.get("junction"),
                      lanes=t.get("lanes"), bus_tags=bus or None)
        x0, y0, x1, y1 = self.box or (-180, -90, 180, 90)
        for i, ((a, lo1, la1), (b, lo2, la2)) in enumerate(zip(nodes, nodes[1:])):
            if not (x0 <= (lo1 + lo2) / 2 <= x1 and y0 <= (la1 + la2) / 2 <= y1):
                continue
            L = _hav(lo1, la1, lo2, la2)
            for keep, u, v, lu, au, lv, av, f in (
                    (fwd, a, b, lo1, la1, lo2, la2, True),
                    (bwd, b, a, lo2, la2, lo1, la1, False)):
                if not keep:
                    continue
                row = dict(common, seq=i, u=u, v=v, forward=f, lon_u=lu, lat_u=au,
                           lon_v=lv, lat_v=av, length_m=L)
                for k in self.rows:
                    self.rows[k].append(row[k])


def build_segments(pbf: Path, out: Path, box=None, highways=CAR_HIGHWAYS) -> dict:
    h = _Handler(box, highways)
    h.apply_file(str(pbf), locations=True)
    t = pa.table(h.rows, schema=SCHEMA)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out, compression="zstd")
    return {"segments": t.num_rows,
            "ways": len(set(h.rows["way_id"])),
            "km_directional": round(sum(h.rows["length_m"]) / 1000, 1)}
