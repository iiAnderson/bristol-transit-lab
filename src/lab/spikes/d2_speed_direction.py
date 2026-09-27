"""
Spike for D2 (plans/P2.md): does R5 (via r5py) honour per-direction speed edits?

First version (``run``): real ways in a small extract. It cannot discriminate: routes
between a way's end nodes take 0–1 minute at R5's minute resolution, and a slowed way
is bypassed on parallel streets. Kept for the record. ``run_synthetic`` is the test:

On a small extract, pick two-way primary/secondary ways and route by car between each
way's end nodes in both directions on three networks:

* baseline — OSM as downloaded;
* forward — the chosen ways tagged ``maxspeed:forward=5 mph`` (backward untouched);
* both — the chosen ways tagged ``maxspeed=5 mph``.

Option A (r5py for car) passes if, on ``forward``, times rise along the way's direction
and not against it, and on ``both`` they rise both ways. The box comes from the caller.
"""
from __future__ import annotations

import datetime as dt
import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Point


def _osmium(*a: str) -> None:
    subprocess.run(["osmium", *a], check=True, capture_output=True)


def extract(src: Path, box: tuple, out: Path) -> Path:
    _osmium("extract", "-b", ",".join(map(str, box)), "--strategy", "complete_ways",
            "-O", "-o", str(out), str(src))
    return out


def choose_ways(osm_xml: Path, n: int, seed: int) -> list[dict]:
    root = ET.parse(osm_xml).getroot()
    nodes = {nd.get("id"): (float(nd.get("lon")), float(nd.get("lat")))
             for nd in root.iter("node")}
    cands = []
    for w in root.iter("way"):
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        if tags.get("highway") not in ("primary", "secondary", "tertiary"):
            continue
        if tags.get("oneway") in ("yes", "-1", "1") or "junction" in tags:
            continue
        refs = [nd.get("ref") for nd in w.iter("nd")]
        if len(refs) < 2 or refs[0] not in nodes or refs[-1] not in nodes:
            continue
        a, b = nodes[refs[0]], nodes[refs[-1]]
        km = math.hypot((a[0] - b[0]) * 111.32 * math.cos(math.radians(a[1])),
                        (a[1] - b[1]) * 111.32)
        if km >= 0.3:
            cands.append({"id": w.get("id"), "highway": tags["highway"], "km": round(km, 3),
                          "maxspeed": tags.get("maxspeed"), "start": a, "end": b})
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(cands), size=min(n, len(cands)), replace=False)
    return [cands[i] for i in idx]


def edit(osm_xml: Path, ways: set[str], tag: str, value: str, out_xml: Path) -> None:
    tree = ET.parse(osm_xml)
    for w in tree.getroot().iter("way"):
        if w.get("id") in ways:
            for t in list(w.iter("tag")):
                if t.get("k") in ("maxspeed", "maxspeed:forward", "maxspeed:backward"):
                    w.remove(t)
            ET.SubElement(w, "tag", k=tag, v=value)
    tree.write(out_xml, encoding="utf-8", xml_declaration=True)


def route(pbf: Path, ways: list[dict], departure: dt.datetime) -> dict[tuple, float]:
    from r5py import TransportMode, TransportNetwork, TravelTimeMatrix
    net = TransportNetwork(str(pbf), [])
    out = {}
    for w in ways:
        for direction, (a, b) in (("fwd", (w["start"], w["end"])),
                                  ("bwd", (w["end"], w["start"]))):
            o = gpd.GeoDataFrame({"id": ["o"]}, geometry=[Point(*a)], crs="EPSG:4326")
            d = gpd.GeoDataFrame({"id": ["d"]}, geometry=[Point(*b)], crs="EPSG:4326")
            r = TravelTimeMatrix(net, origins=o, destinations=d, departure=departure,
                                 transport_modes=[TransportMode.CAR], snap_to_network=True)
            v = r["travel_time"].iloc[0]
            out[(w["id"], direction)] = None if v is None or v != v else int(v)
    return out


def run(src: Path, box: tuple, work: Path, departure: dt.datetime, n: int = 20,
        seed: int = 1) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    base_pbf = extract(src, box, work / "base.osm.pbf")
    base_xml = work / "base.osm"
    _osmium("cat", "-O", "-o", str(base_xml), str(base_pbf))
    ways = choose_ways(base_xml, n, seed)
    ids = {w["id"] for w in ways}
    nets = {"baseline": base_pbf}
    for name, tag in (("forward", "maxspeed:forward"), ("both", "maxspeed")):
        x = work / f"{name}.osm"
        edit(base_xml, ids, tag, "5 mph", x)
        pbf = work / f"{name}.osm.pbf"
        _osmium("cat", "-O", "-o", str(pbf), str(x))
        nets[name] = pbf
    times = {k: route(p, ways, departure) for k, p in nets.items()}
    rows = []
    for w in ways:
        row = {k: w[k] for k in ("id", "highway", "km", "maxspeed")}
        for net in nets:
            for d in ("fwd", "bwd"):
                row[f"{net}_{d}"] = times[net][(w["id"], d)]
        rows.append(row)

    def rose(r, net, d):
        b, x = r[f"baseline_{d}"], r[f"{net}_{d}"]
        return b is not None and x is not None and x > b

    summary = {
        "ways": len(rows),
        "baseline_zero_minute_routes": sum(r[f"baseline_{d}"] == 0 for r in rows
                                           for d in ("fwd", "bwd")),
        "forward_rises_fwd": sum(rose(r, "forward", "fwd") for r in rows),
        "forward_rises_bwd": sum(rose(r, "forward", "bwd") for r in rows),
        "both_rises_fwd": sum(rose(r, "both", "fwd") for r in rows),
        "both_rises_bwd": sum(rose(r, "both", "bwd") for r in rows),
    }
    summary["option_a_direction_test"] = (
        "pass" if summary["forward_rises_fwd"] >= 0.8 * len(rows)
        and summary["forward_rises_bwd"] <= 0.1 * len(rows)
        and summary["both_rises_bwd"] >= 0.8 * len(rows) else "fail")
    return {"summary": summary, "rows": rows}


