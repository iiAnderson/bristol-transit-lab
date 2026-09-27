"""
Attach authority, zone, area type, road class, direction and B1 flags to car segments.

* authority: LAD (Dec 2024) polygon containing the segment midpoint;
* zone and area type: internal LSOA polygon containing the midpoint; centre / urban /
  rural from ONS RUC 2021 (LSOA) and the D4 centre rule; segments outside internal zones
  (the clip buffer) are ``buffer``;
* class: DfT's own category for the road in that authority, from AADF count points
  (TM/TA → ``srn``, PM/PA → ``local_a``); roads with no count point fall back to OSM
  (motorway/trunk → ``srn``, other ``ref`` A… → ``local_a``); ``B`` refs → ``b_road``;
  the rest ``minor``. OSM/DfT disagreements are listed (D5);
* direction: ``inbound`` if the segment ends nearer the nearest attractor than it starts;
* B1: any bus-lane / PSV tag on the way.
"""
from __future__ import annotations

from pathlib import Path

import duckdb


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
             out: Path) -> dict:
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
               d.dft,
               CASE WHEN d.dft IS NOT NULL THEN d.dft
                    WHEN s.highway IN ('motorway', 'motorway_link', 'trunk', 'trunk_link') THEN 'srn'
                    WHEN s.ref LIKE 'A%' THEN 'local_a'
                    WHEN s.ref LIKE 'B%' THEN 'b_road' ELSE 'minor' END road_class,
               s.bus_tags IS NOT NULL bus_flag
        FROM s JOIN near n USING (way_id, seq, forward)
        LEFT JOIN _dftclass d ON d.lad = s.lad AND d.road_ref = s.ref""", [centre_lsoas])
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY _ann TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    return {
        "segments": q("SELECT count(*) FROM _ann")[0][0],
        "by_class_km": q("SELECT road_class, round(sum(length_m)/1000) FROM _ann GROUP BY 1 ORDER BY 2 DESC"),
        "by_area_km": q("SELECT area_type, round(sum(length_m)/1000) FROM _ann GROUP BY 1 ORDER BY 2 DESC"),
        "no_authority_km": q("SELECT round(sum(length_m)/1000) FROM _ann WHERE lad IS NULL")[0][0],
        "bus_flag_km": q("SELECT round(sum(length_m)/1000, 1) FROM _ann WHERE bus_flag")[0][0],
        "d5_osm_trunk_but_dft_local": q("""SELECT lad, ref, round(sum(length_m)/1000, 1) FROM _ann
            WHERE highway IN ('trunk', 'trunk_link', 'motorway', 'motorway_link') AND dft = 'local_a'
            GROUP BY ALL ORDER BY 3 DESC"""),
        "d5_osm_nontrunk_but_dft_srn": q("""SELECT lad, ref, round(sum(length_m)/1000, 1) FROM _ann
            WHERE highway NOT IN ('trunk', 'trunk_link', 'motorway', 'motorway_link') AND dft = 'srn'
            GROUP BY ALL ORDER BY 3 DESC"""),
    }
