"""
P2b calibration pipeline (plans/P2.md §5 "as built" and "Fit, concretely").

Steps, each recorded in the result:
1. free flow per segment (OSM maxspeed, else tagged defaults);
2. SRN factors from WebTRIS (matched segment: its site; others: nearest site on the same
   road and carriageway; else the SRN mean for the period — Welsh trunk roads included,
   [MODELLED] per D8);
3. local-road shape from bus moving speeds (ratios to IP by period × direction × area ×
   class group), falling back to DfT national ratios where a cell is thin;
4. off-peak and weekend ties: OP/IP from DfT's national weekday split, WE/IP from
   WebTRIS, both [MODELLED] for local roads;
5. level: IP factor by area type × authority multiplier, fitted to DfT all-day speeds per
   authority × road (covered targets only);
6. B-road and minor levels from bus-speed ratios to local A in the same area;
7. per-segment speeds for AM, IP, PM, OP, WE → ``link_speed`` and OSRM speed files.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import fit

AREAS = ["centre", "urban", "rural"]


def free_flow(seg: pd.DataFrame, p: dict) -> np.ndarray:
    wales = seg["lad"].fillna("").str.startswith("W") | seg["lsoa"].fillna("").str.startswith("W")
    urban = seg["area_type"].isin(["centre", "urban"])
    service = seg["highway"].isin(["service", "living_street"])
    main = seg["highway"].str.replace("_link", "").isin(
        ["motorway", "trunk", "primary", "secondary", "tertiary"])
    default = np.select(
        [service, urban & wales, urban, main],
        [p["service_kmh"], p["urban_wales_kmh"], p["urban_england_kmh"], p["rural_main_kmh"]],
        p["rural_minor_kmh"])
    return np.where(seg["maxspeed_kmh"].notna(), seg["maxspeed_kmh"], default).astype(float)


def _bearing(a):
    return np.degrees(np.arctan2(
        np.sin(np.radians(a["lon_v"] - a["lon_u"])) * np.cos(np.radians(a["lat_v"])),
        np.cos(np.radians(a["lat_u"])) * np.sin(np.radians(a["lat_v"]))
        - np.sin(np.radians(a["lat_u"])) * np.cos(np.radians(a["lat_v"]))
        * np.cos(np.radians(a["lon_v"] - a["lon_u"])))) % 360


def srn_factors(seg: pd.DataFrame, wspeed: pd.DataFrame, wsites: pd.DataFrame,
                periods: list[str], max_km: float = 10.0) -> tuple[pd.DataFrame, dict]:
    """Per SRN segment: factor per period and its source (site | nearest_site | srn_mean)."""
    srn = seg[seg["road_class"] == "srn"].copy()
    srn["bearing"] = _bearing(srn)
    wsites = wsites.astype({"lon": float, "lat": float})
    s = wsites.dropna(subset=["u"]).merge(
        srn[["u", "v", "ff", "bearing"]], on=["u", "v"], how="inner")
    w = wspeed.pivot_table(index="site_id", columns="period", values="kmh_hmean")
    s = s.merge(w, left_on="site_id", right_index=True, how="inner")
    for p in periods:
        s[f"f_{p}"] = s[p] / s["ff"]
    means = {p: float(s[f"f_{p}"].median()) for p in periods}
    out = {p: np.full(len(srn), np.nan) for p in periods}
    src = np.full(len(srn), "srn_mean", dtype=object)
    mx, my = ((srn["lon_u"] + srn["lon_v"]) / 2).to_numpy(), ((srn["lat_u"] + srn["lat_v"]) / 2).to_numpy()
    sx, sy = s["lon"].to_numpy(), s["lat"].to_numpy()
    for ref in srn["ref"].dropna().unique():
        idx = np.where(srn["ref"].to_numpy() == ref)[0]
        cand = np.where(s["road"].to_numpy() == ref)[0]
        if not len(cand):
            continue
        dx = (mx[idx, None] - sx[None, cand]) * 111.32 * math.cos(math.radians(51.45))
        dy = (my[idx, None] - sy[None, cand]) * 111.32
        dist = np.hypot(dx, dy)
        bdiff = np.abs((srn["bearing"].to_numpy()[idx, None] - s["bearing"].to_numpy()[None, cand]
                        + 180) % 360 - 180)
        dist[bdiff >= 90] = np.inf
        j = dist.argmin(axis=1)
        dmin = dist[np.arange(len(idx)), j]
        ok = dmin <= max_km
        for p in periods:
            vals = s[f"f_{p}"].to_numpy()[cand][j]
            out[p][idx[ok]] = vals[ok]
        src[idx[ok]] = np.where(dmin[ok] < 0.05, "site", "nearest_site")
    for p in periods:
        m = np.isnan(out[p])
        out[p][m] = means[p]
    res = srn[["u", "v"]].copy()
    for p in periods:
        res[p] = out[p]
    res["source"] = src
    return res, {"sites_used": len(s), "srn_mean_factor": means,
                 "srn_km_by_source": res.assign(km=srn["length_m"].to_numpy() / 1000)
                 .groupby("source")["km"].sum().round(1).to_dict()}


def _cell_speeds(trav: pd.DataFrame) -> pd.DataFrame:
    agg = trav.groupby(["u", "v", "period"]).agg(n=("leg", "nunique"),
                                                  hm=("kmh", lambda x: len(x) / (1 / x).sum()))
    agg = agg.reset_index().pivot_table(index=["u", "v"], columns="period", values=["n", "hm"])
    agg.columns = [f"{a}_{b}" for a, b in agg.columns]
    return agg


def bus_shape(seg: pd.DataFrame, trav: pd.DataFrame, min_obs: int, min_cells: int,
              national: dict[str, float],
              trav_pm: pd.DataFrame | None = None) -> tuple[dict, dict, dict]:
    """r[(p, dir, area, group)] = median bus speed ratio p/IP; class level ratios.
    AMPH (the 08:00–09:00 skim hour) comes from traversals whose leg started in hour 8.
    ``trav_pm`` (the live 07:00–19:00 days) gives the PM/IP ratio, both periods from the
    same days and feed; the archive (07:00–16:00) has no PM data."""
    if "hour" in trav.columns:
        trav = pd.concat([trav, trav[trav["hour"] == 8].assign(period="AMPH")],
                         ignore_index=True)
    base = seg[seg["road_class"].isin(["local_a", "b_road", "minor"]) & ~seg["bus_flag"]].copy()
    base["area"] = base["area_type"].replace({"buffer": "rural"})
    base["group"] = np.where(base["road_class"] == "minor", "minor", "main")
    loc = base.merge(_cell_speeds(trav), left_on=["u", "v"], right_index=True, how="inner")
    loc_pm = None if trav_pm is None or trav_pm.empty else \
        base.merge(_cell_speeds(trav_pm), left_on=["u", "v"], right_index=True, how="inner")
    r, diag = {}, {}
    for p in ("AM", "PM", "AMPH"):
        # A period without bus data falls back to the national ratio in every cell.
        src, tag = (loc_pm, "bus_live") if p == "PM" and loc_pm is not None else (loc, "bus")
        if f"hm_{p}" in src.columns and "hm_IP" in src.columns:
            ok = (src[f"n_{p}"].fillna(0) >= min_obs) & (src["n_IP"].fillna(0) >= min_obs)
            d = src[ok].assign(ratio=lambda x: x[f"hm_{p}"] / x["hm_IP"])
            for (direction, area, group), g in d.groupby(["direction", "area", "group"]):
                if len(g) >= min_cells:
                    r[(p, direction, area, group)] = float(g["ratio"].median())
                    diag[(p, direction, area, group)] = (tag, len(g))
        for direction in ("inbound", "outbound"):
            for area in AREAS:
                for group in ("main", "minor"):
                    if (p, direction, area, group) not in r:
                        r[(p, direction, area, group)] = national["AM" if p == "AMPH" else p]
                        diag[(p, direction, area, group)] = ("national_fallback", 0)
    ok = loc["n_IP"].fillna(0) >= min_obs if "n_IP" in loc.columns else loc["u"] < 0
    lev = loc[ok].assign(bf=lambda x: x["hm_IP"] / x["ff"])
    level = {}
    for area in AREAS:
        base = lev[(lev["road_class"] == "local_a") & (lev["area"] == area)]["bf"].median()
        for cls in ("b_road", "minor"):
            v = lev[(lev["road_class"] == cls) & (lev["area"] == area)]["bf"]
            level[(cls, area)] = (float(v.median() / base), len(v)) \
                if len(v) >= min_cells and base == base else (1.0, 0)
    return r, diag, level


def dft_national_ratios(cgn0503: str, months: list[str]) -> dict[str, float]:
    """England weekday AM/IP, PM/IP and OP/IP speed ratios over the given months."""
    x = pd.read_excel(cgn0503, engine="odf", sheet_name="CGN0503a", header=3)
    x = x[x["Month"].isin(months)]
    col = lambda k: [c for c in x.columns if str(c).startswith(k)][0]  # noqa: E731
    am, ip = x[col("Weekday morning peak")].astype(float), x[col("Weekday inter peak")].astype(float)
    pm, op = x[col("Weekday evening peak")].astype(float), x[col("Weekday off peak")].astype(float)
    return {"AM": float(am.mean() / ip.mean()), "PM": float(pm.mean() / ip.mean()),
            "OP": float(op.mean() / ip.mean())}


def run(seg: pd.DataFrame, trav: pd.DataFrame, wspeed: pd.DataFrame, wsites: pd.DataFrame,
        targets: pd.DataFrame, cov: pd.DataFrame, P: dict, p: dict, ff_p: dict,
        national: dict, min_coverage: float, lam: float,
        road_lam: float, anpr: dict | None = None,
        trav_pm: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (hybrid link_speed, base link_speed, report). ``anpr`` (paths, obs,
    signals) turns on the ANPR layer fitted on the ANPR calibration half."""
    periods = fit.PERIODS
    out_periods = periods + ["AMPH"]
    seg = seg.copy()
    seg["aadf_2way"] = pd.to_numeric(seg["aadf_2way"], errors="coerce").astype(float)
    cov = cov.copy()
    for c in ("km_total", "km_inside"):
        cov[c] = pd.to_numeric(cov[c], errors="coerce").astype(float)
    seg["ff"] = free_flow(seg, ff_p)
    rep: dict = {"segments": len(seg)}

    # 2. SRN
    srn, rep["srn"] = srn_factors(seg, wspeed, wsites,
                                  [q for q in out_periods if q in set(wspeed["period"])])

    # 3–4. local shape and ties
    r, rdiag, level = bus_shape(seg, trav, p["min_obs_per_link"], p["min_cells"], national,
                                trav_pm)
    w = wspeed.pivot_table(index="site_id", columns="period", values="kmh_hmean")
    rho = {"OP": national["OP"], "WE": float((w["WE"] / w["IP"]).median())}
    rep["shape"] = {"r": {"|".join(k): round(v, 4) for k, v in r.items()},
                    "r_source": {"|".join(k): v for k, v in rdiag.items()},
                    "class_level": {"|".join(k): (round(v[0], 4), v[1]) for k, v in level.items()},
                    "rho": rho, "national": national}

    # 5. level fit on covered targets
    t = targets.merge(cov.rename(columns={"road_ref": "ref"}), on=["lad", "ref"], how="left")
    t["coverage"] = t["km_inside"] / t["km_total"]
    use = t[t["coverage"] >= min_coverage].copy()
    use["road"] = use["lad"] + ":" + use["ref"]
    la = seg[seg["road_class"] == "local_a"].copy()
    la["road"] = la["lad"].fillna("") + ":" + la["ref"].fillna("")
    la["area"] = la["area_type"].replace({"buffer": "rural"})
    la["authority"] = la["lad"].fillna("none")
    road_w = la.groupby("road")["aadf_2way"].transform("median")
    la["w"] = la["aadf_2way"].fillna(road_w).fillna(1.0) / 2
    la["ff_kmh"] = la["ff"]
    r_main = {(k[0], k[1], k[2]): v for k, v in r.items() if k[3] == "main"}
    tgt = use.set_index("road")["kmh"]
    tgt = tgt[tgt.index.isin(la["road"])]
    sig = anpr["signals"] if anpr else set()
    scope = anpr.get("scope") if anpr else None
    base = fit.fit_level(la, tgt, r_main, rho, P, AREAS, authority=False)
    lf = fit.fit_level(la, tgt, r_main, rho, P, AREAS, authority=False, road_lam=road_lam)
    loro = fit.leave_one_road_out(la, tgt, r_main, rho, P, AREAS)
    e0, e1, el = base["rel_error"].abs(), lf["rel_error"].abs(), loro["rel_error"].abs()
    n_shape_cells = len(r) + 2 * len(AREAS) + 2          # AM/PM cells, class levels, OP/WE
    rep["base"] = {"g": base["g"], "n_params_fitted_to_dft": base["n_params"],
                   "edf": base["edf"], "n_shape_cells_from_data": n_shape_cells,
                   "median_abs_rel_error": float(e0.median()),
                   "p90_abs_rel_error": float(e0.quantile(0.9)),
                   "share_within_5pct": float((e0 <= 0.05).mean())}
    rep["leave_one_road_out"] = {"median_abs_rel_error": float(el.median()),
                                 "p90_abs_rel_error": float(el.quantile(0.9)),
                                 "share_within_15pct": float((el <= 0.15).mean()),
                                 "passes_15pct_median": bool(el.median() <= 0.15),
                                 "roads": loro.round(4).to_dict("records")}
    rep["level"] = {"g": lf["g"], "m": lf["m"], "n_params": lf["n_params"],
                    "edf": lf["edf"],
                    "median_abs_rel_error": float(e1.median()),
                    "p90_abs_rel_error": float(e1.quantile(0.9)),
                    "share_within_5pct": float((e1 <= 0.05).mean()),
                    "n_targets": lf["n_targets"], "max_abs_rel_error": lf["max_abs_rel_error"],
                    "targets": pd.DataFrame({"dft_kmh": tgt, "modelled_kmh": lf["modelled"],
                                             "rel_error": lf["rel_error"]}).round(4)
                    .reset_index().to_dict("records"),
                    "dropped_targets": t[~(t["coverage"] >= min_coverage)][
                        ["lad", "ref", "coverage"]].round(3).to_dict("records")}

    # 6–7. assemble per-segment factors: the hybrid (with per-road multipliers on the
    # measured roads) and the base alone, so validation can compare them. Then the ANPR
    # layer (Robbie, 2026-10-09: "as close to ANPR as possible"): after the DfT fit, a
    # speed multiplier k[area, period] and a delay per signalised approach d[area] on
    # centre and urban non-SRN segments, fitted per variant on the ANPR calibration half.
    # It is applied model-wide by area type: [CALIBRATED] on Bristol's links, a
    # [MODELLED] transfer to other towns' centre and urban areas.
    ls_by, before, layers = {}, {}, {}
    for variant, mults in (("hybrid", lf["m"]), ("base", {})):
        g_use = base["g"] if variant == "base" else lf["g"]
        before[variant] = _assemble(seg, g_use, mults, r, rho, level, srn, out_periods)
        if anpr:
            layers[variant] = fit_anpr_layer(before[variant], seg, anpr, out_periods)
            ls_by[variant] = _assemble(seg, g_use, mults, r, rho, level, srn, out_periods,
                                       sig, layers[variant], scope)
        else:
            ls_by[variant] = before[variant]
    ls = ls_by["hybrid"]
    ls_base = ls_by["base"]
    rep["median_factor"] = ls.groupby("period")["factor"].median().round(3).to_dict()
    if anpr:
        rep["anpr_layer"] = {v: {"k": {"|".join(kk): round(x, 4) for kk, x in L["k"].items()},
                                 "delay_s": {a: round(x, 2) for a, x in L["d"].items()},
                                 "n_links": L["n_links"], "n_rows": L["n_rows"]}
                             for v, L in layers.items()}
        rep["anpr"] = anpr_report(ls_by, before, seg, anpr)
        rep["anpr_excluded_links"] = anpr.get("excluded", [])
        # what the layer does to the DfT all-day fit (the two sources disagree on level)
        pos = seg.index.get_indexer(la.index)
        eff = {q: ls[ls["period"] == q]["speed_kmh"].to_numpy()[pos] / la["ff_kmh"].to_numpy()
               for q in periods}
        after = fit.allday_speed(la, eff, P).reindex(tgt.index)
        err = (after / tgt - 1).rename("rel_error").reset_index()
        err["lad"] = err["road"].str.split(":").str[0]
        err["before"] = lf["rel_error"].reindex(err["road"]).to_numpy()
        # share of each target road's length the layer touches
        _, _, adj_la = layer_arrays(la, sig, layers["hybrid"], scope, periods)
        touched = (la["length_m"] * adj_la).groupby(la["road"]).sum() \
            / la["length_m"].groupby(la["road"]).sum()
        err["layer_share"] = touched.reindex(err["road"]).to_numpy()
        err["in_scope"] = err["layer_share"] > 0.5
        by = lambda c: err.groupby(c).agg(  # noqa: E731
            n=("rel_error", "size"), median_before=("before", "median"),
            median_after=("rel_error", "median"),
            median_abs_before=("before", lambda e: e.abs().median()),
            median_abs_after=("rel_error", lambda e: e.abs().median()),
            within_5pct_after=("rel_error", lambda e: (e.abs() <= 0.05).mean())) \
            .round(4).reset_index().to_dict("records")
        rep["dft_after_layer"] = {
            "median_abs_rel_error": float(err["rel_error"].abs().median()),
            "median_rel_error": float(err["rel_error"].median()),
            "by_authority": by("lad"), "by_scope": by("in_scope"),
            "roads": err.round(4).to_dict("records")}
        pm, pdl, _ = layer_arrays(la, sig, layers["base"], scope, periods)
        lo2 = fit.leave_one_road_out(la, tgt, r_main, rho, P, AREAS, post_mult=pm,
                                     post_delay=pdl)
        lo2["in_scope"] = (touched.reindex(lo2["road"]).to_numpy() > 0.5)
        e2 = lo2["rel_error"].abs()
        rep["leave_one_road_out_after_layer"] = {
            "median_abs_rel_error": float(e2.median()),
            "p90_abs_rel_error": float(e2.quantile(0.9)),
            "share_within_15pct": float((e2 <= 0.15).mean()),
            "by_scope": lo2.assign(a=e2).groupby("in_scope")["a"].agg(
                n="size", median_abs="median").round(4).reset_index().to_dict("records"),
            "before_by_scope": loro.assign(a=el, in_scope=lo2["in_scope"].to_numpy())
            .groupby("in_scope")["a"].agg(n="size", median_abs="median").round(4)
            .reset_index().to_dict("records"),
            "roads": lo2.round(4).to_dict("records")}
    return ls, ls_base, rep


