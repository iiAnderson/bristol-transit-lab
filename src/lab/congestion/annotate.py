"""
Attach authority, zone, area type, road class, direction and B1 flags to car segments.

* authority: LAD (Dec 2024) polygon containing the segment midpoint;
* zone and area type: internal LSOA polygon containing the midpoint; centre / urban /
  rural from ONS RUC 2021 (LSOA) and the D4 centre rule; segments outside internal zones
  (the clip buffer) are ``buffer``;
* count point: the nearest 2025 AADF count point on the same road (``ref``) in the same
  authority, within ``cp_max_m``; it gives the segment its two-way AADF (flow weight for
  the all-day target) and DfT category;
* class: that count point's category (TM/TA → ``srn``, PM/PA → ``local_a``), so a road
  that changes hands part-way (Newport's A4042) splits correctly; without one, the
  road's category in that authority, then OSM (motorway/trunk → ``srn``, other ``ref``
  A… → ``local_a``); ``B`` refs → ``b_road``; the rest ``minor``. OSM/DfT
  disagreements are listed (D5);
* direction: ``inbound`` if the segment ends nearer the nearest attractor than it starts;
* B1: any bus-lane / PSV tag on the way.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np


def centre_lsoas(con, lsoa_geojson: Path, ruc_csv: Path, threshold: float,
                 min_cluster: int) -> list[str]:
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _d AS
        SELECT g.LSOA21CD, g.geom, coalesce(b.jobs, 0) / (g.Shape__Area / 1e6) dens,
               r.Urban_rural_flag urb
        FROM ST_Read('{lsoa_geojson}') g LEFT JOIN lab.nat_bres b USING (LSOA21CD)
        LEFT JOIN read_csv('{ruc_csv}') r USING (LSOA21CD)""")
    cand = [r[0] for r in con.execute(
        f"SELECT LSOA21CD FROM _d WHERE urb = 'Urban' AND dens >= {threshold}").fetchall()]
    edges = con.execute(f"""SELECT a.LSOA21CD, b.LSOA21CD FROM _d a JOIN _d b
        ON a.LSOA21CD < b.LSOA21CD AND ST_Intersects(a.geom, b.geom)
        WHERE a.LSOA21CD IN (SELECT unnest(?)) AND b.LSOA21CD IN (SELECT unnest(?))""",
                        [cand, cand]).fetchall()
    par = {c: c for c in cand}

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for a, b in edges:
        par[f(a)] = f(b)
    groups: dict[str, list[str]] = {}
    for c in cand:
        groups.setdefault(f(c), []).append(c)
    return sorted(c for g in groups.values() if len(g) >= min_cluster for c in g)


