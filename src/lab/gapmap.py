"""
P2c C5: the draft gap map (plans/P2.md C5; SPEC §10 first publishable output).

For each internal HBW OD pair (``p1-central``, daily flows — P4 adds periods), the
ratio of PT GC to car GC in the AM peak hour, weighted by flow.

* OA → LSOA skims are aggregated to LSOA → LSOA with OA weights = the OA's resident
  commuters (Census 2021 OA origin–destination flows, ``nat_oa_flows``, summed over
  destinations; TS001 population is not in the lab and commuters suit HBW better),
  computing GC per OA pair first (SPEC §4). OA pairs unreachable by PT drop out
  and the weights renormalise; a pair with no reachable OA is "no PT".
* Car GC = in-vehicle time + parking search + access walk by destination area type
  (PLACEHOLDER values), and a sensitivity with both set to zero.
* Outputs: OD GeoParquet, origin-LSOA summary (flow-weighted mean ratio), the top 50 OD
  pairs by flow × ratio, and a PNG — all labelled provisional.
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

LABEL = ("Provisional: placeholder car parking/access times; time-based GC. Commute only "
         "(daily HBW flows, AM peak-hour times), v1 excludes external commuters.")


def lsoa_gc(con: duckdb.DuckDBPyConnection, skim_parquet: str, gc_col: str,
            ok_expr: str = "true") -> pd.DataFrame:
    """Commuter-weighted OA → LSOA GC to LSOA → LSOA (origin LSOA = the OA's LSOA)."""
    return con.execute(f"""
        WITH s AS (SELECT from_id OA21CD, to_id d_zone, {gc_col} gc
                   FROM read_parquet('{skim_parquet}') WHERE {ok_expr} AND {gc_col} IS NOT NULL)
        SELECT o.LSOA21CD o_zone, s.d_zone,
               sum(s.gc * p.w) / sum(p.w) gc, sum(p.w) w_reached
        FROM s JOIN int_oa o USING (OA21CD) JOIN oa_w p USING (OA21CD)
        GROUP BY ALL""").df()


def build(con, pt_parquet: str, car_parquet: str, park: dict, walk: dict) -> pd.DataFrame:
    """One row per internal HBW pair: flow, PT GC, car GC (placeholder and zero), ratios."""
    con.execute("""CREATE OR REPLACE TEMP TABLE hbw AS
        SELECT o_zone, d_zone, sum(trips) trips FROM demand
        WHERE demand_version = 'p1-central' AND purpose = 'HBW' AND external IS NULL
        GROUP BY ALL HAVING sum(trips) > 0""")
    pt = lsoa_gc(con, pt_parquet, "gc_min", "NOT unreachable")
    car = lsoa_gc(con, car_parquet, "ivt_min")
    area = con.execute("SELECT LSOA21CD d_zone, area_type FROM lsoa_area_type").df()
    car = car.merge(area, on="d_zone", how="left")
    car["car_gc_zero"] = car["gc"]
    car["car_gc"] = car["gc"] + car["area_type"].map(park).fillna(park["urban"]) \
        + car["area_type"].map(walk).fillna(walk["urban"])
    h = con.execute("SELECT * FROM hbw").df()
    m = h.merge(pt[["o_zone", "d_zone", "gc"]].rename(columns={"gc": "pt_gc"}),
                on=["o_zone", "d_zone"], how="left") \
         .merge(car[["o_zone", "d_zone", "car_gc", "car_gc_zero", "area_type"]],
                on=["o_zone", "d_zone"], how="left")
    m["no_pt"] = m["pt_gc"].isna()
    m["ratio"] = m["pt_gc"] / m["car_gc"]
    m["ratio_zero_parking"] = m["pt_gc"] / m["car_gc_zero"]
    return m


def summaries(m: pd.DataFrame) -> dict:
    ok = m.dropna(subset=["ratio"])
    w = ok["trips"]
    wq = lambda x: float(np.average(x, weights=w))  # noqa: E731
    return {"pairs": len(m), "trips": float(m["trips"].sum()),
            "trips_no_pt": float(m.loc[m["no_pt"], "trips"].sum()),
            "flow_weighted_mean_ratio": wq(ok["ratio"]),
            "flow_weighted_mean_ratio_zero_parking": wq(ok["ratio_zero_parking"]),
            "median_ratio": float(ok["ratio"].median()),
            "median_ratio_zero_parking": float(ok["ratio_zero_parking"].median())}


def origin_summary(m: pd.DataFrame) -> pd.DataFrame:
    ok = m.dropna(subset=["ratio"])
    g = ok.groupby("o_zone")
    return pd.DataFrame({
        "trips": g["trips"].sum(),
        "ratio": g.apply(lambda d: np.average(d["ratio"], weights=d["trips"])),
        "ratio_zero_parking": g.apply(lambda d: np.average(d["ratio_zero_parking"],
                                                           weights=d["trips"]))}).reset_index()


def top_pairs(m: pd.DataFrame, n: int = 50) -> pd.DataFrame:
    ok = m.dropna(subset=["ratio"]).assign(score=lambda d: d["trips"] * d["ratio"])
    return ok.sort_values("score", ascending=False).head(n)
