"""
How fast is the speed-profile rule against the timetable? (plans/P3.md, P3b stop
decision 1c.)

For every pair of stations that today's trains serve as consecutive calls, two times:

* the timetable's — the median working time over the operator's trains calling at both
  (the D8 rule the generator uses on existing track);
* the speed-profile rule's — accelerate, run at the line speed OSM tags along the track
  between the two stations, brake.

Where both exist the difference is what the profile leaves out: timetable allowances,
junction margins, running below the limit. It is fitted as
``timetable = (1 + pct) × profile + per_junction × junctions`` and that allowance is then
applied to every speed-profile leg the generator writes.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def profile_time_s(dist_m: float, line_kmh: float, accel_ms2: float) -> float:
    """The generator's rule (``generator.leg_time_s``) at a line speed."""
    v = line_kmh / 3.6
    if dist_m >= v * v / accel_ms2:
        return dist_m / v + v / accel_ms2
    return 2 * math.sqrt(dist_m / accel_ms2)


def sections(tp: pd.DataFrame, min_trains: int) -> pd.DataFrame:
    """Consecutive public calls of trains in the passenger timetable: from, to, operator,
    trains, the median working run time, and the usual number of passing points between
    (all of them, and those whose code marks a junction)."""
    t = tp[tp.exclusion.isna() & ~tp.journey_cancelled & ~tp.cancelled].sort_values(["rid", "seq"])
    rows = []
    for rid, g in t.groupby("rid", sort=False):
        calls = g[g.public]
        idx = list(calls.index)
        pos = {i: k for k, i in enumerate(g.index)}
        tpl, kind = g.tpl.to_numpy(), g.kind.to_numpy()
        for a, b in zip(idx, idx[1:]):
            between = range(pos[a] + 1, pos[b])
            pps = [tpl[k] for k in between if kind[k] == "PP"]
            rows.append((g.tpl[a], g.tpl[b], g.toc[a], g.arr_s[b] - g.dep_s[a], len(pps),
                         sum(p.endswith(("J", "JN", "JCN")) for p in pps)))
    d = pd.DataFrame(rows, columns=["from", "to", "toc", "run_s", "pp", "junctions"]).dropna(subset=["run_s"])
    g = d.groupby(["from", "to", "toc"])
    out = pd.DataFrame({"trains": g.size(), "timetable_s": g.run_s.median(), "iqr_s": g.run_s.quantile(.75) - g.run_s.quantile(.25),
                        "pp": g.pp.median(), "junctions": g.junctions.median()}).reset_index()
    return out[out.trains >= min_trains]


def with_profile(sec: pd.DataFrame, coords: dict, graph, accel_ms2: float, snap_m: float, min_tagged: float) -> pd.DataFrame:
    """Add the track distance, OSM line speed and profile time; sections whose track
    cannot be routed, or whose line speed is tagged on less than ``min_tagged`` of the
    way, are dropped and counted by the caller."""
    from ..supply import rail_geometry as rg
    cache: dict = {}
    rows = []
    for r in sec.itertuples():
        a, b = getattr(r, "_1"), r.to
        if a not in coords or b not in coords:
            continue
        if (a, b) not in cache:
            try:
                cache[(a, b)] = rg.path(graph, [coords[a], coords[b]], 2 * snap_m, snap_m)
            except Exception:
                cache[(a, b)] = None
        p = cache[(a, b)]
        if p is None or not p["maxspeed_kmh"] or p["maxspeed_tagged_m"] < min_tagged * p["length_m"]:
            continue
        rows.append({"from": a, "to": b, "toc": r.toc, "trains": r.trains, "timetable_s": r.timetable_s,
                     "pp": r.pp, "junctions": r.junctions, "dist_m": p["length_m"], "line_kmh": p["maxspeed_kmh"],
                     "profile_s": profile_time_s(p["length_m"], p["maxspeed_kmh"], accel_ms2)})
    return pd.DataFrame(rows)


def fit(d: pd.DataFrame) -> dict:
    """Least squares, weighted by trains, of timetable = (1 + pct) × profile + per_junction ×
    junctions, with per_junction held at zero or above."""
    w = np.sqrt(d.trains.to_numpy(float))
    X = np.c_[d.profile_s, d.junctions]
    coef, *_ = np.linalg.lstsq(X * w[:, None], d.timetable_s.to_numpy() * w, rcond=None)
    if coef[1] < 0:
        k = float((w * w * d.profile_s * d.timetable_s).sum() / (w * w * d.profile_s ** 2).sum())
        coef = np.array([k, 0.0])
    return {"pct": float(coef[0] - 1), "per_junction_s": float(coef[1])}


def apply(profile_s, junctions, f: dict):
    return (1 + f["pct"]) * np.asarray(profile_s, float) + f["per_junction_s"] * np.asarray(junctions, float)


def evaluate(d: pd.DataFrame, folds: int, seed: int = 0) -> dict:
    """Section-level hold-out: each undirected station pair is predicted from a fit on the
    other folds. Errors of the plain profile and of the profile with the allowance."""
    pair = d.apply(lambda r: "|".join(sorted((r["from"], r["to"]))), axis=1)
    ids = np.array(sorted(pair.unique()))
    fold = dict(zip(ids, np.random.default_rng(seed).permutation(len(ids)) % folds))
    f = pair.map(fold)
    pred = pd.Series(index=d.index, dtype=float)
    for k in range(folds):
        pred[f == k] = apply(d.profile_s[f == k], d.junctions[f == k], fit(d[f != k]))
    e0 = (d.profile_s - d.timetable_s) / d.timetable_s
    e1 = (pred - d.timetable_s) / d.timetable_s
    w = d.trains
    return {"sections": int(len(d)), "station_pairs": int(len(ids)),
            "plain_profile": {"median_signed_error": round(float(e0.median()), 4), "median_abs_error": round(float(e0.abs().median()), 4),
                              "total_time_error": round(float((d.profile_s * w).sum() / (d.timetable_s * w).sum() - 1), 4),
                              "share_faster_than_timetable": round(float((e0 < 0).mean()), 3)},
            "with_allowance_held_out": {"median_signed_error": round(float(e1.median()), 4),
                                        "median_abs_error": round(float(e1.abs().median()), 4),
                                        "p90_abs_error": round(float(e1.abs().quantile(.9)), 4),
                                        "total_time_error": round(float((pred * w).sum() / (d.timetable_s * w).sum() - 1), 4)}}
