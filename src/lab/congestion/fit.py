"""
The P2b level fit (plans/P2.md §5 "Fit, concretely").

A road's modelled all-day speed, like for like with DfT (flow-weighted harmonic mean
over every hour of the week):

    S_road = Σ_seg L·w  ÷  Σ_seg L·w · Σ_p P_p / v_seg,p

where w is the segment's flow weight (AADF), P_p the share of the week's traffic in
period p (from TRA0307: weekday AM, IP, PM, off-peak, and the weekend), and
v_seg,p = free-flow × factor.

Local-A factor: F[p][dir][area] × A[authority], with F[IP] = g[area] (fitted),
F[AM|PM] = g · r[p][dir][area] (bus shape, given), F[OP] = g · ρ_OP, F[WE] = g · ρ_WE
(given). Unknowns: g per area type and a multiplier per authority, fitted by least
squares on log speed with a ridge penalty pulling each log A towards 0.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

PERIODS = ["AM", "IP", "PM", "OP", "WE"]


def period_weights(profile: pd.DataFrame, periods: dict[str, tuple[str, str]]) -> dict[str, float]:
    """Shares of the week's traffic by period, from an hour × day-of-week profile."""
    out = {p: 0.0 for p in PERIODS}
    for _, r in profile.iterrows():
        if r["dow"] >= 5:
            out["WE"] += r["share"]
            continue
        t = dt.time(int(r["hour"]))
        for p, (a, b) in periods.items():
            if dt.time.fromisoformat(a) <= t < dt.time.fromisoformat(b):
                out[p] += r["share"]
                break
        else:
            out["OP"] += r["share"]
    return out


def allday_speed(seg: pd.DataFrame, factors: dict[str, np.ndarray], P: dict[str, float],
                 key: str = "road") -> pd.Series:
    """seg: length_m, w, ff_kmh, road key; factors: period -> per-segment factor array."""
    inv = sum(P[p] / (seg["ff_kmh"].to_numpy() * factors[p]) for p in PERIODS)
    lw = seg["length_m"].to_numpy() * seg["w"].to_numpy()
    df = pd.DataFrame({key: seg[key].to_numpy(), "lw": lw, "lwinv": lw * inv})
    g = df.groupby(key).sum()
    return g["lw"] / g["lwinv"]


def segment_factors(seg: pd.DataFrame, g: dict[str, float], A: dict[str, float],
                    r: dict[tuple, float], rho: dict[str, float],
                    m: dict[str, float] | None = None) -> dict[str, np.ndarray]:
    """Per-segment factor arrays for each period (local A roads); ``m`` optional per-road
    multipliers."""
    base = seg["area"].map(g).to_numpy() * seg["authority"].map(A).to_numpy()
    if m:
        base = base * seg["road"].map(m).fillna(1.0).to_numpy()
    out = {"IP": base, "OP": base * rho["OP"], "WE": base * rho["WE"]}
    for p in ("AM", "PM"):
        ratio = np.array([r.get((p, d, a), 1.0) for d, a in zip(seg["direction"], seg["area"])])
        out[p] = base * ratio
    return out


def fit_level(seg: pd.DataFrame, targets: pd.Series, r: dict, rho: dict, P: dict,
              areas: list[str], lam: float = 1.0, road_lam: float | None = None) -> dict:
    """Fit g[area] and A[authority] (and, if ``road_lam`` is given, a multiplier per
    target road with its own ridge) so modelled all-day road speeds match ``targets``
    (indexed by road key). Returns parameters, residuals and diagnostics."""
    seg = seg[seg["road"].isin(targets.index)].copy()
    auths = sorted(seg["authority"].unique())
    roads = list(targets.index) if road_lam is not None else []
    na, nA = len(areas), len(auths)

    def unpack(x):
        g = dict(zip(areas, np.exp(x[:na])))
        A = dict(zip(auths, np.exp(x[na:na + nA])))
        m = dict(zip(roads, np.exp(x[na + nA:])))
        return g, A, m

    def resid(x):
        g, A, m = unpack(x)
        s = allday_speed(seg, segment_factors(seg, g, A, r, rho, m), P)
        res = np.log(s.reindex(targets.index).to_numpy()) - np.log(targets.to_numpy())
        pen = [np.sqrt(lam) * x[na:na + nA]]
        if roads:
            pen.append(np.sqrt(road_lam) * x[na + nA:])
        return np.concatenate([res, *pen])

    x0 = np.zeros(na + nA + len(roads))
    sol = least_squares(resid, x0)
    g, A, m = unpack(sol.x)
    s = allday_speed(seg, segment_factors(seg, g, A, r, rho, m), P).reindex(targets.index)
    err = s / targets - 1
    return {"g": g, "A": A, "m": m, "modelled": s, "rel_error": err,
            "n_params": na + nA + len(roads), "n_targets": len(targets),
            "max_abs_rel_error": float(err.abs().max()), "success": bool(sol.success)}
