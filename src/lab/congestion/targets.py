"""
DfT all-day local-A-road targets per authority × road, and what is needed to model them
like for like (plans/P2.md §5 "as built").

* Targets: CGN0503e (England) and CGN0509c (Wales), 2025, mph → km/h.
* Coverage rule: a target is used only if at least ``min_coverage`` of that road's
  length (DfT-local A-road segments of that ``ref`` in that authority, from the full
  merged OSM) lies inside the modelled clip box; otherwise it describes road we don't
  model and is dropped, listed.
* Flow weights: each segment takes the 2025 AADF (all motor vehicles, both directions
  unless the count point is directional) of the nearest count point on the same road in
  the same authority.
* Time profile: TRA0307 — traffic index by hour × day of week (GB, all roads), the
  latest year, normalised to shares of the week.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def dft_targets(cgn0503: Path, cgn0509: Path, year_col: str = "2025") -> pd.DataFrame:
    e = pd.read_excel(cgn0503, engine="odf", sheet_name="CGN0503e", header=3)
    w = pd.read_excel(cgn0509, engine="odf", sheet_name="CGN0509c", header=3)
    ecol = [c for c in e.columns if str(c).startswith(year_col)][0]
    wcol = [c for c in w.columns if str(c).startswith(year_col)][0]
    out = pd.concat([
        pd.DataFrame({"lad": e["ONS Area Code"], "authority": e["Local Authority"],
                      "ref": e["Road Name"], "mph": pd.to_numeric(e[ecol], errors="coerce")}),
        pd.DataFrame({"lad": w["ONS Area Code"], "authority": w["Local Authority"],
                      "ref": w["Road Name"], "mph": pd.to_numeric(w[wcol], errors="coerce")}),
    ])
    out["kmh"] = out["mph"] * 1.609344
    return out.dropna(subset=["kmh"]).reset_index(drop=True)


def tra0307_profile(path: Path) -> tuple[int, pd.DataFrame]:
    x = pd.read_excel(path, engine="odf", sheet_name="TRA0307", header=4)
    x.columns = [str(c).strip() for c in x.columns]
    x["Year"] = pd.to_numeric(x["Year"], errors="coerce")
    year = int(x["Year"].max())
    y = x[x["Year"] == year]
    rows = []
    for _, r in y.iterrows():
        h = int(str(r["Time of Day"]).strip()[:2])
        for i, d in enumerate(DAYS):
            rows.append({"dow": i, "hour": h, "index": float(r[d])})
    p = pd.DataFrame(rows)
    p["share"] = p["index"] / p["index"].sum()
    return year, p


def coverage(con: duckdb.DuckDBPyConnection, full_main: Path, lad_geojson: Path,
             aadf_parquet: Path, box: tuple) -> pd.DataFrame:
    """Share of each authority × DfT-local A road's length inside the clip box."""
    x0, y0, x1, y1 = box
    con.execute("INSTALL spatial; LOAD spatial")
    return con.execute(f"""
        WITH lad AS (SELECT LAD24CD, geom FROM ST_Read('{lad_geojson}')),
        dft AS (SELECT DISTINCT local_authority_code lad, road_name road_ref
                FROM read_parquet('{aadf_parquet}') WHERE year = 2025 AND road_category IN ('PA', 'PM')),
        s AS (SELECT ref road_ref, length_m, (lon_u + lon_v) / 2 x, (lat_u + lat_v) / 2 y
              FROM read_parquet('{full_main}') WHERE ref LIKE 'A%' AND forward),
        s2 AS (SELECT s.*, lad.LAD24CD lad FROM s JOIN lad ON ST_Contains(lad.geom, ST_Point(s.x, s.y)))
        SELECT s2.lad, s2.road_ref, sum(length_m) / 1000 km_total,
               sum(length_m) FILTER (WHERE x BETWEEN {x0} AND {x1} AND y BETWEEN {y0} AND {y1}) / 1000 km_inside
        FROM s2 GROUP BY ALL ORDER BY 1, 2""").df()
