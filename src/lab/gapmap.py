"""
P2c C5: the gap map (plans/P2.md C5, revised 2026-10-09; SPEC §10 first publishable
output).

For each internal HBW OD pair (``p1-central``, daily flows — P4 adds periods): PT GC and
car GC in the AM peak hour.

* OA → LSOA skims are aggregated to LSOA → LSOA with OA weights = the OA's resident
  commuters (Census 2021 OA origin–destination flows, ``nat_oa_flows``, summed over
  destinations; TS001 population is not in the lab and commuters suit HBW better),
  computing GC per OA pair first (SPEC §4). OA pairs without the mode drop out and the
  weights renormalise.
* **PT is the alternative with at least one ride** (``gc_ride_min``; walk-only minutes are
  not PT, SPEC §7.3). A pair with no ride itinerary from any of its OAs is "no PT".
* Car GC = in-vehicle time + parking search + access walk by destination area type. Both
  ends of the [MODELLED] range are carried: ``high`` (the values in params) and ``low``
  (zero, as G-BATS3 treats walk to/from the car).
* **Scope:** intrazonal pairs and pairs under ``gapmap.min_distance_km`` (straight line
  between population-weighted centroids) are left out of the gap map and reported
  separately: there PT is mostly a walk and a minute of parking time swings the ratio.
* **Headline:** Σ(trips × PT GC) ÷ Σ(trips × car GC) and the flow-weighted median of the
  pair ratios (not the mean of ratios). Second variable: GC difference, PT − car (min).
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

LABEL = ("Provisional. Commute only (daily HBW flows, AM peak-hour times; internal commuters). "
         "PT = itineraries with at least one ride; pairs under {min_km:g} km and intrazonal "
         "pairs excluded. Time-based GC; car parking/access times are a modelled range "
         "(low = none; high = centre {centre:g}, urban {urban:g}, rural {rural:g} min). Car "
         "AM peak hour is still ~10% too fast in central Bristol on held-out ANPR "
         "(validation half {anpr:.2f}), so PT's disadvantage is slightly understated.")

BANDS = [("intrazonal", None, None), ("< 1 km", 0, 1), ("1–2 km", 1, 2), ("2–5 km", 2, 5),
         ("5–10 km", 5, 10), ("> 10 km", 10, np.inf)]


def lsoa_agg(con: duckdb.DuckDBPyConnection, skim_parquet: str, cols: dict[str, str],
             ok_col: str) -> pd.DataFrame:
    """Commuter-weighted OA → LSOA values to LSOA → LSOA (origin LSOA = the OA's LSOA),
    over OA pairs where ``ok_col`` is not null. cols: output name -> expression."""
    agg = ", ".join(f"sum(({e}) * p.w) / sum(p.w) AS {k}" for k, e in cols.items())
    return con.execute(f"""
        SELECT o.LSOA21CD o_zone, s.to_id d_zone, {agg}, sum(p.w) w_reached
        FROM read_parquet('{skim_parquet}') s
        JOIN int_oa o ON o.OA21CD = s.from_id JOIN oa_w p ON p.OA21CD = s.from_id
        WHERE s.{ok_col} IS NOT NULL
        GROUP BY ALL""").df()


def build(con, pt_parquet: str, car_parquet: str, park: dict, walk: dict) -> pd.DataFrame:
    """One row per internal HBW pair: flow, distance, PT GC (ride and any-mode), car GC
    (high and low), ratios and differences."""
    con.execute("""CREATE OR REPLACE TEMP TABLE hbw AS
        SELECT o_zone, d_zone, sum(trips) trips FROM demand
        WHERE demand_version = 'p1-central' AND purpose = 'HBW' AND external IS NULL
        GROUP BY ALL HAVING sum(trips) > 0""")
    ride = lsoa_agg(con, pt_parquet, {"pt_gc": "gc_ride_min"}, "gc_ride_min")
    anym = lsoa_agg(con, pt_parquet, {"pt_gc_any": "gc_min", "walk_only_share": "walk_only_share"},
                    "gc_min")
    car = lsoa_agg(con, car_parquet, {"car_ivt": "ivt_min"}, "ivt_min")
    area = con.execute("SELECT LSOA21CD d_zone, area_type FROM lsoa_area_type").df()
    car = car.merge(area, on="d_zone", how="left")
    car["car_gc_low"] = car["car_ivt"]
    car["car_gc_high"] = car["car_ivt"] + car["area_type"].map(park).fillna(park["urban"]) \
        + car["area_type"].map(walk).fillna(walk["urban"])
    dist = con.execute("""SELECT h.o_zone, h.d_zone,
        111.32 * sqrt(pow((a.lon - b.lon) * cos(radians((a.lat + b.lat) / 2)), 2)
                      + pow(a.lat - b.lat, 2)) km
        FROM hbw h JOIN lsoa_pwc a ON a.LSOA21CD = h.o_zone
        JOIN lsoa_pwc b ON b.LSOA21CD = h.d_zone""").df()
    k = ["o_zone", "d_zone"]
    m = con.execute("SELECT * FROM hbw").df().merge(dist, on=k, how="left") \
        .merge(ride[[*k, "pt_gc"]], on=k, how="left") \
        .merge(anym[[*k, "pt_gc_any", "walk_only_share"]], on=k, how="left") \
        .merge(car[[*k, "car_gc_high", "car_gc_low", "area_type"]], on=k, how="left")
    m["intrazonal"] = m["o_zone"] == m["d_zone"]
    m["no_pt"] = m["pt_gc"].isna()
    for v in ("high", "low"):
        m[f"ratio_{v}"] = m["pt_gc"] / m[f"car_gc_{v}"]
        m[f"diff_{v}"] = m["pt_gc"] - m[f"car_gc_{v}"]
        m[f"ratio_any_{v}"] = m["pt_gc_any"] / m[f"car_gc_{v}"]
    m["band"] = band(m)
    return m


def band(m: pd.DataFrame) -> pd.Series:
    b = pd.Series("> 10 km", index=m.index)
    for name, lo, hi in BANDS[1:]:
        b[(m["km"] >= lo) & (m["km"] < hi)] = name
    b[m["intrazonal"]] = "intrazonal"
    return pd.Categorical(b, [x[0] for x in BANDS], ordered=True)


def wmedian(x: pd.Series, w: pd.Series) -> float:
    o = np.argsort(x.to_numpy())
    c = np.cumsum(w.to_numpy()[o])
    return float(x.to_numpy()[o][np.searchsorted(c, c[-1] / 2)])


def by_band_old_metric(m: pd.DataFrame) -> pd.DataFrame:
    """The superseded headline (flow-weighted mean of pair ratios, PT = any mode incl.
    walk-only) by distance band, with how much of that "PT" is walking."""
    ok = m.dropna(subset=["ratio_any_high"])
    rows = []
    for b, g in list(ok.groupby("band", observed=True)) + [("all", ok)]:
        w = g["trips"]
        rows.append({"band": b, "pairs": len(g), "trips": float(w.sum()),
                     "trip_share": float(w.sum() / ok["trips"].sum()),
                     "mean_ratio_high": float(np.average(g["ratio_any_high"], weights=w)),
                     "mean_ratio_low": float(np.average(g["ratio_any_low"], weights=w)),
                     "walk_only_share": float(np.average(g["walk_only_share"], weights=w)),
                     "trips_walk_only": float(w[g["walk_only_share"] >= 0.999].sum() / w.sum()),
                     "car_ivt_min": float(np.average(g["car_gc_low"], weights=w))})
    return pd.DataFrame(rows)


def headline(g: pd.DataFrame) -> dict:
    """Revised metric over pairs with a ride-based PT GC."""
    ok = g.dropna(subset=["pt_gc", "car_gc_low"])
    w = ok["trips"]
    out = {"pairs": int(len(ok)), "trips": float(w.sum()),
           "trips_no_pt": float(g.loc[g["no_pt"], "trips"].sum())}
    for v in ("high", "low"):
        out[f"ratio_of_sums_{v}"] = float((w * ok["pt_gc"]).sum() / (w * ok[f"car_gc_{v}"]).sum())
        out[f"median_ratio_{v}"] = wmedian(ok[f"ratio_{v}"], w)
        out[f"mean_diff_min_{v}"] = float(np.average(ok[f"diff_{v}"], weights=w))
        out[f"median_diff_min_{v}"] = wmedian(ok[f"diff_{v}"], w)
    return out


def by_band_new_metric(m: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame([{"band": b, **headline(g)}
                         for b, g in list(m.groupby("band", observed=True)) + [("all", m)]])


def in_map(m: pd.DataFrame, min_km: float) -> pd.Series:
    return ~m["intrazonal"] & (m["km"] >= min_km)


def origin_summary(g: pd.DataFrame) -> pd.DataFrame:
    """Per origin LSOA, over mapped pairs with PT: ratio of sums and mean difference."""
    ok = g.dropna(subset=["pt_gc", "car_gc_low"])
    t = ok["trips"]
    s = pd.DataFrame({"o_zone": ok["o_zone"], "trips": t, "pt": t * ok["pt_gc"],
                      "car_high": t * ok["car_gc_high"], "car_low": t * ok["car_gc_low"]}) \
        .groupby("o_zone").sum()
    return pd.DataFrame({"trips": s["trips"], "ratio_high": s["pt"] / s["car_high"],
                         "ratio_low": s["pt"] / s["car_low"],
                         "diff_high": (s["pt"] - s["car_high"]) / s["trips"],
                         "diff_low": (s["pt"] - s["car_low"]) / s["trips"]}).reset_index()


def top_pairs(g: pd.DataFrame, variant: str, by: str = "ratio", n: int = 50) -> pd.DataFrame:
    ok = g.dropna(subset=["pt_gc", "car_gc_low"])
    return ok.assign(score=ok["trips"] * ok[f"{by}_{variant}"]) \
        .sort_values("score", ascending=False).head(n)


def robustness(g: pd.DataFrame, orig: pd.DataFrame, n: int = 50) -> dict:
    """Does the parking/access range change the picture? Spearman ρ between the low and
    high rankings (pairs and origins) and the overlap of the top-n corridors."""
    ok = g.dropna(subset=["pt_gc", "car_gc_low"])
    key = lambda d: set(zip(d["o_zone"], d["d_zone"]))  # noqa: E731
    out = {"spearman_pair_ratio": float(ok["ratio_high"].corr(ok["ratio_low"], method="spearman")),
           "spearman_pair_diff": float(ok["diff_high"].corr(ok["diff_low"], method="spearman")),
           "spearman_origin_ratio": float(orig["ratio_high"].corr(orig["ratio_low"], method="spearman")),
           "spearman_origin_diff": float(orig["diff_high"].corr(orig["diff_low"], method="spearman"))}
    for by in ("ratio", "diff"):
        out[f"top{n}_overlap_trips_x_{by}"] = len(
            key(top_pairs(g, "high", by, n)) & key(top_pairs(g, "low", by, n)))
    return out
