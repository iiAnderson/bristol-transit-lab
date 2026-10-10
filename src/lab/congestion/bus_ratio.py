"""
``bus_speed_ratio`` (SPEC §5 as amended at P3; plans/P3.md Q3, P3b-7): the speed of a bus
moving in traffic as a fraction of the calibrated car speed on the same link, direction
and period, by road class × area type × period.

It is for **new** on-street bus and BRT sections and for ``road_speed_factor``: a
generated bus leg runs at car speed × ratio between stops, plus its dwell. The bus speeds
are P2's moving speeds (dwell legs near stops already dropped), so the ratio excludes
dwell. Links with bus-lane tags are left out of the fit: a bus lane is what
``road_speed_factor`` is for.

The fit is time-consistent: for a group of cells (link × direction × period),
k = Σ car time ÷ Σ bus time, so that car speed × k reproduces the group's total bus running
time. It is validated on spatial hold-outs (blocks of ``block_km``): the ratio fitted
without a block predicts that block's bus speeds.

One caveat is built in: P2 used these same bus speeds to shape the car speeds by period,
so the two sides are not independent, and the ratio inherits P2's held-out bus error.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def cells(trav: pd.DataFrame, min_obs: int) -> pd.DataFrame:
    """Bus speed per link × direction × period: the harmonic mean of its traversals (a
    time-mean, as a journey experiences it), where there are at least ``min_obs``."""
    t = trav[trav.kmh > 0]
    g = t.assign(inv=1 / t.kmh).groupby(["u", "v", "period"])
    out = pd.DataFrame({"n": g.size(), "bus_kmh": g.size() / g.inv.sum()}).reset_index()
    return out[out.n >= min_obs]


def join(cell: pd.DataFrame, car: pd.DataFrame, seg: pd.DataFrame) -> pd.DataFrame:
    """Cells with the calibrated car speed and the segment's class, area, length, block."""
    c = cell.merge(car[["u", "v", "period", "speed_kmh"]].rename(columns={"speed_kmh": "car_kmh"}),
                   on=["u", "v", "period"])
    s = seg[["u", "v", "length_m", "road_class", "area_type", "bus_flag", "mlon", "mlat"]].drop_duplicates(["u", "v"])
    c = c.merge(s, on=["u", "v"])
    c["bus_h"], c["car_h"] = c.length_m / 1000 / c.bus_kmh, c.length_m / 1000 / c.car_kmh
    return c


def fit(c: pd.DataFrame, keys=("road_class", "area_type", "period")) -> pd.DataFrame:
    g = c.groupby(list(keys))
    out = pd.DataFrame({"ratio": g.car_h.sum() / g.bus_h.sum(), "cells": g.size(),
                        "km": g.length_m.sum() / 1000, "traversals": g.n.sum()}).reset_index()
    return out


def blocks(c: pd.DataFrame, block_km: float) -> pd.Series:
    bx = np.floor(c.mlon * 111.32 * np.cos(np.radians(c.mlat.mean())) / block_km).astype(int)
    by = np.floor(c.mlat * 111.32 / block_km).astype(int)
    return bx.astype(str) + "_" + by.astype(str)


def hold_out(c: pd.DataFrame, block_km: float, folds: int, min_cells: int, seed: int = 0) -> dict:
    """Spatial k-fold: blocks are dealt into ``folds`` groups; each group's cells are
    predicted (car speed × ratio) from a fit on the others. Returns the per-cell error of
    the predicted bus speed, overall and by period, and the share of held-out cells whose
    group had too few training cells and fell back to the period's overall ratio."""
    c = c.assign(block=blocks(c, block_km))
    ids = np.array(sorted(c.block.unique()))
    rng = np.random.default_rng(seed)
    fold_of = dict(zip(ids, rng.permutation(len(ids)) % folds))
    c["fold"] = c.block.map(fold_of)
    parts = []
    for f in range(folds):
        tr, te = c[c.fold != f], c[c.fold == f].copy()
        k = fit(tr)
        k = k[k.cells >= min_cells]
        kp = fit(tr, ("period",)).set_index("period").ratio
        te = te.merge(k[["road_class", "area_type", "period", "ratio"]], on=["road_class", "area_type", "period"], how="left")
        te["fallback"] = te.ratio.isna()
        te["ratio"] = te.ratio.fillna(te.period.map(kp))
        parts.append(te)
    t = pd.concat(parts, ignore_index=True)
    t["err"] = (t.car_kmh * t.ratio - t.bus_kmh).abs() / t.bus_kmh
    t["signed"] = (t.car_kmh * t.ratio - t.bus_kmh) / t.bus_kmh
    # What a generated route needs is running time over a stretch of road, not the speed on
    # one link: the same prediction summed over each held-out block and period.
    t["pred_h"] = t.length_m / 1000 / (t.car_kmh * t.ratio)
    b = t.groupby(["block", "period"]).agg(pred_h=("pred_h", "sum"), bus_h=("bus_h", "sum"), car_h=("car_h", "sum"),
                                           km=("length_m", lambda x: x.sum() / 1000), cells=("u", "size"))
    b = b[b.km >= 1.0]                                    # at least a kilometre of observed link-direction
    be, b1 = (b.pred_h - b.bus_h) / b.bus_h, (b.car_h - b.bus_h) / b.bus_h
    block = {"block_periods": int(len(b)), "median_abs_time_error": round(float(be.abs().median()), 4),
             "p90_abs_time_error": round(float(be.abs().quantile(.9)), 4),
             "median_signed_time_error": round(float(be.median()), 4),
             "total_time_error": round(float(b.pred_h.sum() / b.bus_h.sum() - 1), 4),
             "median_abs_time_error_if_ratio_1": round(float(b1.abs().median()), 4),
             "median_signed_time_error_if_ratio_1": round(float(b1.median()), 4),
             "total_time_error_if_ratio_1": round(float(b.car_h.sum() / b.bus_h.sum() - 1), 4)}
    res = {"cells": int(len(t)), "blocks": int(len(ids)), "folds": folds, "running_time_by_block": block,
           "median_abs_error": round(float(t.err.median()), 4), "p90_abs_error": round(float(t.err.quantile(.9)), 4),
           "median_signed_error": round(float(t.signed.median()), 4),
           "share_fallback": round(float(t.fallback.mean()), 4),
           "by_period": {p: round(float(g.err.median()), 4) for p, g in t.groupby("period")},
           "by_area": {p: round(float(g.err.median()), 4) for p, g in t.groupby("area_type")},
           # the alternative of using the car speed itself (ratio 1), for scale
           "median_abs_error_if_ratio_1": round(float(((t.car_kmh - t.bus_kmh).abs() / t.bus_kmh).median()), 4)}
    return res
