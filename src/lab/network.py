"""The identity of a routable network (SPEC §4, amended at P3): the inputs that decide
what R5 builds, by hash. Two outputs are comparable only if their versions match."""
from __future__ import annotations

import hashlib
import json


def version(input_hashes: dict[str, str], elevation: str | None = None) -> str:
    """``input_hashes``: name → sha256 for the OSM clip, any OSM patch file, the GTFS
    feeds and, with ``elevation`` (the slope cost function's name), the terrain raster."""
    doc = {"inputs": dict(sorted(input_hashes.items())), "elevation": elevation}
    h = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:10]
    return f"{'flat' if elevation is None else elevation.lower()}-{h}"


def settings(cfg, raw: dict, ps: dict) -> dict:
    """What the routable baseline network is, from params and the feed registry:
    ``elevation`` (the slope cost function's name, or None), ``walk_kmh`` (rescaled when
    terrain is on), ``tif``, ``version``, ``hashes``, and the directories that depend on
    them. ``LAB_NETWORK=flat`` in the environment turns terrain off, to reproduce
    network v1."""
    import os
    from pathlib import Path
    from .supply import feeds
    fn = ps["routing.elevation_cost_function"]
    fn = None if fn == "NONE" or os.environ.get("LAB_NETWORK") == "flat" else fn
    names = ["osm_clip", "bus_gtfs", "rail_gtfs"] + (["dem_clip"] if fn else [])
    hashes = {n: feeds.get(cfg, n)["sha256"] for n in names}
    v = version(hashes, fn)
    base = raw["baseline"]
    root = Path(cfg.root) / base["skims"] / base["scenario"]
    return {"elevation": fn, "version": v, "hashes": hashes,
            "walk_kmh": ps["routing.walk_speed_kmh"] * (ps["routing.walk_speed_terrain_factor"] if fn else 1.0),
            "tif": Path(cfg.root) / raw["dem"]["out"],
            "skims": root / v, "car_skims": root / "car",
            "r5r_net": Path(cfg.root) / base["r5r"] / f"net-{v}"}


def skim(net: dict, mode: str, period: str = "DAY"):
    """Path of one skim file. Walk and cycle have no period ("DAY")."""
    d = net["car_skims"] if mode == "car" else net["skims"] / mode
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{period}.parquet"
