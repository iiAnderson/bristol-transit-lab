"""
P2a A8: r5py network smoke test and runtime projections (plans/P2.md A8, D3, D7).

* 50 seeded LSOA → LSOA pairs by walk, cycle and PT: times must be positive and imply
  plausible speeds against the straight-line distance;
* a 1% sample of OA origins × all LSOA destinations by PT (60-minute window, three
  percentiles), timed, and projected to the full OA → LSOA matrix;
* ``DetailedItineraries`` on a sample of LSOA pairs at one departure, timed, and
  projected to 729² pairs × the D7 number of departures.

Times are wall-clock on this machine; memory is the process's peak RSS (the JVM runs
in-process through JPype).
"""
from __future__ import annotations

import datetime as dt
import math
import resource
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

WINDOW = dt.timedelta(minutes=60)
PERCENTILES = [25, 50, 75]


def _gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"id": df["id"].values},
                            geometry=[Point(x, y) for x, y in zip(df["lon"], df["lat"])],
                            crs="EPSG:4326")


def _km(a, b) -> float:
    dx = (a.lon - b.lon) * 111.32 * math.cos(math.radians((a.lat + b.lat) / 2))
    dy = (a.lat - b.lat) * 111.32
    return math.hypot(dx, dy)


def peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9   # bytes on macOS


def run(osm: str, gtfs: list[str], lsoa: pd.DataFrame, oa: pd.DataFrame,
        departure: dt.datetime, *, n_pairs: int = 50, origin_share: float = 0.01,
        itin_pairs: int = 200, itin_departures: int = 3, seed: int = 1) -> dict:
    from r5py import (DetailedItineraries, TransportMode, TransportNetwork,
                      TravelTimeMatrix)
    rng = np.random.default_rng(seed)
    out: dict = {"departure": departure.isoformat()}

    t = time.time()
    net = TransportNetwork(osm, gtfs)
    out["network_build_s"] = round(time.time() - t, 1)

    # 50 pairs, three modes
    idx = rng.choice(len(lsoa), size=(n_pairs, 2), replace=True)
    idx = idx[idx[:, 0] != idx[:, 1]]
    o = lsoa.iloc[idx[:, 0]].reset_index(drop=True)
    d = lsoa.iloc[idx[:, 1]].reset_index(drop=True)
    pairs = []
    modes = {"walk": [TransportMode.WALK], "cycle": [TransportMode.BICYCLE],
             "pt": [TransportMode.TRANSIT, TransportMode.WALK]}
    for i in range(len(o)):
        pairs.append({"from": o.id[i], "to": d.id[i], "km": round(_km(o.iloc[i], d.iloc[i]), 2)})
    for m, tm in modes.items():
        t = time.time()
        res = []
        for i in range(len(o)):   # paired, not all-to-all
            r = TravelTimeMatrix(net, origins=_gdf(o.iloc[[i]]), destinations=_gdf(d.iloc[[i]]),
                                 departure=departure, departure_time_window=WINDOW,
                                 percentiles=[50], transport_modes=tm)
            res.append(r["travel_time"].iloc[0])
        out[f"pairs_{m}_s"] = round(time.time() - t, 1)
        for p, v in zip(pairs, res):
            p[m] = None if pd.isna(v) else int(v)
    out["pairs"] = pairs
    checks = {}
    for m, lo, hi in [("walk", 2.0, 7.0), ("cycle", 6.0, 30.0), ("pt", 2.0, 80.0)]:
        speeds = [p["km"] / (p[m] / 60) for p in pairs if p.get(m) and p["km"] > 0.3]
        checks[m] = {"n_routed": sum(p.get(m) is not None for p in pairs),
                     "n_positive": sum((p.get(m) or 0) > 0 for p in pairs),
                     "median_kmh": round(float(np.median(speeds)), 1) if speeds else None,
                     "outside_plausible": sum(not lo <= s <= hi for s in speeds)}
    out["pair_checks"] = checks

    # D3: 1% of OA origins x all LSOA destinations, PT
    n_o = max(1, round(len(oa) * origin_share))
    so = oa.iloc[rng.choice(len(oa), n_o, replace=False)]
    t = time.time()
    ttm = TravelTimeMatrix(net, origins=_gdf(so), destinations=_gdf(lsoa),
                           departure=departure, departure_time_window=WINDOW,
                           percentiles=PERCENTILES,
                           transport_modes=[TransportMode.TRANSIT, TransportMode.WALK])
    s = time.time() - t
    cells = n_o * len(lsoa)
    out["d3_sample"] = {"origins": n_o, "destinations": len(lsoa), "seconds": round(s, 1),
                        "unreachable_share": round(float(ttm["travel_time_p50"].isna().mean()), 3),
                        "projected_full_pt_one_period_h": round(s * len(oa) / n_o / 3600, 2)}
    for m, tm in [("walk", [TransportMode.WALK]), ("cycle", [TransportMode.BICYCLE])]:
        t = time.time()
        TravelTimeMatrix(net, origins=_gdf(so), destinations=_gdf(lsoa), departure=departure,
                         transport_modes=tm)
        out["d3_sample"][f"projected_full_{m}_h"] = round(
            (time.time() - t) * len(oa) / n_o / 3600, 2)
    out["d3_sample"]["cells"] = cells

    # D7: DetailedItineraries on LSOA pairs, one departure
    k = min(itin_pairs, len(lsoa) * (len(lsoa) - 1))
    a = rng.choice(len(lsoa), k)
    b = rng.choice(len(lsoa), k)
    keep = a != b
    io = lsoa.iloc[a[keep]].reset_index(drop=True)
    idd = lsoa.iloc[b[keep]].reset_index(drop=True)
    # One-to-one pairs need unique ids on each side.
    io["id"] = [f"{x}#{i}" for i, x in enumerate(io["id"])]
    idd["id"] = [f"{x}#{i}" for i, x in enumerate(idd["id"])]
    t = time.time()
    it = DetailedItineraries(net, origins=_gdf(io), destinations=_gdf(idd),
                             departure=departure,
                             transport_modes=[TransportMode.TRANSIT, TransportMode.WALK])
    s = time.time() - t
    n = int(keep.sum())
    full = len(lsoa) ** 2
    out["d7_sample"] = {"pairs": n, "seconds": round(s, 1), "legs": len(it),
                        "options_per_pair": round(it.groupby(["from_id", "to_id"])["option"]
                                                  .nunique().mean(), 2) if len(it) else None,
                        "projected_full_h": round(s / n * full * itin_departures / 3600, 1)}
    out["peak_rss_gb"] = round(peak_rss_gb(), 2)
    return out
