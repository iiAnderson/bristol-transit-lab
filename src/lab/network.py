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


def skim_change(a_dir, b_dir) -> dict:
    """Pair-by-pair change between two network versions' skims (b − a), for the change
    report: PT median and mean components, walk and cycle times."""
    import pandas as pd
    out = {}
    for per in ("AM", "IP"):
        fa, fb = a_dir / "pt" / f"{per}.parquet", b_dir / "pt" / f"{per}.parquet"
        if not (fa.is_file() and fb.is_file()):
            continue
        cols = ["from_id", "to_id", "p50", "unreachable", "access_min", "egress_min", "transfer_min",
                "wait_min", "ride_min", "gc_min", "ride_share"]
        j = pd.read_parquet(fa, columns=cols).merge(pd.read_parquet(fb, columns=cols),
                                                    on=["from_id", "to_id"], suffixes=("_a", "_b"))
        both = j[~j.unreachable_a & ~j.unreachable_b]
        d = both.p50_b - both.p50_a
        walk = lambda x, s: x[f"access_min_{s}"] + x[f"egress_min_{s}"] + x[f"transfer_min_{s}"]  # noqa: E731
        out[f"pt_{per}"] = {
            "pairs": int(len(j)), "reachable_a": int((~j.unreachable_a).sum()),
            "reachable_b": int((~j.unreachable_b).sum()),
            "lost": int((~j.unreachable_a & j.unreachable_b).sum()),
            "gained": int((j.unreachable_a & ~j.unreachable_b).sum()),
            "median_p50_a": float(both.p50_a.median()), "median_p50_b": float(both.p50_b.median()),
            "p50_change_min": {"mean": round(float(d.mean()), 2), "p5": float(d.quantile(.05)),
                               "median": float(d.median()), "p95": float(d.quantile(.95))},
            "share_p50_2min_slower": round(float((d >= 2).mean()), 3),
            "share_p50_2min_faster": round(float((d <= -2).mean()), 3),
            "mean_walk_min_a": round(float(walk(both, "a").mean()), 2),
            "mean_walk_min_b": round(float(walk(both, "b").mean()), 2),
            "mean_wait_change_min": round(float((both.wait_min_b - both.wait_min_a).mean()), 2),
            "mean_ride_change_min": round(float((both.ride_min_b - both.ride_min_a).mean()), 2),
            "median_gc_a": float(both.gc_min_a.median()), "median_gc_b": float(both.gc_min_b.median())}
    for mode in ("walk", "cycle"):
        fa, fb = a_dir / mode / "DAY.parquet", b_dir / mode / "DAY.parquet"
        if not (fa.is_file() and fb.is_file()):
            continue
        j = pd.read_parquet(fa).merge(pd.read_parquet(fb), on=["from_id", "to_id"], suffixes=("_a", "_b"))
        both = j[j.travel_time_a.notna() & j.travel_time_b.notna()]
        d = both.travel_time_b - both.travel_time_a
        corr = {}
        if "travel_time_corrected_b" in j or "travel_time_corrected" in j:
            c = j.get("travel_time_corrected_b", j.get("travel_time_corrected"))
            raw_b = j.travel_time_b
            corr = {"truncation_correction": {
                f"pairs_within_{t}_min": {"raw": int((raw_b <= t).sum()), "corrected": int((c <= t).sum()),
                                          "change_pct": round(float(100 * ((c <= t).sum() / (raw_b <= t).sum() - 1)), 1)}
                for t in (15, 30, 45)}}
        out[mode] = corr | {"reachable_a": int(j.travel_time_a.notna().sum()),
                     "reachable_b": int(j.travel_time_b.notna().sum()),
                     "mean_a": round(float(both.travel_time_a.mean()), 2),
                     "mean_change_min": round(float(d.mean()), 2),
                     "mean_change_pct": round(float(100 * d.sum() / both.travel_time_a.sum()), 1),
                     "p5_p50_p95": [float(x) for x in d.quantile([.05, .5, .95])]}
    return out