LAYER_AREAS = ["centre", "urban"]


def in_scope(seg, scope) -> np.ndarray:
    """Segments the ANPR layer may touch: inside the scope LSOAs (all, if no scope)."""
    if scope is None:
        return np.ones(len(seg), dtype=bool)
    return seg["lsoa"].isin(scope).to_numpy()


def layer_arrays(seg, sig, layer, scope, periods) -> tuple[dict, dict, np.ndarray]:
    """Per-segment speed multiplier and signal delay (s) by period, and the mask of
    adjusted segments: centre/urban, not SRN, in scope."""
    area = seg["area_type"].replace({"buffer": "rural"}).to_numpy() if "area_type" in seg \
        else seg["area"].to_numpy()
    adj = np.isin(area, list(layer["d"])) & (seg["road_class"] != "srn").to_numpy() \
        & in_scope(seg, scope)
    approach = seg["v"].isin(sig).to_numpy() & adj
    d = np.where(approach, pd.Series(area).map(layer["d"]).fillna(0.0).to_numpy(), 0.0)
    mult = {q: np.where(adj, np.array([layer["k"].get((a, q), 1.0) for a in area]), 1.0)
            for q in periods}
    return mult, {q: d for q in periods}, adj


def _anpr_seg_info(seg, scope=None):
    info = seg.assign(_in=in_scope(seg, scope))[["u", "v", "area_type", "road_class", "_in"]] \
        .drop_duplicates(["u", "v"])
    area = info["area_type"].replace({"buffer": "rural"})
    return pd.DataFrame({"u": info["u"], "v": info["v"],
                         "area": area.where(info["_in"], "outside_scope"),
                         "srn": info["road_class"] == "srn"})


