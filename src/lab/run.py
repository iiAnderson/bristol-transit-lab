"""
Running a scenario (SPEC §5, §8.2; plans/P3.md P3b-5): PT skims on the scenario's network
at each timetable offset, the offsets combined, and connectivity metrics for the
scenario, its parent and the difference. Demand-dependent metrics arrive in P5–P6.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from . import network, params
from . import skims as sk

MEAN_COLS = ["access_min", "wait_min", "ride_min", "transfer_min", "egress_min", "n_rides", "n_transfers",
             "p25", "p50", "p75", "best_min", "reach_share", "walk_only_share", "ride_share",
             "r_access_min", "r_wait_min", "r_ride_min", "r_transfer_min", "r_egress_min", "r_n_transfers",
             "r_best_min", "gc_min", "gc_min_random_arrival", "gc_ride_min"]


def offset_network(cfg, raw: dict, ps: dict, gtfs: dict[str, Path]) -> dict:
    """Network settings for one offset's feeds: the baseline's terrain and walk speed,
    with the version and the r5r folder keyed to these GTFS files."""
    from .supply import feeds
    base = network.settings(cfg, raw, ps)
    hashes = {"osm_clip": feeds.get(cfg, "osm_clip")["sha256"]}
    if base["elevation"]:
        hashes["dem_clip"] = base["hashes"]["dem_clip"]
    hashes |= {f"gtfs:{k}": params.file_hash(p) for k, p in sorted(gtfs.items())}
    v = network.version(hashes, base["elevation"])
    return base | {"version": v, "hashes": hashes, "gtfs": gtfs,
                   "r5r_net": Path(cfg.root) / raw["baseline"]["r5r"] / f"net-{v}"}


def pt_skim(cfg, raw: dict, ps: dict, nw: dict, period: str, work: Path, day, log, chunk: int = 100) -> pd.DataFrame:
    """r5r PT skim for one period on one network (as ``lab skims pt``, for any feeds)."""
    import duckdb
    net = nw["r5r_net"]
    work.mkdir(parents=True, exist_ok=True)
    net.mkdir(parents=True, exist_ok=True)
    want = {Path(raw["osm"]["clip"]).name: cfg.root / raw["osm"]["clip"]} | {f"{k}.zip": p for k, p in nw["gtfs"].items()}
    for name, src in want.items():
        dst = net / name
        if not dst.is_file() or params.file_hash(dst) != params.file_hash(src):
            shutil.copyfile(src, dst)
    for stale in net.glob("*.zip"):
        if stale.name not in want:
            stale.unlink()
    if nw["elevation"] and not ((net / "elevation.tif").is_file()
                                and params.file_hash(net / "elevation.tif") == nw["hashes"]["dem_clip"]):
        shutil.copyfile(nw["tif"], net / "elevation.tif")
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        con.execute("SELECT OA21CD id, lat, lon FROM int_oa_pwc ORDER BY 1").df().to_csv(work / "origins.csv", index=False)
        con.execute("SELECT LSOA21CD id, lat, lon FROM skim_dest ORDER BY 1").df() \
            .to_csv(work / "destinations.csv", index=False)
    (work / "sample_ids.csv").write_text("id\n")              # no per-minute sample for scenario runs
    rcfg = {"net_dir": str(net), "origins": str(work / "origins.csv"), "destinations": str(work / "destinations.csv"),
            "departure": f"{day} {ps[f'skims.window_start.{period}']}", "window_min": ps["skims.departure_window_min"],
            "max_rides": ps["routing.max_rides"], "walk_speed_kmh": nw["walk_kmh"], "elevation": nw["elevation"],
            "network_version": nw["version"], "max_walk_min": ps["routing.max_walk_min"],
            "max_trip_min": ps["routing.max_trip_min"], "reach_share_min": ps["routing.pt_reachable_share_min"],
            "chunk": chunk, "sample_ids": str(work / "sample_ids.csv"), "out_dir": str(work / "chunks"),
            "java_mem": "10G"}
    script = cfg.root / "src" / "lab" / "r" / "pt_skims.R"
    hs = list(nw["hashes"].values())
    sk.run_pt(script, rcfg, work / "config.json", log, [hs[0], *hs[1:3], params.file_hash(script), nw["version"]])
    s = sk.combine(work / "chunks")
    w = {k: ps[f"generalised_cost.{k}"] for k in ("w_walk", "w_wait", "p_interchange")}
    curve = ps["generalised_cost.first_wait_curve"]
    s["gc_min"] = sk.gc_tag(s, w, curve).where(~s["unreachable"])
    s["gc_min_random_arrival"] = sk.gc_from_components(s, w).where(~s["unreachable"])
    s["gc_ride_min"] = sk.gc_tag(sk.ride_only(s), w, curve).where(s["ride_share"] >= ps["routing.pt_reachable_share_min"])
    return s


def combine_offsets(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """One skim from a scenario's offsets (plans/P3.md D7 as decided): every time and
    cost is the mean over the offsets in which the pair is reachable; ``p50_min_offsets``
    and ``p50_max_offsets`` give the range; a pair is unreachable unless it is reachable
    at every offset."""
    n = len(frames)
    if n == 1:
        f = frames[0].copy()
        f["p50_min_offsets"], f["p50_max_offsets"], f["offsets_reachable"], f["offsets"] = f.p50, f.p50, (~f.unreachable).astype(int), 1
        return f
    key = ["from_id", "to_id"]
    cols = [c for c in MEAN_COLS if c in frames[0]]
    allf = pd.concat([f[key + cols + ["unreachable"]].assign(_k=i) for i, f in enumerate(frames)], ignore_index=True)
    ok = allf[~allf.unreachable]
    g = ok.groupby(key, sort=False)
    out = g[cols].mean()
    out["p50_min_offsets"], out["p50_max_offsets"] = g.p50.min(), g.p50.max()
    out["offsets_reachable"] = g.size()
    out = out.reset_index()
    pairs = allf[key].drop_duplicates()
    out = pairs.merge(out, on=key, how="left")
    out["offsets_reachable"] = out.offsets_reachable.fillna(0).astype(int)
    out["offsets"] = n
    out["unreachable"] = out.offsets_reachable < n
    tr = frames[0][key + ["top_routes"]] if "top_routes" in frames[0] else None
    return out.merge(tr, on=key, how="left") if tr is not None else out


def jobs_within(pt: pd.DataFrame, jobs: pd.Series, thresholds: list[int]) -> pd.DataFrame:
    """Jobs reachable by PT within each threshold (minutes, on the pair's median total
    time), per origin. ``jobs`` is indexed by destination."""
    ok = pt[~pt.unreachable]
    j = ok.to_id.map(jobs).fillna(0.0)
    return pd.DataFrame({f"pt_jobs_{t}": j.where(ok.p50 <= t, 0.0).groupby(ok.from_id).sum() for t in thresholds})


def access_summary(acc: pd.DataFrame, residents: pd.Series, edge: pd.Series, thresholds: list[int]) -> dict:
    a = acc.reindex(residents.index).fillna(0.0)
    out = {}
    for t in thresholds:
        v = a[f"pt_jobs_{t}"]
        out[f"jobs_{t}min"] = {"median_core_oa": float(v[~edge].median()), "median_edge_oa": float(v[edge].median()),
                               "resident_weighted_mean": float((v * residents).sum() / residents.sum())}
    return out


def diff(a, b):
    """b − a through nested dicts of numbers."""
    if isinstance(a, dict):
        return {k: diff(a[k], b[k]) for k in a if k in b}
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round(b - a, 4)
    return None


def skim_diff(parent: pd.DataFrame, scen: pd.DataFrame) -> dict:
    j = parent[["from_id", "to_id", "p50", "unreachable"]].merge(
        scen[["from_id", "to_id", "p50", "unreachable", "p50_min_offsets", "p50_max_offsets"]],
        on=["from_id", "to_id"], suffixes=("_parent", ""))
    both = j[~j.unreachable_parent & ~j.unreachable]
    d = both.p50 - both.p50_parent
    rng = both.p50_max_offsets - both.p50_min_offsets
    return {"pairs_reachable_parent": int((~j.unreachable_parent).sum()), "pairs_reachable": int((~j.unreachable).sum()),
            "pairs_gained": int((j.unreachable_parent & ~j.unreachable).sum()),
            "pairs_lost": int((~j.unreachable_parent & j.unreachable).sum()),
            "p50_change_min": {"mean": round(float(d.mean()), 3), "p1": float(d.quantile(.01)), "p5": float(d.quantile(.05)),
                               "p95": float(d.quantile(.95))},
            "pairs_2min_faster": int((d <= -2).sum()), "pairs_2min_slower": int((d >= 2).sum()),
            "pairs_5min_faster": int((d <= -5).sum()),
            "offset_range_min": {"mean": round(float(rng.mean()), 3), "p95": float(rng.quantile(.95)),
                                 "max": float(rng.max())},
            "largest_gains": both.assign(d=d).nsmallest(8, "d")[["from_id", "to_id", "p50_parent", "p50", "d"]]
            .round(1).to_dict("records")}