def synthetic_osm(out: Path, tags: dict[str, str], lon0: float, lat0: float,
                  km: float = 5.0, n: int = 51) -> tuple:
    """One straight two-way primary road of ``km`` with nothing parallel to it."""
    dlon = km / (111.32 * math.cos(math.radians(lat0))) / (n - 1)
    nodes = [(i + 1, lon0 + i * dlon, lat0) for i in range(n)]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="lab">']
    xml += [f'<node id="{i}" version="1" lat="{la:.7f}" lon="{lo:.7f}"/>' for i, lo, la in nodes]
    split = tags.pop("_split", None)       # (forward speed, backward speed)
    offset = tags.pop("_offset_deg", 0.0)  # draw the backward way apart, like a dual carriageway
    if split and offset:
        back = [(20000 + i, lo, la - offset) for i, lo, la in nodes]
        xml += [f'<node id="{i}" version="1" lat="{la:.7f}" lon="{lo:.7f}"/>' for i, lo, la in back]
        # join the two carriageways at both ends
        back = [nodes[0], *back[1:-1], nodes[-1]]
    else:
        back = nodes
    ways = ([("1", nodes, {"oneway": "yes", "maxspeed": split[0]}),
             ("2", back[::-1], {"oneway": "yes", "maxspeed": split[1]})]
            if split else [("1", nodes, tags)])
    for wid, seq, t in ways:
        xml.append(f'<way id="{wid}" version="1">')
        xml += [f'<nd ref="{i}"/>' for i, _, _ in seq]
        for k, v in {"highway": "primary", **t}.items():
            xml.append(f'<tag k="{k}" v="{v}"/>')
        xml.append("</way>")
    # Dead-end side streets at every node: enough junctions that R5 keeps the component
    # (it prunes small islands), with no alternative route along the main road.
    for i, lo, la in nodes[1:-1]:
        xml.append(f'<node id="{10000 + i}" version="1" lat="{la + 0.0006:.7f}" lon="{lo:.7f}"/>')
        xml.append(f'<way id="{10000 + i}" version="1"><nd ref="{i}"/><nd ref="{10000 + i}"/>'
                   '<tag k="highway" v="residential"/></way>')
    xml.append("</osm>")
    x = out.with_suffix(".osm")
    x.write_text("\n".join(xml))
    _osmium("cat", "-O", "-o", str(out), str(x))
    return (nodes[0][1], lat0), (nodes[-1][1], lat0)


def run_synthetic(work: Path, departure: dt.datetime, lon0: float, lat0: float) -> dict:
    """A→B follows the way's direction (forward). Expected at 5 km: 10 mph ≈ 19 min,
    60 mph ≈ 3 min."""
    work.mkdir(parents=True, exist_ok=True)
    cases = {
        "default": {},
        "maxspeed_10mph": {"maxspeed": "10 mph"},
        "forward_10_backward_60": {"maxspeed:forward": "10 mph", "maxspeed:backward": "60 mph"},
        "forward_60_backward_10": {"maxspeed:forward": "60 mph", "maxspeed:backward": "10 mph"},
        # Workaround: the two-way road as two opposite one-way ways, one maxspeed each.
        "split_60_10": {"_split": ("60 mph", "10 mph")},
        "split_10_60": {"_split": ("10 mph", "60 mph")},
        "split_offset_60_10": {"_split": ("60 mph", "10 mph"), "_offset_deg": 0.00007},
        "split_offset_10_60": {"_split": ("10 mph", "60 mph"), "_offset_deg": 0.00007},
    }
    out = {}
    for name, tags in cases.items():
        a, b = synthetic_osm(work / f"{name}.osm.pbf", dict(tags), lon0, lat0)
        w = [{"id": name, "start": a, "end": b}]
        t = route(work / f"{name}.osm.pbf", w, departure)
        out[name] = {"fwd_min": t[(name, "fwd")], "bwd_min": t[(name, "bwd")]}
    def directional(x, slow_fwd):
        return x["fwd_min"] > 2 * x["bwd_min"] if slow_fwd else x["bwd_min"] > 2 * x["fwd_min"]
    out["maxspeed_honoured"] = out["maxspeed_10mph"]["fwd_min"] > 2 * out["default"]["fwd_min"]
    out["forward_backward_tags_directional"] = (
        directional(out["forward_10_backward_60"], True)
        and directional(out["forward_60_backward_10"], False))
    out["split_oneway_directional"] = (directional(out["split_10_60"], True)
                                       and directional(out["split_60_10"], False))
    return out
