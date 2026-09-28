"""
P2b held-out validation (plans/P2.md §5 and §8 pre-registered rule).

Two sources, each run for the hybrid (base + per-road multipliers) and the base alone:

* **Bus moving speeds, spatially blocked.** Segments are grouped into 2 km blocks and
  the blocks into K folds. For each fold the whole calibration is re-run without the
  fold's bus traversals, so the shape ratios never see the held-out segments; a
  bus-to-car ratio κ[class, area, period] is fitted on the training segments, and each
  held-out segment's bus speed is predicted as model car speed × κ.
* **Bristol ANPR journey times, 2023–24** (absolute levels). Each link is routed start
  to end by OSRM with that period's calibrated speeds; the observed time is the
  plate-match-weighted mean hourly journey time on Tue–Thu, excluding August and the
  Christmas and summer holiday windows (an approximate neutral-day filter: the 2023/24
  and 2024/25 school calendars were not checked). Links whose routed length differs
  from the ANPR link length by more than ``max_len_diff`` are excluded (listed).
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from ..supply import osrm


def period_dataset(base: Path, speed_file: Path, out_dir: Path) -> Path:
    """A copy of the OSRM MLD files customised with one period's segment speeds."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in base.parent.glob(base.name + ".osrm*"):
        shutil.copyfile(f, out_dir / f.name)
    b = out_dir / base.name
    osrm.customize(b, speed_file)
    return b


def anpr_observed(counts: list[Path], periods: dict[str, tuple[str, str]], tz: str) -> pd.DataFrame:
    x = pd.concat([pd.read_parquet(f) for f in counts], ignore_index=True)
    t = pd.to_datetime(x["t_ms"], unit="ms", utc=True).dt.tz_convert(ZoneInfo(tz))
    keep = (t.dt.weekday.isin([1, 2, 3]) & (t.dt.month != 8)
            & ~((t.dt.month == 12) & (t.dt.day >= 18)) & ~((t.dt.month == 1) & (t.dt.day <= 4))
            & ~((t.dt.month == 7) & (t.dt.day >= 22)) & ~((t.dt.month == 9) & (t.dt.day <= 3)))
    x = x[keep & (x["matches"] > 0) & (x["journey_s"] > 0)].copy()
    hh = t[keep].dt.strftime("%H:%M")
    x["period"] = None
    for p, (a, b) in periods.items():
        x.loc[(hh >= a) & (hh < b), "period"] = p
    x = x.dropna(subset=["period"])
    g = x.groupby(["link_id", "period"])
    return pd.DataFrame({"obs_s": g.apply(lambda d: np.average(d["journey_s"], weights=d["matches"])),
                         "hours": g.size(), "matches": g["matches"].sum()}).reset_index()


def anpr_modelled(links_geojson: Path, port: int) -> pd.DataFrame:
    rows = []
    s = requests.Session()
    for f in json.loads(links_geojson.read_text())["features"]:
        c = f["geometry"]["coordinates"]
        (x0, y0), (x1, y1) = c[0][:2], c[-1][:2]
        r = s.get(f"http://127.0.0.1:{port}/route/v1/driving/{x0},{y0};{x1},{y1}",
                  params={"overview": "false"}, timeout=60).json()
        if r.get("code") != "Ok":
            continue
        rt = r["routes"][0]
        rows.append({"link_id": f["properties"]["JOURNEY_LINK_ID"],
                     "desc": f["properties"]["JOURNEY_LINK_DESCRIPTION"],
                     "link_m": f["properties"]["SHAPE.STLength()"],
                     "route_m": rt["distance"], "mod_s": rt["duration"]})
    return pd.DataFrame(rows)


def summarise(err: pd.Series) -> dict:
    e = err.abs()
    return {"n": int(len(e)), "median_abs_rel_error": float(e.median()),
            "p10_ratio": float((1 + err).quantile(0.1)), "median_ratio": float((1 + err).median()),
            "p90_ratio": float((1 + err).quantile(0.9))}


def blocks(seg: pd.DataFrame, km: float, k: int, seed: int = 0) -> pd.Series:
    mx = (seg["lon_u"] + seg["lon_v"]) / 2
    my = (seg["lat_u"] + seg["lat_v"]) / 2
    bx = np.floor(mx * 111.32 * 0.623 / km).astype(int)
    by = np.floor(my * 111.32 / km).astype(int)
    key = bx.astype(str) + "_" + by.astype(str)
    ids = pd.Series(key.unique())
    fold = pd.Series(np.random.default_rng(seed).permutation(len(ids)) % k, index=ids)
    return key.map(fold)


def bus_holdout(seg: pd.DataFrame, trav: pd.DataFrame, fold: pd.Series, k: int,
                run_calibration, min_obs: int) -> pd.DataFrame:
    """run_calibration(trav_subset) -> (ls_hybrid, ls_base). Returns held-out rows with
    observed bus speed and both predictions."""
    agg = trav.groupby(["u", "v", "period"]).agg(
        n=("leg", "nunique"), bus=("kmh", lambda x: len(x) / (1 / x).sum())).reset_index()
    agg = agg[agg["period"].isin(["AM", "IP"]) & (agg["n"] >= min_obs)]
    info = seg[["u", "v", "road_class", "area_type", "bus_flag"]].assign(fold=fold.to_numpy())
    info = info[info["road_class"].isin(["local_a", "b_road", "minor"]) & ~info["bus_flag"]]
    agg = agg.merge(info.drop_duplicates(["u", "v"]), on=["u", "v"])
    uv_fold = seg[["u", "v"]].assign(fold=fold.to_numpy()).drop_duplicates(["u", "v"])
    trav_f = trav.merge(uv_fold, on=["u", "v"], how="left")
    out = []
    for f in range(k):
        test = agg[agg["fold"] == f]
        train = agg[agg["fold"] != f]
        if test.empty:
            continue
        ls_h, ls_b = run_calibration(trav_f[trav_f["fold"] != f].drop(columns="fold"))
        for name, ls in (("hybrid", ls_h), ("base", ls_b)):
            m = ls[ls["period"].isin(["AM", "IP"])][["u", "v", "period", "speed_kmh"]] \
                .drop_duplicates(["u", "v", "period"])
            tr = train.merge(m, on=["u", "v", "period"])
            kappa = (tr["bus"] / tr["speed_kmh"]).groupby(
                [tr["road_class"], tr["area_type"], tr["period"]]).median().rename("kappa")
            te = test.merge(m, on=["u", "v", "period"]).merge(
                kappa, left_on=["road_class", "area_type", "period"], right_index=True, how="left")
            te["kappa"] = te["kappa"].fillna(float((tr["bus"] / tr["speed_kmh"]).median()))
            te["pred"] = te["speed_kmh"] * te["kappa"]
            te["variant"], te["fold_k"] = name, f
            out.append(te)
    r = pd.concat(out, ignore_index=True)
    r["rel_error"] = r["pred"] / r["bus"] - 1
    return r