def _anpr_components(L, seg, anpr, half, periods):
    from . import signals as sg
    lens = seg[["u", "v", "length_m"]].drop_duplicates(["u", "v"])
    info = _anpr_seg_info(seg, anpr.get("scope"))
    pth = anpr["paths"][anpr["paths"]["half"] == half].drop(columns=["length_m"], errors="ignore")
    return {q: sg.link_components(
        pth, L[L["period"] == q][["u", "v", "speed_kmh"]].merge(lens, on=["u", "v"]),
        info, anpr["signals"], LAYER_AREAS) for q in periods}


def fit_anpr_layer(ls0, seg, anpr, periods) -> dict:
    from . import signals as sg
    comp = _anpr_components(ls0, seg, anpr, "calibration", periods)
    obs = anpr["obs"][anpr["obs"]["half"] == "calibration"]
    return sg.fit_layer(comp, obs, LAYER_AREAS, periods)


def anpr_report(ls_by, before, seg, anpr) -> dict:
    """Modelled ÷ observed per period on both ANPR halves, hybrid and base, with the layer
    and before it. Speeds in the link table already include the layer, so link times are
    plain sums. PMPH (17:00–18:00 observed) is set against the modelled PM period."""
    from . import signals as sg
    out = {}
    pairs = [("AM", "AM"), ("AMPH", "AMPH"), ("IP", "IP"), ("PM", "PM"), ("PM", "PMPH"),
             ("OP", "OP"), ("WE", "WE")]
    for stage, tables in (("", ls_by), ("|before", before)):
        for variant, L in tables.items():
            for half in ("calibration", "validation"):
                comp = _anpr_components(L, seg, anpr, half, sorted({m for m, _ in pairs}))
                for per, obs_per in pairs:
                    t = comp[per][["T_centre", "T_urban", "T_other"]].sum(axis=1)
                    o = anpr["obs"][(anpr["obs"]["half"] == half)
                                    & (anpr["obs"]["period"] == obs_per)]
                    j = pd.concat([t.rename("m"), o.set_index("link_id")["obs_s"].rename("o")],
                                  axis=1, join="inner")
                    ratio = j["m"] / j["o"]
                    out[f"{variant}|{obs_per}|{half}{stage}"] = {
                        "n": int(len(j)), "median_ratio": float(ratio.median()),
                        "median_abs_rel_error": float((ratio - 1).abs().median()),
                        "share_within_15pct": float(((ratio - 1).abs() <= 0.15).mean())}
    return out


