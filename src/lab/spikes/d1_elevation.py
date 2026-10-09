"""
D1 spike (plans/P3.md): what a terrain model does to walking and cycling times.

Three street networks from the same OSM clip — flat, and with the terrain raster under
each of R5's two slope cost functions — and for each a walk and a cycle matrix from
every OA population-weighted centroid to the LSOA destinations. OAs are classed by
local relief (the standard deviation of terrain height within ``relief_radius_m`` of
the centroid) so the effect can be read on steep and on flat ground separately.
"""
from __future__ import annotations

import datetime as dt
import math
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point


def relief(dem_tif: str, pts: pd.DataFrame, radius_m: float) -> pd.Series:
    """Standard deviation of terrain height (m) within ``radius_m`` of each point."""
    import rasterio
    with rasterio.open(dem_tif) as f:
        z, tr = f.read(1), f.transform
    out = []
    for lon, lat in zip(pts.lon, pts.lat):
        dy = radius_m / 111_320 / abs(tr.e)
        dx = radius_m / (111_320 * math.cos(math.radians(lat))) / tr.a
        c, r = ~tr * (lon, lat)
        win = z[max(int(r - dy), 0):int(r + dy) + 1, max(int(c - dx), 0):int(c + dx) + 1]
        out.append(float(win.std()) if win.size else np.nan)
    return pd.Series(out, index=pts.index)


def _gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"id": df["id"]}, crs="EPSG:4326",
                            geometry=[Point(x, y) for x, y in zip(df.lon, df.lat)])


def matrices(osm: str, dem_tif: str | None, fn: str | None, o: pd.DataFrame,
             d: pd.DataFrame, dep: dt.datetime, ps: dict, max_min: int) -> dict:
    """Walk and cycle matrices on one network; ``fn`` is the cost function's name."""
    from r5py import ElevationCostFunction, TransportMode, TransportNetwork, TravelTimeMatrix
    t0 = time.time()
    net = (TransportNetwork(osm, []) if dem_tif is None else
           TransportNetwork(osm, [], elevation_model=[dem_tif],  # a list: r5py iterates a bare str
                            elevation_cost_function=ElevationCostFunction(fn)))
    out = {"build_s": round(time.time() - t0, 1)}
    for mode, tm in (("walk", TransportMode.WALK), ("cycle", TransportMode.BICYCLE)):
        t0 = time.time()
        m = TravelTimeMatrix(net, origins=_gdf(o), destinations=_gdf(d), departure=dep,
                             transport_modes=[tm], max_time=dt.timedelta(minutes=max_min),
                             speed_walking=ps["routing.walk_speed_kmh"],
                             speed_cycling=ps["routing.cycle_speed_kmh"],
                             max_bicycle_traffic_stress=ps["routing.max_bicycle_lts"])
        out[mode] = m.rename(columns={"travel_time": "t"})
        out[f"{mode}_s"] = round(time.time() - t0, 1)
    return out


def compare(flat: pd.DataFrame, hilly: pd.DataFrame, rel: pd.Series, cap_min: int) -> dict:
    """Pairs within ``cap_min`` on the flat network, by the origin's relief class
    (fifths of OAs: flattest, middle three, steepest)."""
    j = flat.merge(hilly, on=["from_id", "to_id"], suffixes=("_flat", "_dem"))
    j = j[j.t_flat.notna() & (j.t_flat <= cap_min) & (j.t_flat > 0)]
    j["lost"] = j.t_dem.isna()
    j["d"] = j.t_dem - j.t_flat
    q = rel.quantile([0.2, 0.8])
    cls = pd.cut(rel, [-np.inf, q[0.2], q[0.8], np.inf], labels=["flattest fifth", "middle", "steepest fifth"])
    j["cls"] = j.from_id.map(cls)
    res = {}
    for name, g in [("all", j), *j.groupby("cls", observed=True)]:
        ok = g[~g.lost]
        res[str(name)] = {
            "pairs": int(len(g)), "mean_flat_min": round(float(ok.t_flat.mean()), 2),
            "mean_change_min": round(float(ok.d.mean()), 2),
            "mean_change_pct": round(float(100 * ok.d.sum() / ok.t_flat.sum()), 1),
            "p10_p50_p90_change_min": [float(x) for x in ok.d.quantile([0.1, 0.5, 0.9])],
            "share_slower_2min_plus": round(float((ok.d >= 2).mean()), 3),
            "share_faster_2min_plus": round(float((ok.d <= -2).mean()), 3),
            "beyond_max_time_with_dem": int(g.lost.sum())}
    res["relief_sd_m_cut_points"] = [round(float(q[0.2]), 1), round(float(q[0.8]), 1)]
    return res


def asymmetry(m: pd.DataFrame, cap_min: int) -> dict:
    """Uphill and downhill should differ: |t(a→b) − t(b→a)| over pairs present both ways.
    Needs origins and destinations that share ids; here it is run OA → OA on a sample."""
    r = m.rename(columns={"from_id": "to_id", "to_id": "from_id", "t": "t_back"})
    j = m.merge(r, on=["from_id", "to_id"])
    j = j[(j.from_id < j.to_id) & j.t.notna() & j.t_back.notna() & (j.t <= cap_min)]
    a = (j.t - j.t_back).abs()
    return {"pairs": int(len(j)), "mean_abs_diff_min": round(float(a.mean()), 2),
            "share_2min_plus": round(float((a >= 2).mean()), 3)}
