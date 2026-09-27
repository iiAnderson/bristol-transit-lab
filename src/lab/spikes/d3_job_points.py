"""
D3 test (plans/P2.md, approved at the P2a stop): job-weighted LSOA destination points.

Points: for each internal LSOA, the mean of its OAs' population-weighted centroids,
weighted by Census 2021 ODWP01EW workplace counts ("Working in the UK but not working at
or from home", by OA of workplace). Lockdown distorts the level of those counts; only
their within-LSOA distribution is used. An LSOA with no workplace count keeps its PWC.

Test: LSOA PWC origins → LSOA destinations, PT p50 (r5py, 60-min window) and car (OSRM,
free flow), to PWC and to job-weighted points. Changes are summarised over internal HBW
pairs, weighted by p1-central trips. The rule: use job-weighted points for HBW
destinations if the median absolute change exceeds either threshold.
"""
from __future__ import annotations

import datetime as dt
import math

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

WORKPLACE_CODE = 3


def job_points(con: duckdb.DuckDBPyConnection, odwp_oa_csv: str) -> pd.DataFrame:
    """Internal LSOAs with PWC and job-weighted points (lon, lat) and workplace totals."""
    return con.execute(f"""
        WITH wp AS (
            SELECT "OA of workplace code" OA21CD, sum("Count") workers
            FROM read_csv('{odwp_oa_csv}')
            WHERE "Place of work indicator (4 categories) code" = {WORKPLACE_CODE}
            GROUP BY 1),
        oa AS (
            SELECT o.OA21CD, o.LSOA21CD, p.lon, p.lat, coalesce(wp.workers, 0) w
            FROM int_oa o JOIN int_oa_pwc p USING (OA21CD) LEFT JOIN wp USING (OA21CD)),
        jw AS (
            SELECT LSOA21CD, sum(w) workers,
                   sum(lon * w) / nullif(sum(w), 0) jlon, sum(lat * w) / nullif(sum(w), 0) jlat
            FROM oa GROUP BY 1)
        SELECT l.LSOA21CD id, l.lon, l.lat, coalesce(jw.jlon, l.lon) jlon,
               coalesce(jw.jlat, l.lat) jlat, coalesce(jw.workers, 0) workers
        FROM int_lsoa l LEFT JOIN jw USING (LSOA21CD) ORDER BY 1""").df()


def _gdf(ids, lons, lats) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"id": list(ids)},
                            geometry=[Point(x, y) for x, y in zip(lons, lats)], crs="EPSG:4326")


def _wmedian(x: np.ndarray, w: np.ndarray) -> float:
    o = np.argsort(x)
    c = np.cumsum(w[o])
    return float(x[o][np.searchsorted(c, c[-1] / 2)])


def run(con, odwp_oa_csv: str, osrm_base, osm: str, gtfs: list[str], departure: dt.datetime,
        abs_min: float, rel: float) -> dict:
    from r5py import TransportMode, TransportNetwork, TravelTimeMatrix
    from ..supply import osrm
    pts = job_points(con, odwp_oa_csv)
    moved_m = [math.hypot((a - c) * 111320 * math.cos(math.radians(b)), (b - d) * 111320)
               for a, b, c, d in zip(pts.lon, pts.lat, pts.jlon, pts.jlat)]
    pts["moved_m"] = moved_m
    hbw = con.execute("""SELECT o_zone, d_zone, sum(trips) trips FROM demand
        WHERE demand_version = 'p1-central' AND purpose = 'HBW' AND external IS NULL
        GROUP BY ALL HAVING sum(trips) > 0""").df()

    orig = _gdf(pts.id, pts.lon, pts.lat)
    net = TransportNetwork(osm, gtfs)
    pt = {}
    for name, (x, y) in {"pwc": ("lon", "lat"), "job": ("jlon", "jlat")}.items():
        t = TravelTimeMatrix(net, origins=orig, destinations=_gdf(pts.id, pts[x], pts[y]),
                             departure=departure,
                             departure_time_window=dt.timedelta(minutes=60), percentiles=[50],
                             transport_modes=[TransportMode.TRANSIT, TransportMode.WALK])
        pt[name] = t.rename(columns={"from_id": "o_zone", "to_id": "d_zone",
                                     "travel_time": f"pt_{name}"})
    src = list(zip(pts.lon, pts.lat))
    with osrm.Server(osrm_base) as s:
        car = {n: s.table(src, list(zip(pts[x], pts[y]))) / 60
               for n, (x, y) in {"pwc": ("lon", "lat"), "job": ("jlon", "jlat")}.items()}
    ids = pts.id.tolist()
    cardf = pd.DataFrame({"o_zone": np.repeat(ids, len(ids)), "d_zone": np.tile(ids, len(ids)),
                          "car_pwc": car["pwc"].ravel(), "car_job": car["job"].ravel()})
    m = hbw.merge(pt["pwc"], on=["o_zone", "d_zone"], how="left") \
           .merge(pt["job"], on=["o_zone", "d_zone"], how="left") \
           .merge(cardf, on=["o_zone", "d_zone"], how="left")
    out = {"lsoas": len(pts), "lsoas_without_workplaces": int((pts.workers == 0).sum()),
           "moved_m": {q: round(float(np.quantile(moved_m, q)), 0) for q in (0.5, 0.9, 0.99)},
           "hbw_pairs": len(m), "hbw_trips": round(float(m.trips.sum()))}
    use = False
    for mode in ("pt", "car"):
        a, b = m[f"{mode}_pwc"], m[f"{mode}_job"]
        ok = a.notna() & b.notna() & (a > 0)
        d = (b - a)[ok].to_numpy()
        r = (d / a[ok]).to_numpy()
        w = m.trips[ok].to_numpy()
        res = {"pairs_compared": int(ok.sum()),
               "trips_compared": round(float(w.sum())),
               "median_abs_change_min_weighted": round(_wmedian(np.abs(d), w), 2),
               "median_abs_change_pct_weighted": round(100 * _wmedian(np.abs(r), w), 2),
               "median_change_min_weighted": round(_wmedian(d, w), 2),
               "p90_abs_change_min_weighted": round(float(np.quantile(np.abs(d), 0.9)), 2),
               "median_abs_change_min_unweighted": round(float(np.median(np.abs(d))), 2)}
        res["exceeds"] = (res["median_abs_change_min_weighted"] > abs_min
                          or res["median_abs_change_pct_weighted"] > 100 * rel)
        use = use or res["exceeds"]
        out[mode] = res
    out["decision"] = ("use job-weighted points for HBW destinations" if use
                       else "keep PWC for HBW destinations")
    out["points"] = pts
    return out
