"""
Fix-spacing bias test (plans/P2.md A6 "Bias test").

The archive holds one snapshot every 30 s; the live collection polled every 10 s. The
same live days are map-matched twice with identical filters — at full resolution and
thinned to what a 30 s snapshot would have held — and the harmonic-mean bus speed of
each segment × period cell is compared (thinned ÷ full). Ratios are summarised by road
class × area type and by stop density (stops within ``stop_radius_m`` of the segment
midpoint per km of segment, in bands).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


def cells(trav: pd.DataFrame, min_obs: int) -> pd.DataFrame:
    t = trav[trav["kmh"] > 0]
    g = t.groupby(["u", "v", "period"]).agg(n=("leg", "nunique"),
                                             hm=("kmh", lambda x: len(x) / (1 / x).sum()))
    return g[g["n"] >= min_obs]


def stops_near(seg: pd.DataFrame, stops: pd.DataFrame, radius_m: float) -> np.ndarray:
    """Stops within ``radius_m`` of each segment midpoint (equirectangular metres)."""
    lat0 = np.radians(float(stops["lat"].mean()))
    xy = lambda lon, lat: np.c_[np.radians(lon) * np.cos(lat0) * 6371000,  # noqa: E731
                                np.radians(lat) * 6371000]
    tree = cKDTree(xy(stops["lon"].to_numpy(), stops["lat"].to_numpy()))
    mid = xy((seg["lon_u"] + seg["lon_v"]).to_numpy() / 2, (seg["lat_u"] + seg["lat_v"]).to_numpy() / 2)
    return tree.query_ball_point(mid, radius_m, return_length=True)


def compare(full: pd.DataFrame, thin: pd.DataFrame, seg: pd.DataFrame, stops: pd.DataFrame,
            min_obs: int, stop_radius_m: float = 100.0) -> tuple[pd.DataFrame, dict]:
    a, b = cells(full, min_obs), cells(thin, min_obs)
    j = a.join(b, lsuffix="_full", rsuffix="_thin", how="inner").reset_index()
    j["ratio"] = j["hm_thin"] / j["hm_full"]
    info = seg[["u", "v", "road_class", "area_type", "bus_flag", "lon_u", "lat_u",
                "lon_v", "lat_v"]].drop_duplicates(["u", "v"])
    info = info.assign(stops=stops_near(info, stops, stop_radius_m))
    j = j.merge(info.drop(columns=["lon_u", "lat_u", "lon_v", "lat_v"]), on=["u", "v"])
    j = j[~j["bus_flag"]]
    j["stop_band"] = pd.cut(j["stops"], [-1, 0, 1, 3, 1000], labels=["0", "1", "2-3", "4+"])
    summ = lambda g: pd.Series({"cells": len(g), "median_ratio": g["ratio"].median(),  # noqa: E731
                                "p10": g["ratio"].quantile(0.1), "p90": g["ratio"].quantile(0.9)})
    return j, {
        "overall": summ(j).to_dict(),
        "by_class_area": j.groupby(["road_class", "area_type"]).apply(summ).reset_index()
        .to_dict("records"),
        "by_period": j.groupby("period").apply(summ).reset_index().to_dict("records"),
        "by_stop_band": j.groupby("stop_band", observed=True).apply(summ).reset_index()
        .to_dict("records"),
        "legs_full": int(full["leg"].nunique()), "legs_thin": int(thin["leg"].nunique())}
