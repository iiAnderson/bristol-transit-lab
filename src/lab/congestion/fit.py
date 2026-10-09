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
                 key: str = "road", delay_s: dict[str, np.ndarray] | None = None) -> pd.Series:
    """seg: length_m, w, ff_kmh, road key; factors: period -> per-segment factor array;
    delay_s: optional period -> per-segment fixed delay (s), e.g. at a signalised
    approach. Time on a segment = L / v + delay."""
    L = seg["length_m"].to_numpy()
    w = seg["w"].to_numpy()
    # Σ_p P_p · time_p per unit weight, in hours; time_p = L/1000 / v_p + d_p/3600
    t = sum(P[p] * (L / 1000 / (seg["ff_kmh"].to_numpy() * factors[p])
                    + (0 if delay_s is None else delay_s[p] / 3600)) for p in PERIODS)
    df = pd.DataFrame({key: seg[key].to_numpy(), "lw": L / 1000 * w, "lwinv": w * t})
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
              areas: list[str], lam: float = 1.0, road_lam: float | None = None,
              authority: bool = True, delay_s: dict | None = None) -> dict:
    """Fit g[area] (the base level), optionally A[authority] (ridge ``lam``) and, if
    ``road_lam`` is given, a multiplier per target road (ridge ``road_lam``) so modelled
    all-day road speeds match ``targets`` (indexed by road key).

    Returns parameters, residuals, parameter count and effective degrees of freedom
    (trace of the hat matrix J (JᵀJ + Λ)⁻¹ Jᵀ at the solution; unpenalised parameters
    count 1 each, ridged ones less)."""
    keep = seg["road"].isin(targets.index).to_numpy()
    seg = seg[keep].copy()
    if delay_s is not None:
        delay_s = {p: np.asarray(v)[keep] for p, v in delay_s.items()}
    auths = sorted(seg["authority"].unique()) if authority else []
    roads = list(targets.index) if road_lam is not None else []
    na, nA, nr = len(areas), len(auths), len(roads)

    def unpack(x):
        g = dict(zip(areas, np.exp(x[:na])))
        A = dict(zip(auths, np.exp(x[na:na + nA]))) if auths else \
            {a: 1.0 for a in seg["authority"].unique()}
        m = dict(zip(roads, np.exp(x[na + nA:])))
        return g, A, m

    def data_resid(x):
        g, A, m = unpack(x)
        s = allday_speed(seg, segment_factors(seg, g, A, r, rho, m), P, delay_s=delay_s)
        return np.log(s.reindex(targets.index).to_numpy()) - np.log(targets.to_numpy())

    def resid(x):
        pen = []
        if nA:
            pen.append(np.sqrt(lam) * x[na:na + nA])
        if nr:
            pen.append(np.sqrt(road_lam) * x[na + nA:])
        return np.concatenate([data_resid(x), *pen])

    x0 = np.zeros(na + nA + nr)
    sol = least_squares(resid, x0)
    g, A, m = unpack(sol.x)
    s = allday_speed(seg, segment_factors(seg, g, A, r, rho, m), P,
                     delay_s=delay_s).reindex(targets.index)
    err = s / targets - 1
    # effective degrees of freedom
    J = sol.jac[:len(targets)]
    Lam = np.diag(np.r_[np.zeros(na), np.full(nA, lam), np.full(nr, road_lam or 0.0)])
    H = J @ np.linalg.pinv(J.T @ J + Lam) @ J.T
    return {"g": g, "A": A if auths else {}, "m": m, "modelled": s, "rel_error": err,
            "n_params": na + nA + nr, "edf": float(np.trace(H)), "n_targets": len(targets),
            "max_abs_rel_error": float(err.abs().max()), "success": bool(sol.success)}


def leave_one_road_out(seg: pd.DataFrame, targets: pd.Series, r: dict, rho: dict,
                       P: dict, areas: list[str], delay_s: dict | None = None,
                       post_mult: dict | None = None,
                       post_delay: dict | None = None) -> pd.DataFrame:
    """Fit the base (area levels only) without road k, predict k; one row per road.
    ``post_mult`` / ``post_delay`` (period -> per-segment arrays) are a layer applied to
    the prediction only, after the fit (the ANPR layer)."""
    rows = []
    for k in targets.index:
        f = fit_level(seg, targets.drop(k), r, rho, P, areas, authority=False, delay_s=delay_s)
        sel = (seg["road"] == k).to_numpy()
        one = seg[sel]
        d1 = None if delay_s is None else {p: np.asarray(v)[sel] for p, v in delay_s.items()}
        fac = segment_factors(one, f["g"], {a: 1.0 for a in one["authority"]}, r, rho)
        if post_mult is not None:
            fac = {p: fac[p] * np.asarray(post_mult[p])[sel] for p in fac}
        if post_delay is not None:
            d1 = {p: (0 if d1 is None else d1[p]) + np.asarray(post_delay[p])[sel] for p in fac}
        pred = allday_speed(one, fac, P, delay_s=d1)[k]
        rows.append({"road": k, "dft_kmh": float(targets[k]), "predicted_kmh": float(pred),
                     "rel_error": float(pred / targets[k] - 1)})
    return pd.DataFrame(rows)