def annotate(con, segments: Path, lad_geojson: Path, lsoa_geojson: Path, ruc_csv: Path,
             aadf_parquet: Path, centres: list[dict], centre_lsoas: list[str],
             out: Path, cp_max_m: float = 2000) -> dict:
    cen = " UNION ALL ".join(f"SELECT '{c['name']}' centre, {c['lon']} lon, {c['lat']} lat"
                             for c in centres)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _seg AS
        SELECT *, (lon_u + lon_v) / 2 mlon, (lat_u + lat_v) / 2 mlat,
               ST_Point((lon_u + lon_v) / 2, (lat_u + lat_v) / 2) mid
        FROM read_parquet('{segments}')""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _lad AS
        SELECT LAD24CD, LAD24NM, geom FROM ST_Read('{lad_geojson}')""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _lsoa AS
        SELECT g.LSOA21CD, g.geom, r.Urban_rural_flag urb FROM ST_Read('{lsoa_geojson}') g
        LEFT JOIN read_csv('{ruc_csv}') r USING (LSOA21CD)""")
    # DfT class by (authority, road name) from 2025 AADF count points
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _dftclass AS
        SELECT local_authority_code lad, road_name road_ref,
               CASE WHEN bool_or(road_category IN ('TM', 'TA')) THEN 'srn'
                    WHEN bool_or(road_category IN ('PM', 'PA')) THEN 'local_a' END dft
        FROM read_parquet('{aadf_parquet}') WHERE year = 2025 GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _cp AS
        SELECT count_point_id cp_id, local_authority_code lad, road_name road_ref,
               any_value(road_category) cp_category, any_value(longitude) lon,
               any_value(latitude) lat, sum(all_motor_vehicles)::DOUBLE aadf_2way,
               any_value(link_length_km) link_length_km
        FROM read_parquet('{aadf_parquet}') WHERE year = 2025 GROUP BY 1, 2, 3""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _segcp AS
        SELECT s.way_id, s.seq, s.forward,
               arg_min(c.cp_id, (s.mlon - c.lon)^2 * 0.39 + (s.mlat - c.lat)^2) cp_id,
               sqrt(min((s.mlon - c.lon)^2 * 0.39 + (s.mlat - c.lat)^2)) * 111320 cp_dist_m
        FROM (SELECT s.*, l.LAD24CD lad FROM _seg s LEFT JOIN _lad l ON ST_Contains(l.geom, s.mid)
              WHERE s.ref IS NOT NULL) s
        JOIN _cp c ON c.lad = s.lad AND c.road_ref = s.ref
        GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _ann AS
        WITH s AS (
          SELECT s.*, l.LAD24CD lad, z.LSOA21CD lsoa, z.urb
          FROM _seg s
          LEFT JOIN _lad l ON ST_Contains(l.geom, s.mid)
          LEFT JOIN _lsoa z ON ST_Contains(z.geom, s.mid)),
        c AS (SELECT * FROM ({cen})),
        near AS (
          SELECT s.way_id, s.seq, s.forward,
                 arg_min(c.centre, (s.mlon - c.lon)^2 * 0.39 + (s.mlat - c.lat)^2) centre,
                 arg_min((s.lon_v - c.lon)^2 * 0.39 + (s.lat_v - c.lat)^2
                         - (s.lon_u - c.lon)^2 * 0.39 - (s.lat_u - c.lat)^2,
                         (s.mlon - c.lon)^2 * 0.39 + (s.mlat - c.lat)^2) closer
          FROM s, c GROUP BY ALL)
        SELECT s.* EXCLUDE (mid, urb), n.centre,
               CASE WHEN n.closer < 0 THEN 'inbound' ELSE 'outbound' END direction,
               CASE WHEN s.lsoa IS NULL THEN 'buffer'
                    WHEN s.lsoa IN (SELECT unnest(?)) THEN 'centre'
                    WHEN s.urb = 'Urban' THEN 'urban' ELSE 'rural' END area_type,
               d.dft, k.cp_id, k.cp_dist_m, c2.cp_category, c2.aadf_2way,
               CASE WHEN k.cp_dist_m <= {cp_max_m} AND c2.cp_category IN ('TM', 'TA') THEN 'srn'
                    WHEN k.cp_dist_m <= {cp_max_m} AND c2.cp_category IN ('PM', 'PA') THEN 'local_a'
                    WHEN d.dft IS NOT NULL THEN d.dft
                    WHEN s.highway IN ('motorway', 'motorway_link', 'trunk', 'trunk_link') THEN 'srn'
                    WHEN s.ref LIKE 'A%' THEN 'local_a'
                    WHEN s.ref LIKE 'B%' THEN 'b_road' ELSE 'minor' END road_class,
               s.bus_tags IS NOT NULL bus_flag
        FROM s JOIN near n USING (way_id, seq, forward)
        LEFT JOIN _dftclass d ON d.lad = s.lad AND d.road_ref = s.ref
        LEFT JOIN _segcp k USING (way_id, seq, forward)
        LEFT JOIN _cp c2 ON c2.cp_id = k.cp_id""", [centre_lsoas])
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY _ann TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    return {
        "segments": q("SELECT count(*) FROM _ann")[0][0],
        "by_class_km": q("SELECT road_class, round(sum(length_m)/1000) FROM _ann GROUP BY 1 ORDER BY 2 DESC"),
        "by_area_km": q("SELECT area_type, round(sum(length_m)/1000) FROM _ann GROUP BY 1 ORDER BY 2 DESC"),
        "no_authority_km": q("SELECT round(sum(length_m)/1000) FROM _ann WHERE lad IS NULL")[0][0],
        "bus_flag_km": q("SELECT round(sum(length_m)/1000, 1) FROM _ann WHERE bus_flag")[0][0],
        "local_a_srn_km_with_count_point": q(f"""SELECT road_class, round(sum(length_m)/1000),
            round(sum(length_m) FILTER (WHERE cp_dist_m <= {cp_max_m})/1000) FROM _ann
            WHERE road_class IN ('local_a', 'srn') GROUP BY 1"""),
        "roads_split_by_count_point": q("""SELECT lad, ref, list(DISTINCT road_class) FROM _ann
            WHERE ref LIKE 'A%' AND road_class IN ('local_a', 'srn') GROUP BY ALL
            HAVING count(DISTINCT road_class) > 1 ORDER BY 1, 2"""),
        "d5_osm_trunk_but_dft_local": q("""SELECT lad, ref, round(sum(length_m)/1000, 1) FROM _ann
            WHERE highway IN ('trunk', 'trunk_link', 'motorway', 'motorway_link') AND dft = 'local_a'
            GROUP BY ALL ORDER BY 3 DESC"""),
        "d5_osm_nontrunk_but_dft_srn": q("""SELECT lad, ref, round(sum(length_m)/1000, 1) FROM _ann
            WHERE highway NOT IN ('trunk', 'trunk_link', 'motorway', 'motorway_link') AND dft = 'srn'
            GROUP BY ALL ORDER BY 3 DESC"""),
    }


