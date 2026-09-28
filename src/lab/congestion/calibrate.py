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


def bus_shape(seg: pd.DataFrame, trav: pd.DataFrame, min_obs: int, min_cells: int,
              national: dict[str, float]) -> tuple[dict, dict, dict]:
    """r[(p, dir, area, group)] = median bus speed ratio p/IP; class level ratios.
    AMPH (the 08:00–09:00 skim hour) comes from traversals whose leg started in hour 8."""
    if "hour" in trav.columns:
        trav = pd.concat([trav, trav[trav["hour"] == 8].assign(period="AMPH")],
                         ignore_index=True)
    agg = trav.groupby(["u", "v", "period"]).agg(n=("leg", "nunique"),
                                                  hm=("kmh", lambda x: len(x) / (1 / x).sum()))
    agg = agg.reset_index().pivot_table(index=["u", "v"], columns="period", values=["n", "hm"])
    agg.columns = [f"{a}_{b}" for a, b in agg.columns]
    loc = seg[seg["road_class"].isin(["local_a", "b_road", "minor"]) & ~seg["bus_flag"]].copy()
    loc["area"] = loc["area_type"].replace({"buffer": "rural"})
    loc["group"] = np.where(loc["road_class"] == "minor", "minor", "main")
    loc = loc.merge(agg, left_on=["u", "v"], right_index=True, how="inner")
    r, diag = {}, {}
    for p in ("AM", "PM", "AMPH"):
        # The archive window is 07:00–16:00, so there is no PM bus data by design; a
        # period without bus data falls back to the national ratio in every cell.
        if f"hm_{p}" in loc.columns and "hm_IP" in loc.columns:
            ok = (loc[f"n_{p}"].fillna(0) >= min_obs) & (loc["n_IP"].fillna(0) >= min_obs)
            d = loc[ok].assign(ratio=lambda x: x[f"hm_{p}"] / x["hm_IP"])
            for (direction, area, group), g in d.groupby(["direction", "area", "group"]):
                if len(g) >= min_cells:
                    r[(p, direction, area, group)] = float(g["ratio"].median())
                    diag[(p, direction, area, group)] = ("bus", len(g))
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
        road_lam: float, anpr: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (hybrid link_speed, base link_speed, report). ``anpr`` (paths, obs,
    signals) turns on signal delays calibrated to the ANPR calibration half."""
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
    r, rdiag, level = bus_shape(seg, trav, p["min_obs_per_link"], p["min_cells"], national)
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
    # Signal delays: alternate (a) the DfT level fit with the current delays and (b) the
    # delay fit on the ANPR calibration half with the current speeds (IP labels 11–15);
    # AM and other periods take the IP delay [MODELLED] until the ANPR hour convention is
    # confirmed; SRN approaches carry no delay (WebTRIS measures SRN speeds directly).
    delays = {"centre": 0.0, "urban": 0.0, "rural": 0.0}
    sig = anpr["signals"] if anpr else set()
    la_d = None
    rep["signals"] = {"enabled": bool(anpr), "iterations": []}
    for it in range(3 if anpr else 1):
        if anpr:
            la_d = _delays_for(la, sig, delays, periods)
        base = fit.fit_level(la, tgt, r_main, rho, P, AREAS, authority=False, delay_s=la_d)
        lf = fit.fit_level(la, tgt, r_main, rho, P, AREAS, authority=False,
                           road_lam=road_lam, delay_s=la_d)
        if not anpr:
            break
        ls_ip = _assemble(seg, lf["g"], lf["m"], r, rho, level, srn, ["IP"], sig, None)
        from . import signals as sg
        cal = anpr["obs"][(anpr["obs"]["half"] == "calibration") & (anpr["obs"]["period"] == "IP")]
        spd = ls_ip[["u", "v", "speed_kmh"]].merge(
            seg[["u", "v", "length_m"]].drop_duplicates(["u", "v"]), on=["u", "v"])
        area = seg[["u", "v", "area_type"]].drop_duplicates(["u", "v"]).rename(
            columns={"area_type": "area"})
        area["area"] = area["area"].replace({"buffer": "rural"})
        paths = anpr["paths"][anpr["paths"]["half"] == "calibration"]
        fitted = sg.fit_delays(paths, spd, area, sig, cal.set_index("link_id")["obs_s"],
                               ["centre", "urban"])
        delays = {"centre": fitted["centre"], "urban": fitted["urban"], "rural": fitted["urban"]}
        rep["signals"]["iterations"].append({"it": it, "delays_s": dict(delays),
                                             "g": dict(lf["g"])})
    rep["signals"]["delays_s"] = delays
    loro = fit.leave_one_road_out(la, tgt, r_main, rho, P, AREAS, delay_s=la_d)
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
    # measured roads) and the base alone, so validation can compare them
    ls_by = {}
    for variant, mults in (("hybrid", lf["m"]), ("base", {})):
        ls_by[variant] = _assemble(seg, base["g"] if variant == "base" else lf["g"], mults,
                                   r, rho, level, srn, out_periods, sig,
                                   delays if anpr else None)
    ls = ls_by["hybrid"]
    ls_base = ls_by["base"]
    rep["median_factor"] = ls.groupby("period")["factor"].median().round(3).to_dict()
    if anpr:
        rep["anpr"] = anpr_report(ls, ls_base, seg, anpr, delays)
        rep["anpr_excluded_links"] = anpr.get("excluded", [])
    return ls, ls_base, rep


def _delays_for(la, sig, delays, periods):
    is_sig = la["v"].isin(sig).to_numpy()
    d = np.where(is_sig, la["area"].map(delays).fillna(0.0).to_numpy(), 0.0)
    return {p: d for p in periods}


def anpr_report(ls, ls_base, seg, anpr, delays) -> dict:
    """Modelled ÷ observed on both ANPR halves, IP and the AM peak hour (both hour
    conventions), for the hybrid and the base, with the fitted signal delays."""
    from . import signals as sg
    area = seg[["u", "v", "area_type"]].drop_duplicates(["u", "v"]).rename(
        columns={"area_type": "area"})
    area["area"] = area["area"].replace({"buffer": "rural"})
    lens = seg[["u", "v", "length_m"]].drop_duplicates(["u", "v"])
    out = {}
    for variant, L in (("hybrid", ls), ("base", ls_base)):
        for per, obs_per in (("IP", "IP"), ("AMPH", "AMPH_if_start"), ("AMPH", "AMPH_if_end")):
            # speeds in `ls` already include the delays on approaches, so no extra delay
            spd = L[L["period"] == per][["u", "v", "speed_kmh"]].merge(lens, on=["u", "v"])
            for half in ("calibration", "validation"):
                pth = anpr["paths"][anpr["paths"]["half"] == half]
                t = sg.link_times(pth, spd, area, set(), {})
                o = anpr["obs"][(anpr["obs"]["half"] == half) & (anpr["obs"]["period"] == obs_per)]
                j = pd.concat([t.rename("m"), o.set_index("link_id")["obs_s"].rename("o")],
                              axis=1, join="inner")
                ratio = j["m"] / j["o"]
                out[f"{variant}|{obs_per}|{half}"] = {
                    "n": int(len(j)), "median_ratio": float(ratio.median()),
                    "median_abs_rel_error": float((ratio - 1).abs().median())}
    return out


def _assemble(seg, g_area, mults, r, rho, level, srn, periods, sig=frozenset(),
              delays=None) -> pd.DataFrame:
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
    # Signal delay on approach segments (v is a signal node; not SRN), written into the
    # segment speed: L / (L / v + d).
    d = np.zeros(len(seg))
    if delays:
        approach = seg["v"].isin(sig).to_numpy() & ~is_srn
        d = np.where(approach, pd.Series(area).map(delays).fillna(0.0).to_numpy(), 0.0)
    for per in periods:
        v = np.clip(ff * fac[per], 3.0, ff * 1.2)
        v_eff = np.where(d > 0, (L / 1000) / ((L / 1000) / v + d / 3600), v)
        rows.append(pd.DataFrame({"period": per, "way_id": seg["way_id"].to_numpy(),
                                  "u": seg["u"].to_numpy(), "v": seg["v"].to_numpy(),
                                  "speed_kmh": np.clip(v_eff, 1.0, None),
                                  "factor": fac[per], "signal_delay_s": d,
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