def _assemble(seg, g_area, mults, r, rho, level, srn, periods, sig=frozenset(),
              layer=None, scope=None) -> pd.DataFrame:
    area = seg["area_type"].replace({"buffer": "rural"}).to_numpy()
    road_key = seg["lad"].fillna("") + ":" + seg["ref"].fillna("")
    M = np.where(seg["road_class"] == "local_a", road_key.map(mults).fillna(1.0), 1.0)
    g = np.array([g_area[a] for a in area])
    grp = np.where(seg["road_class"] == "minor", "minor", "main")
    cls_lev = np.array([1.0 if c in ("local_a", "srn") else level.get((c, a), (1.0, 0))[0]
                        for c, a in zip(seg["road_class"], area)])
    base = g * M * cls_lev
    fac = {"IP": base, "OP": base * rho["OP"], "WE": base * rho["WE"]}
    for per in ("AM", "PM", "AMPH"):
        if per in periods:
            fac[per] = base * np.array([r[(per, d, a, gr)] for d, a, gr in
                                        zip(seg["direction"], area, grp)])
    src = np.where(seg["road_class"] == "local_a", "fit_dft_level+bus_shape",
                   "fit_class_ratio+bus_shape").astype(object)
    is_srn = (seg["road_class"] == "srn").to_numpy()
    s_idx = np.where(is_srn)[0]
    sm = srn.drop_duplicates(["u", "v"]).set_index(["u", "v"]).reindex(
        pd.MultiIndex.from_arrays([seg["u"].to_numpy()[is_srn], seg["v"].to_numpy()[is_srn]]))
    for per in periods:
        fac[per] = fac[per].copy()
        fac[per][s_idx] = sm[per].to_numpy()
    src[s_idx] = sm["source"].to_numpy()
    rows = []
    ff = seg["ff"].to_numpy()
    L = seg["length_m"].to_numpy()
    # ANPR layer on centre and urban non-SRN segments: speed × k[area, period], and a
    # delay on signalised approaches (v is a signal node) written into the segment speed
    # as L / (L / v + d).
    d = np.zeros(len(seg))
    mult = {per: np.ones(len(seg)) for per in periods}
    if layer:
        mult, dd, _ = layer_arrays(seg, sig, layer, scope, periods)
        d = dd[periods[0]]
    for per in periods:
        v = np.clip(ff * fac[per], 3.0, ff * 1.2)
        k = mult[per]
        v = v * k
        v_eff = np.where(d > 0, (L / 1000) / ((L / 1000) / v + d / 3600), v)
        rows.append(pd.DataFrame({"period": per, "way_id": seg["way_id"].to_numpy(),
                                  "u": seg["u"].to_numpy(), "v": seg["v"].to_numpy(),
                                  "speed_kmh": np.clip(v_eff, 1.0, None),
                                  "factor": fac[per], "anpr_k": k, "signal_delay_s": d,
                                  "source": src, "tag": "CALIBRATED"}))
    return pd.concat(rows, ignore_index=True)


def write_speed_files(ls: pd.DataFrame, out_dir) -> dict:
    """OSRM segment speed files (from_osm_id,to_osm_id,speed_kmh), one per period."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for per, g in ls.groupby("period"):
        f = out_dir / f"speeds_{per}.csv"
        g[["u", "v"]].assign(s=g["speed_kmh"].round(1)).to_csv(f, header=False, index=False)
        files[per] = f
    return files
