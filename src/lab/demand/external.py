"""
External rail gateways (SPEC §11b, amended 2026-10-18).

Road is out of scope. People who live outside the extent and work inside it (and the
reverse) cross it by rail at a *gateway*: a rail station inside the extent. Their onward
(or access) leg is an ordinary internal trip from or to the gateway.

**Sizing and pattern come from different sources**, because the census alone explains
under a tenth of ORR's gateway journeys (measured 2026-10-18, sources.md):

1. **Volume** per gateway and direction: ORR's station-to-station origin–destination
   matrix (all purposes, annual) × a weekday factor. Only pairs with exactly one end at a
   gateway count; pairs between two gateways are internal.
2. **Where the onward leg goes** (inbound) or **where the access leg starts** (outbound):
   the census commute pattern of the external MSOAs that feed that gateway. Each external
   MSOA's rail commuters (HBW external rows × the 2011 WU03EW train share) are spread over
   the nearest external stations by their ORR flow, then over gateways by that station's
   ORR split; the MSOA's own LSOA pattern is carried through. A gateway with too few census
   commuters falls back to a jobs (inbound) or residents (outbound) gravity from the
   gateway, and says so in `basis`.

External trips are fixed-mode: a scheme cannot change their main mode. The table holds
the internal leg only. It is never mixed into the HBW rows, which stay tagged `external_*`
and out of analysis (SPEC §6.1), so nothing is counted twice.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .. import params, upstream
from ..config import LabConfig
from ..supply import gtfs_rail

DIRS = ("external_in", "external_out")


class ExternalError(RuntimeError):
    pass


@dataclass(frozen=True)
class Build:
    version: str
    summary: dict


def load_params(cfg: LabConfig) -> dict:
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "external.yaml")}
    return {k.split(".", 1)[1]: v for k, v in ps.items() if k.startswith("external.")}


# --- stations and ORR flows ---------------------------------------------------------

def _ref_file(darwin: Path) -> Path:
    """The Darwin reference file with the most CRS codes (not every snapshot has them)."""
    best, n = None, 0
    for f in sorted(darwin.glob("*_ref_*.xml.gz")):
        k = len(gtfs_rail.read_ref(f).crs)
        if k > n:
            best, n = f, k
    if best is None:
        raise ExternalError(f"no Darwin reference file with CRS codes under {darwin}")
    return best


def _stations(con, cfg: LabConfig, raw: Path) -> Path:
    """`station`: every station with a CRS and a NaPTAN point; `gateway`: those in the extent."""
    ref_path = _ref_file(raw / "darwin")
    ref = gtfs_rail.read_ref(ref_path)
    pts = gtfs_rail.read_naptan(raw / "naptan" / "naptan_910_rail.csv")
    rows = [(crs, pts[tpl][0], pts[tpl][1], pts[tpl][2], tpl)
            for tpl, crs in ref.crs.items() if tpl in pts and pts[tpl][3] == "active"]
    con.execute("CREATE OR REPLACE TABLE station (crs VARCHAR, lon DOUBLE, lat DOUBLE, "
                "name VARCHAR, tiploc VARCHAR)")
    con.executemany("INSERT INTO station VALUES (?,?,?,?,?)", rows)
    x0, y0, x1, y1 = cfg.extent
    con.execute(f"""CREATE OR REPLACE TABLE gateway AS
        SELECT * FROM station WHERE lon BETWEEN {x0} AND {x1} AND lat BETWEEN {y0} AND {y1}
          AND crs IN (SELECT o FROM odm UNION SELECT d FROM odm)""")
    return ref_path


def _odm(con, path: Path) -> None:
    con.execute(f"""CREATE OR REPLACE TABLE odm AS
        SELECT origin_tlc AS o, destination_tlc AS d, CAST(journeys AS DOUBLE) AS j
        FROM read_csv('{path}')""")


def _gateway_flows(con, max_unlocated: float) -> dict:
    """ORR pairs with exactly one end at a gateway, by direction and external station."""
    con.execute("""CREATE OR REPLACE TABLE gateway_flow AS
        SELECT CASE WHEN d IN (SELECT crs FROM gateway) THEN 'external_in' ELSE 'external_out' END AS dir,
               CASE WHEN d IN (SELECT crs FROM gateway) THEN o ELSE d END AS ext_crs,
               CASE WHEN d IN (SELECT crs FROM gateway) THEN d ELSE o END AS gateway,
               sum(j) AS journeys
        FROM odm
        WHERE (o IN (SELECT crs FROM gateway)) <> (d IN (SELECT crs FROM gateway))
        GROUP BY ALL""")
    tot, lost, names = con.execute("""
        SELECT sum(journeys), sum(journeys) FILTER (WHERE ext_crs NOT IN (SELECT crs FROM station)),
               list(DISTINCT ext_crs) FILTER (WHERE ext_crs NOT IN (SELECT crs FROM station))
        FROM gateway_flow""").fetchone()
    if not tot:
        raise ExternalError("no ORR pair has exactly one end at a gateway")
    share = (lost or 0) / tot
    if share > max_unlocated:
        raise ExternalError(
            f"{share:.1%} of gateway journeys start or end at a station with no NaPTAN point "
            f"(limit {max_unlocated:.1%}): {sorted(names)[:20]}")
    return {"journeys": tot, "unlocated_share": share, "unlocated_stations": len(names or [])}


# --- census: rail share and origin stations -----------------------------------------

def _rail_share(con, wu: Path, ps: dict, base: str) -> dict:
    """Train share of each external MSOA's non-home commuters to or from the extent (2011).

    Train only: the tram/metro column is a local mode and cannot reach a gateway.
    Below `rail_share_min_n` commuters the pooled share of the MSOA's distance band is used.
    """
    cuts = list(ps["distance_bands_km"])
    con.execute(f"""CREATE OR REPLACE TABLE ext_rail_share AS
        WITH inside AS (SELECT DISTINCT MSOA21CD AS m FROM int_lsoa),
        wu AS (SELECT * FROM read_csv('{wu}')),
        pair AS (
            SELECT CASE WHEN "Area of workplace" IN (SELECT m FROM inside)
                        THEN 'external_in' ELSE 'external_out' END AS dir,
                   CASE WHEN "Area of workplace" IN (SELECT m FROM inside)
                        THEN "Area of residence" ELSE "Area of workplace" END AS msoa,
                   sum("Train") AS train,
                   sum("All categories: Method of travel to work"
                       - "Work mainly at or from home") AS n
            FROM wu
            WHERE ("Area of workplace" IN (SELECT m FROM inside))
               <> ("Area of residence" IN (SELECT m FROM inside))
            GROUP BY ALL),
        ext AS (
            SELECT CASE d.external WHEN 'external_in' THEN d.o_zone ELSE d.d_zone END AS msoa,
                   d.external AS dir, sum(d.trips) AS trips
            FROM demand d
            WHERE d.demand_version='{base}' AND d.external IN ('external_in','external_out')
              AND CASE d.external WHEN 'external_in' THEN d.o_level ELSE d.d_level END = 'MSOA'
            GROUP BY ALL),
        banded AS (
            SELECT e.*, p.train, p.n,
                   len(list_filter({cuts}, c -> c <= coalesce(x.km, 0))) AS band
            FROM ext e LEFT JOIN pair p USING (msoa, dir)
            LEFT JOIN hbw_ext_dist x ON x.msoa = e.msoa),
        pooled AS (
            SELECT dir, band, sum(train) / nullif(sum(n), 0) AS share
            FROM banded WHERE n >= {ps["rail_share_min_n"]} GROUP BY ALL)
        SELECT b.msoa, b.dir, b.trips, b.band, b.n,
               CASE WHEN b.n >= {ps["rail_share_min_n"]} THEN b.train / b.n ELSE p.share END AS share,
               CASE WHEN b.n >= {ps["rail_share_min_n"]} THEN 'msoa' ELSE 'band' END AS basis
        FROM banded b LEFT JOIN pooled p USING (dir, band)""")
    missing = con.execute("SELECT count(*) FROM ext_rail_share WHERE share IS NULL").fetchone()[0]
    if missing:
        raise ExternalError(f"{missing} external MSOAs have no rail share, even from their band")
    rows = con.execute("""SELECT dir, basis, count(*), sum(trips), sum(trips*share)
                          FROM ext_rail_share GROUP BY ALL ORDER BY ALL""").fetchall()
    return {f"{d}:{b}": {"msoas": n, "commuters": t, "rail_commuters": r} for d, b, n, t, r in rows}


def _origin_weights(con, ps: dict) -> None:
    """Each external MSOA's rail commuters over its `origin_station_k` nearest stations
    that have ORR flow to a gateway, weighted by that flow ÷ (distance + offset)²."""
    k, off = int(ps["origin_station_k"]), float(ps["distance_offset_km"])
    con.execute(f"""CREATE OR REPLACE TABLE ext_origin_weight AS
        WITH tot AS (SELECT dir, ext_crs AS crs, sum(journeys) AS tot FROM gateway_flow GROUP BY ALL),
        cand AS (
            SELECT r.msoa, r.dir, t.crs, t.tot,
                   111.32*sqrt(pow((m.lon-s.lon)*cos(radians(m.lat)),2) + pow(m.lat-s.lat,2)) AS km
            FROM ext_rail_share r JOIN up.msoa_pwc m ON m.MSOA21CD = r.msoa
            JOIN tot t ON t.dir = r.dir JOIN station s ON s.crs = t.crs),
        near AS (SELECT *, tot / pow(km + {off}, 2) AS raw FROM cand
                 QUALIFY row_number() OVER (PARTITION BY msoa, dir ORDER BY km) <= {k})
        SELECT msoa, dir, crs, km, raw / sum(raw) OVER (PARTITION BY msoa, dir) AS w FROM near""")
    n = con.execute("""SELECT count(*) FROM ext_rail_share r WHERE NOT EXISTS (
        SELECT 1 FROM ext_origin_weight w WHERE w.msoa=r.msoa AND w.dir=r.dir)""").fetchone()[0]
    if n:
        raise ExternalError(f"{n} external MSOAs have no origin station (no MSOA centroid?)")


# --- the demand ---------------------------------------------------------------------

def _demand(con, ps: dict, base: str, version: str, landuse: str) -> dict:
    lam, min_c, wd = (float(ps["fallback_decay_km"]), float(ps["gateway_min_commuters"]),
                      float(ps["weekday_factor"]))
    con.execute("""CREATE OR REPLACE TABLE gateway_commuters AS
        WITH st AS (SELECT dir, ext_crs, sum(journeys) AS tot FROM gateway_flow GROUP BY ALL)
        SELECT r.dir, r.msoa, f.gateway, r.trips * r.share * w.w * f.journeys / st.tot AS rail
        FROM ext_rail_share r
        JOIN ext_origin_weight w USING (msoa, dir)
        JOIN gateway_flow f ON f.dir = r.dir AND f.ext_crs = w.crs
        JOIN st ON st.dir = f.dir AND st.ext_crs = f.ext_crs""")
    con.execute(f"""CREATE OR REPLACE TABLE msoa_zone_share AS
        WITH z AS (
            SELECT external AS dir,
                   CASE external WHEN 'external_in' THEN o_zone ELSE d_zone END AS msoa,
                   CASE external WHEN 'external_in' THEN d_zone ELSE o_zone END AS zone,
                   sum(trips) AS t
            FROM demand WHERE demand_version='{base}' AND external IN ('external_in','external_out')
              AND CASE external WHEN 'external_in' THEN d_level ELSE o_level END = 'LSOA'
            GROUP BY ALL)
        SELECT dir, msoa, zone, t / sum(t) OVER (PARTITION BY dir, msoa) AS sh FROM z""")
    con.execute("""CREATE OR REPLACE TABLE gateway_pattern AS
        SELECT c.dir, c.gateway, z.zone, sum(c.rail * z.sh) AS commuters
        FROM gateway_commuters c JOIN msoa_zone_share z USING (dir, msoa) GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TABLE gateway_total AS
        SELECT dir, gateway, sum(journeys) * {wd} AS trips,
               coalesce((SELECT sum(commuters) FROM gateway_pattern p
                         WHERE p.dir=f.dir AND p.gateway=f.gateway), 0) AS commuters
        FROM gateway_flow f GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TABLE fallback_weight AS
        WITH lsoa AS (
            SELECT l.LSOA21CD AS zone, l.lon, l.lat, sum(u.jobs) AS jobs, sum(u.residents) AS res
            FROM int_lsoa l JOIN landuse u ON u.parent_id = l.LSOA21CD
            WHERE u.landuse_version='{landuse}' AND u.level='OA' GROUP BY ALL)
        SELECT t.dir, t.gateway, z.zone,
               CASE t.dir WHEN 'external_in' THEN z.jobs ELSE z.res END
                 * exp(-111.32*sqrt(pow((z.lon-g.lon)*cos(radians(g.lat)),2) + pow(z.lat-g.lat,2)) / {lam}) AS w
        FROM gateway_total t JOIN gateway g ON g.crs = t.gateway CROSS JOIN lsoa z
        WHERE t.commuters < {min_c}""")
    con.execute("""CREATE TABLE IF NOT EXISTS demand_gateway (
        demand_version VARCHAR, dir VARCHAR, gateway VARCHAR, zone VARCHAR, trips DOUBLE,
        basis VARCHAR)""")
    con.execute("DELETE FROM demand_gateway WHERE demand_version=?", [version])
    con.execute(f"""INSERT INTO demand_gateway
        SELECT '{version}', t.dir, t.gateway, p.zone,
               t.trips * p.commuters / sum(p.commuters) OVER (PARTITION BY t.dir, t.gateway), 'census'
        FROM gateway_total t JOIN gateway_pattern p USING (dir, gateway)
        WHERE t.commuters >= {min_c}
        UNION ALL
        SELECT '{version}', t.dir, t.gateway, f.zone,
               t.trips * f.w / sum(f.w) OVER (PARTITION BY t.dir, t.gateway), 'fallback'
        FROM gateway_total t JOIN fallback_weight f USING (dir, gateway)""")
    rows = con.execute("""
        SELECT t.dir, t.gateway, t.trips, t.commuters,
               coalesce((SELECT sum(trips) FROM demand_gateway d WHERE d.demand_version=?
                         AND d.dir=t.dir AND d.gateway=t.gateway), 0) AS placed,
               coalesce((SELECT any_value(basis) FROM demand_gateway d WHERE d.demand_version=?
                         AND d.dir=t.dir AND d.gateway=t.gateway), 'none') AS basis
        FROM gateway_total t ORDER BY t.trips DESC""", [version, version]).fetchall()
    bad = [(d, g, t, p) for d, g, t, c, p, b in rows if abs(t - p) > 1e-6 * max(t, 1)]
    if bad:
        raise ExternalError(f"gateway trips not reconciled to the ORR total: {bad[:5]}")
    return {"gateways": [{"dir": d, "gateway": g, "weekday_trips": t, "census_commuters": c,
                          "census_share": c / t if t else 0.0, "basis": b}
                         for d, g, t, c, p, b in rows]}


def run(cfg: LabConfig, con: duckdb.DuckDBPyConnection | None = None, *,
        base_version: str = "p1-central", landuse: str, ps: dict | None = None) -> Build:
    ps = ps or load_params(cfg)
    raw = cfg.root / "data" / "raw"
    need = [raw / "orr" / "ODM_for_RDM_2024-25.csv", raw / "census2011" / "wu03ew" / "wu03ew_msoa.csv",
            raw / "naptan" / "naptan_910_rail.csv"]
    for p in need:
        if not p.is_file():
            raise ExternalError(f"missing input {p}; see sources.md (external gateways)")
    own = con is None
    con = con or duckdb.connect(str(cfg.lab_db))
    try:
        if not con.execute("SELECT 1 FROM duckdb_databases() WHERE database_name='up'").fetchall():
            upstream.attach(con, cfg)
        _odm(con, need[0])
        ref = _stations(con, cfg, raw)
        flows = _gateway_flows(con, float(ps["max_unlocated_share"]))
        shares = _rail_share(con, need[1], ps, base_version)
        _origin_weights(con, ps)
        version = "x1-" + base_version.split("-", 1)[-1]
        out = _demand(con, ps, base_version, version, landuse)
        summary = {"built": dt.date.today().isoformat(), "darwin_ref": ref.name,
                   "stations_with_points": con.execute("SELECT count(*) FROM station").fetchone()[0],
                   "gateway_count": con.execute("SELECT count(*) FROM gateway").fetchone()[0],
                   "orr": flows, "rail_share": shares, **out}
        return Build(version, summary)
    finally:
        if own:
            con.close()