def apply_propagation(con, annotated: Path, out: Path) -> dict:
    """Replace road class (and the count point used for flow weights) on A-roads and
    motorways with the along-road propagation from DfT count points (road_class.py)."""
    import pandas as pd

    from . import road_class as rc
    seg = pd.read_parquet(annotated)
    cps = con.execute("SELECT cp_id, road_ref, cp_category, lon, lat, link_length_km FROM _cp").df()
    prop, bounds = rc.propagate(seg, cps)
    seg = seg.merge(prop.rename(columns={"cp_id": "cp_id_road"}),
                    on=["way_id", "seq", "forward"], how="left")
    osm = np.where(seg["highway"].isin(["motorway", "motorway_link", "trunk", "trunk_link"]),
                   "srn", "local_a")
    numbered = seg["ref"].fillna("").str.match(r"^[AM]\d")
    seg["class_basis"] = np.where(numbered, seg["basis"].fillna("osm_fallback"), None)
    seg["road_class"] = np.where(numbered & seg["dft_class"].notna(), seg["dft_class"],
                                 np.where(numbered, osm, seg["road_class"]))
    cpw = con.execute("SELECT cp_id, aadf_2way FROM _cp").df().set_index("cp_id")["aadf_2way"]
    seg["aadf_2way"] = np.where(seg["cp_id_road"].notna(),
                                seg["cp_id_road"].map(cpw), seg["aadf_2way"])
    seg = seg.drop(columns=["basis", "dft_class"])
    seg.to_parquet(out, compression="zstd")
    a = seg[numbered & seg["ref"].str.startswith("A")]
    km = a.groupby("class_basis")["length_m"].sum() / 1000
    return {"a_road_km_by_basis": km.round(1).to_dict(),
            "a_road_share_by_basis": (km / km.sum()).round(3).to_dict(),
            "class_boundaries": bounds,
            "by_class_km_after": (seg.groupby("road_class")["length_m"].sum() / 1000)
            .round(0).to_dict()}
