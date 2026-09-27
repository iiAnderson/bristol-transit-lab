"""
P1 — analysis-grade commute (HBW) demand (SPEC §6.1).

Built from upstream's raw OA → OA `flows`, never from its game-adjusted tables:

1. **Base matrix.** LSOA → MSOA for internal origins, MSOA → MSOA for external ones.
   Each row is tagged `external` = NULL (both ends in the extent), 'external_in' or
   'external_out', with `ext_dist_km` from the external MSOA's population-weighted
   centroid to the nearest in-map OA centroid (upstream's own distance definition).
   Nothing is cut, clamped or spread.
2. **Lockdown correction** by upstream `covid.correct` — the same code the game path
   runs — with the BRES discount at low / central / high, and the cap decided by
   whether it binds. External_out destinations lie outside the extent, have no BRES
   row, and are kept unrescaled.
3. **Destination split** MSOA → LSOA by BRES LSOA jobs. Assumption: within an MSOA
   every origin gets the same destination pattern.
4. **CA / NCA split** by ODWP14EW, applied after the correction, at the finest level
   that clears the noise threshold. Assumption: redistributed no-fixed-place workers
   share their origin's car-availability split.

Everything is written to the lab DB; the upstream DB is attached read-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .. import upstream
from ..config import LabConfig
from . import bres_discount

PURPOSE = "HBW"
PERIOD = "DAY"            # daily totals; split into periods in P4
VARIANTS = ("low", "central", "high")
# [MODELLED] minimum MSOA→MSOA flow (all car-availability categories) for a pair's own
# CA share to be used; below it, the origin MSOA's share. Upstream measured MSOA→MSOA
# at a mean of 21.95 with 2.0% of commuters in flows ≤ 2.
CA_PAIR_MIN = 10


class DemandError(RuntimeError):
    pass


@dataclass(frozen=True)
class Build:
    discounts: dict[str, float]
    cap: dict[str, float | None]
    summaries: dict[str, dict]
    checks: list[tuple[str, float, float, bool]]


def _extent_views(con, bbox) -> None:
    """In-map geography, derived from upstream's raw tables only."""
    min_lon, min_lat, max_lon, max_lat = bbox
    con.execute("""CREATE OR REPLACE TEMP VIEW ext_oa AS
                   SELECT OA21CD, LSOA21CD, MSOA21CD FROM up.oa_extent""")
    # OAs whose population-weighted centroid is inside the bbox — the same set as
    # upstream's `oa_points`, derived independently so no game table is read.
    con.execute(f"""CREATE OR REPLACE TEMP VIEW inmap_pwc AS
        SELECT p.OA21CD, p.lon, p.lat FROM up.pwc p
        WHERE p.OA21CD IN (SELECT OA21CD FROM ext_oa)
          AND p.lon BETWEEN {min_lon} AND {max_lon}
          AND p.lat BETWEEN {min_lat} AND {max_lat}""")


def build_base(con) -> None:
    # Upstream's `flows` labels every external end's LSOA and MSOA as the literal
    # 'EXT', so external codes come from the raw `oa_flows` and the national lookup
    # `oa_lu_all`. Workplaces in Scotland and Northern Ireland are not in that lookup
    # (England and Wales only); they are kept at country level, with no distance.
    con.execute("""
        CREATE OR REPLACE TEMP VIEW oa_geo AS
        SELECT OA21CD, LSOA21CD, MSOA21CD FROM up.oa_lu_all
    """)
    con.execute("""
        CREATE OR REPLACE TABLE hbw_base AS
        WITH f AS (
            SELECT f.n, f.o_oa, f.d_oa,
                   f.o_oa IN (SELECT OA21CD FROM ext_oa) AS o_in,
                   f.d_oa IN (SELECT OA21CD FROM ext_oa) AS d_in,
                   lo.LSOA21CD AS o_lsoa, lo.MSOA21CD AS o_msoa,
                   COALESCE(ld.MSOA21CD,
                            CASE WHEN f.d_oa LIKE 'S%' THEN 'S92000003'
                                 WHEN f.d_oa LIKE 'N%' THEN 'N92000002' END) AS d_msoa
            FROM up.oa_flows f
            LEFT JOIN oa_geo lo ON lo.OA21CD=f.o_oa
            LEFT JOIN oa_geo ld ON ld.OA21CD=f.d_oa)
        SELECT CASE WHEN o_in THEN o_lsoa ELSE o_msoa END AS o_zone,
               CASE WHEN o_in THEN 'LSOA' ELSE 'MSOA' END AS o_level,
               o_msoa, d_msoa,
               CASE WHEN d_msoa IN ('S92000003','N92000002') THEN 'COUNTRY'
                    ELSE 'MSOA' END AS d_msoa_level,
               CASE WHEN o_in AND d_in THEN NULL
                    WHEN d_in THEN 'external_in' ELSE 'external_out' END AS external,
               sum(n)::DOUBLE AS n
        FROM f GROUP BY ALL
    """)
    bad = con.execute("""SELECT count(*), COALESCE(sum(n),0) FROM hbw_base
                         WHERE o_zone IS NULL OR o_msoa IS NULL OR d_msoa IS NULL""").fetchone()
    if bad[0]:
        raise DemandError(f"{bad[0]} base rows ({bad[1]:,.0f} commuters) have an "
                          "unmapped origin or destination")
    # Distance from each external MSOA to the map: its PWC to the nearest in-map OA
    # PWC, planar-approximate exactly as upstream's clamp computes it.
    con.execute("""
        CREATE OR REPLACE TABLE hbw_ext_dist AS
        WITH ext AS (
            SELECT DISTINCT CASE WHEN external='external_in' THEN o_msoa ELSE d_msoa END AS msoa
            FROM hbw_base WHERE external IS NOT NULL)
        SELECT e.msoa,
               min(111320*sqrt(pow((w.lon-m.lon)*cos(radians(m.lat)),2)
                             + pow(w.lat-m.lat,2))) / 1000 AS km
        FROM ext e JOIN up.msoa_pwc m ON m.MSOA21CD=e.msoa CROSS JOIN inmap_pwc w
        GROUP BY 1
    """)
    missing = con.execute("""
        SELECT count(*) FROM hbw_base b WHERE external IS NOT NULL
          AND d_msoa_level = 'MSOA'
          AND CASE WHEN external='external_in' THEN o_msoa ELSE d_msoa END
              NOT IN (SELECT msoa FROM hbw_ext_dist)""").fetchone()[0]
    if missing:
        raise DemandError(f"{missing} external rows have no MSOA centroid to measure from")


def _inputs_for_correct(con, frac: float, *, quirk: bool = False) -> None:
    """No-fixed-place workers by LSOA, and BRES by MSOA, as `correct` expects.

    `quirk=True` reproduces upstream's game path, which counts no-fixed-place workers
    only for OAs whose centroid is in the map (1,963 people in the TS058 column are
    lost from the folded edge OAs). Used only to reconcile with upstream's 1.43.
    """
    where = "WHERE t.OA21CD IN (SELECT OA21CD FROM inmap_pwc)" if quirk else \
            "WHERE t.OA21CD IN (SELECT OA21CD FROM ext_oa)"
    con.execute(f"""CREATE OR REPLACE TABLE hbw_nofixed_lsoa AS
        SELECT e.LSOA21CD AS zone, sum(t.nofixed_offshore * (1 - {frac})) AS nofixed
        FROM up.ts058 t JOIN ext_oa e ON e.OA21CD=t.OA21CD {where} GROUP BY 1""")
    con.execute("""CREATE OR REPLACE TABLE hbw_bres_msoa AS
        SELECT e.MSOA21CD AS msoa, sum(x.jobs) AS jobs
        FROM up.bres x JOIN (SELECT DISTINCT LSOA21CD, MSOA21CD FROM ext_oa) e
          ON e.LSOA21CD=x.LSOA21CD GROUP BY 1""")


def _flows_view(con, where: str = "TRUE") -> None:
    # External_out destinations share MSOA codes with in-map MSOAs where an MSOA
    # straddles the boundary, so they are keyed apart to keep them out of the rescale.
    con.execute(f"""CREATE OR REPLACE TEMP VIEW hbw_for_correct AS
        SELECT o_zone, CASE WHEN external='external_out' THEN 'OUT:'||d_msoa
                            ELSE d_msoa END AS d_msoa, n
        FROM hbw_base WHERE {where}""")


def correct_variant(con, covid, prefix: str, d: float, cap) -> dict:
    return covid.correct(
        con, flows="hbw_for_correct", origin_col="o_zone", size_col="n",
        nofixed="hbw_nofixed_lsoa", bres_msoa="hbw_bres_msoa", cap=cap,
        out=covid.Outputs.prefixed(prefix), bres_discount=d, unmatched="keep",
        verbose=False)


def _upstream_equivalent_view(con, max_km: float) -> None:
    """Upstream's inputs to the covid stage, rebuilt at the lab's grain.

    FOR RECONCILIATION ONLY — this reproduces the game path's external clamp
    (external_out ends moved onto the nearest in-map MSOA, both directions cut at
    `max_km`) so the lab can be compared with upstream's figures. Nothing built from
    it feeds the demand table (rule 9).
    """
    con.execute(f"""CREATE OR REPLACE TEMP TABLE recon_near AS
        SELECT m.MSOA21CD AS ext, w.MSOA21CD AS tgt,
               111320*sqrt(pow((w.lon-m.lon)*cos(radians(m.lat)),2)
                         + pow(w.lat-m.lat,2)) AS dist_m
        FROM up.msoa_pwc m
        CROSS JOIN (SELECT p.*, e.MSOA21CD FROM inmap_pwc p JOIN ext_oa e USING (OA21CD)) w
        WHERE m.MSOA21CD IN (SELECT d_msoa FROM hbw_base WHERE external='external_out')
        QUALIFY row_number() OVER (PARTITION BY m.MSOA21CD ORDER BY dist_m, w.OA21CD) = 1""")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW hbw_for_correct AS
        SELECT o_zone, d_msoa, n FROM hbw_base
        WHERE external IS NULL OR (external='external_in' AND o_msoa IN
              (SELECT msoa FROM hbw_ext_dist WHERE km <= {max_km}))
        UNION ALL
        SELECT b.o_zone, r.tgt, b.n FROM hbw_base b JOIN recon_near r ON r.ext=b.d_msoa
        WHERE b.external='external_out' AND r.dist_m <= {max_km * 1000}""")


def reconcile_upstream(con, covid, frac: float, cap: float, max_km: float) -> list[dict]:
    """From upstream-equivalent inputs to the lab's, one difference at a time (d = 0).

    Step 0 must reproduce upstream's covid-stage inputs exactly: 356,286 fixed-workplace
    commuters and 432,003 after the no-fixed-place step (read from upstream's own
    `base_flows` and `pops_s1`, for comparison only). Its median factor then differs
    from upstream's 1.43 only by grain (origin uplift at LSOA not OA) and by the
    destination edge folding the lab does not do.
    """
    steps = [
        ("0 upstream-equivalent: externals clamped/cut at 30 km, TS058 quirk", True, None),
        ("1 external_out kept external (not clamped onto edge MSOAs)", True,
         f"external IS NULL OR (external='external_in' AND o_msoa IN "
         f"(SELECT msoa FROM hbw_ext_dist WHERE km <= {max_km}))"),
        ("2 + full TS058 (edge OAs' no-fixed-place workers counted)", False,
         f"external IS NULL OR (external='external_in' AND o_msoa IN "
         f"(SELECT msoa FROM hbw_ext_dist WHERE km <= {max_km}))"),
        ("3 + all externals, uncut (the lab's inputs)", False, "TRUE"),
    ]
    out = []
    for label, quirk, where in steps:
        _inputs_for_correct(con, frac, quirk=quirk)
        if where is None:
            _upstream_equivalent_view(con, max_km)
        else:
            _flows_view(con, where)
        s = correct_variant(con, covid, "hbw_recon_", 0.0, cap)
        row = {"step": label, **{k: float(s[k]) for k in
               ("fixed", "median_raw_factor", "p90_raw_factor", "max_raw_factor",
                "n_capped", "after_step1", "after_step2")}}
        # the same statistic over the destination MSOAs upstream itself has
        row["median_shared_msoas"], row["n_shared"] = con.execute("""
            SELECT median(x.raw_factor), count(*) FROM hbw_recon_dest_factor x
            WHERE x.msoa IN (SELECT msoa FROM up.dest_factor)""").fetchone()
        out.append(row)
    return out


def split_destinations(con, prefix: str, version: str) -> None:
    """MSOA → LSOA by BRES LSOA jobs; external_out stays at its MSOA."""
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW d_share AS
        WITH j AS (SELECT DISTINCT e.LSOA21CD, e.MSOA21CD, b.jobs
                   FROM (SELECT DISTINCT LSOA21CD, MSOA21CD FROM ext_oa) e
                   JOIN up.bres b ON b.LSOA21CD=e.LSOA21CD)
        SELECT LSOA21CD, MSOA21CD, jobs / sum(jobs) OVER (PARTITION BY MSOA21CD) AS share
        FROM j WHERE jobs > 0
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW hbw_{version}_od AS
        WITH m AS (SELECT * FROM {prefix}matrix),
        b AS (SELECT DISTINCT o_zone, o_level, o_msoa, d_msoa, d_msoa_level, external
              FROM hbw_base)
        SELECT m.o_zone, b.o_level, b.o_msoa,
               CASE WHEN b.external='external_out' THEN b.d_msoa ELSE s.LSOA21CD END AS d_zone,
               CASE WHEN b.external='external_out' THEN b.d_msoa_level ELSE 'LSOA' END AS d_level,
               b.d_msoa, b.external,
               m.n * COALESCE(s.share, 1.0) AS trips
        FROM m
        JOIN b ON b.o_zone=m.o_zone
              AND (CASE WHEN b.external='external_out' THEN 'OUT:'||b.d_msoa
                        ELSE b.d_msoa END) = m.d_msoa
        LEFT JOIN d_share s ON s.MSOA21CD=b.d_msoa AND b.external IS DISTINCT FROM 'external_out'
    """)
    lost = con.execute(f"""SELECT count(*) FROM hbw_{version}_od
                           WHERE d_zone IS NULL""").fetchone()[0]
    if lost:
        raise DemandError(f"{lost} rows have an in-map destination MSOA with no BRES LSOA")


def build_ca_shares(con, odwp14: Path) -> dict:
    """CA share per MSOA pair from ODWP14EW, falling back to the origin MSOA."""
    con.execute(f"""
        CREATE OR REPLACE TABLE odwp14 AS
        SELECT "Middle layer Super Output Areas code" AS o_msoa,
               "MSOA of workplace code" AS d_msoa,
               "Car or van availability (5 categories) code" AS cars,
               "Count" AS n
        FROM read_csv_auto('{odwp14}')
        WHERE "Place of work indicator (4 categories) code" = 3
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE hbw_ca_share AS
        WITH pairs AS (SELECT DISTINCT o_msoa, d_msoa FROM hbw_base),
        pr AS (SELECT o_msoa, d_msoa, sum(n) AS n,
                      sum(CASE WHEN cars > 0 THEN n ELSE 0 END) AS ca
               FROM odwp14 GROUP BY 1,2),
        org AS (SELECT o_msoa, sum(n) AS n,
                       sum(CASE WHEN cars > 0 THEN n ELSE 0 END) AS ca
                FROM odwp14 GROUP BY 1)
        SELECT p.o_msoa, p.d_msoa,
               CASE WHEN pr.n >= {CA_PAIR_MIN} THEN pr.ca / pr.n
                    ELSE org.ca / org.n END AS ca_share,
               CASE WHEN pr.n >= {CA_PAIR_MIN} THEN 'pair' ELSE 'origin' END AS basis
        FROM pairs p
        LEFT JOIN pr ON pr.o_msoa=p.o_msoa AND pr.d_msoa=p.d_msoa
        LEFT JOIN org ON org.o_msoa=p.o_msoa
    """)
    miss = con.execute("SELECT count(*) FROM hbw_ca_share WHERE ca_share IS NULL").fetchone()[0]
    if miss:
        raise DemandError(f"{miss} MSOA pairs have no ODWP14EW car-availability share")
    r = con.execute("""
        SELECT basis, count(*), sum(b.n) FROM hbw_ca_share s
        JOIN (SELECT o_msoa, d_msoa, sum(n) n FROM hbw_base GROUP BY 1,2) b
          USING (o_msoa, d_msoa) GROUP BY 1""").fetchall()
    return {basis: {"pairs": p, "commuters": c} for basis, p, c in r}


def write_demand(con, version: str) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS demand (
        demand_version VARCHAR, purpose VARCHAR, period VARCHAR, segment VARCHAR,
        o_zone VARCHAR, o_level VARCHAR, d_zone VARCHAR, d_level VARCHAR,
        trips DOUBLE, external VARCHAR, ext_dist_km DOUBLE)""")
    con.execute("DELETE FROM demand WHERE demand_version=? AND purpose=?", [version, PURPOSE])
    con.execute(f"""
        INSERT INTO demand
        SELECT '{version}', '{PURPOSE}', '{PERIOD}', seg.segment,
               o.o_zone, o.o_level, o.d_zone, o.d_level,
               o.trips * CASE seg.segment WHEN 'CA' THEN s.ca_share ELSE 1 - s.ca_share END,
               o.external, x.km
        FROM hbw_{version.replace('-', '_')}_od o
        JOIN hbw_ca_share s ON s.o_msoa=o.o_msoa AND s.d_msoa=o.d_msoa
        CROSS JOIN (VALUES ('CA'), ('NCA')) seg(segment)
        LEFT JOIN hbw_ext_dist x
          ON x.msoa = CASE o.external WHEN 'external_in' THEN o.o_msoa
                                      WHEN 'external_out' THEN o.d_msoa END
    """)


def run(cfg: LabConfig, con: duckdb.DuckDBPyConnection | None = None) -> Build:
    pkg = upstream.import_package(cfg)
    from importlib import import_module
    covid = import_module(f"{cfg.upstream_package}.covid")
    if not hasattr(covid, "correct"):
        raise DemandError("upstream has no covid.correct — check out a commit that has it")

    raw = cfg.root / "data" / "raw"
    odwp14 = raw / "census2021" / "ODWP14EW_MSOA.csv"
    if not odwp14.is_file():
        raise DemandError(f"missing {odwp14}; see sources.md P1 for the download")
    disc = bres_discount.derive(raw)
    discounts = {"low": disc.d_low, "central": disc.d_central, "high": disc.d_high}

    own = con is None
    con = con or duckdb.connect(str(cfg.lab_db))
    upstream.attach(con, cfg)
    _extent_views(con, cfg.extent)
    build_base(con)

    # offshore_fraction reads unqualified ts058 / resident_cats; temp views point it
    # at the attached upstream tables without writing anything there.
    con.execute("CREATE OR REPLACE TEMP VIEW ts058 AS SELECT * FROM up.ts058")
    con.execute("CREATE OR REPLACE TEMP VIEW resident_cats AS SELECT * FROM up.resident_cats")
    frac, _, _ = covid.offshore_fraction(con)

    city = pkg.CityConfig.from_yaml(cfg.upstream_city_config, root=cfg.upstream_repo)
    recon = reconcile_upstream(con, covid, frac, city.covid_cap, city.max_clamp_km)

    _inputs_for_correct(con, frac)
    _flows_view(con)
    caps, summaries = {}, {}
    for v, d in discounts.items():
        probe = correct_variant(con, covid, f"hbw_{v}_", d, None)
        # The cap stays only if it would bind; a game-era rail is not carried over.
        cap = city.covid_cap if probe["max_raw_factor"] > city.covid_cap else None
        s = probe if cap is None else correct_variant(con, covid, f"hbw_{v}_", d, cap)
        caps[v], summaries[v] = cap, {**s, "d": d}
        split_destinations(con, f"hbw_{v}_", f"p1_{v}")
    ca = build_ca_shares(con, odwp14)
    for v in VARIANTS:
        write_demand(con, f"p1-{v}")

    checks = reconcile_checks(con) + recon_checks(con, recon)
    con.execute("""CREATE OR REPLACE TABLE p1_reconciliation (step VARCHAR, fixed DOUBLE,
        after_step1 DOUBLE, after_step2 DOUBLE, median DOUBLE, median_shared_msoas DOUBLE,
        n_shared BIGINT, p90 DOUBLE, max DOUBLE, n_capped BIGINT)""")
    for r in recon:
        con.execute("INSERT INTO p1_reconciliation VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [r["step"], r["fixed"], r["after_step1"], r["after_step2"],
                     r["median_raw_factor"], r["median_shared_msoas"], r["n_shared"],
                     r["p90_raw_factor"], r["max_raw_factor"], r["n_capped"]])
    failed = [c for c in checks if not c[3]]
    if own:
        con.close()
    if failed:
        raise DemandError("P1 reconciliation failed: " + "; ".join(
            f"{name}: lab {a:,.3f} vs upstream {b:,.3f}" for name, a, b, _ in failed))
    return Build(discounts, caps, {**summaries, "reconciliation": recon, "ca_basis": ca},
                 checks)


# [MODELLED] how far the lab's median factor may sit from upstream's, over the MSOAs
# both have, once inputs are upstream-equivalent. The residual is grain (uplift at LSOA
# not OA) and destination edge folding; measured at 0.021 on 2026-09-27.
MEDIAN_TOLERANCE = 0.05


def recon_checks(con, recon: list[dict]) -> list[tuple[str, float, float, bool]]:
    """Step 0 against upstream's own covid-stage inputs and output (comparison only)."""
    s0 = recon[0]
    up_fixed, up_s1, up_med = con.execute("""
        SELECT (SELECT sum(n) FROM up.base_flows), (SELECT sum(size) FROM up.pops_s1),
               (SELECT median(raw_factor) FROM up.dest_factor)""").fetchone()
    rel = lambda a, b: abs(a - b) <= 1e-9 * abs(b)  # noqa: E731
    return [
        ("upstream-equivalent fixed-workplace commuters", s0["fixed"], up_fixed,
         rel(s0["fixed"], up_fixed)),
        ("upstream-equivalent total after no-fixed-place step", s0["after_step1"], up_s1,
         rel(s0["after_step1"], up_s1)),
        ("upstream-equivalent median factor, shared MSOAs", s0["median_shared_msoas"], up_med,
         abs(s0["median_shared_msoas"] - up_med) <= MEDIAN_TOLERANCE),
    ]


def reconcile_checks(con) -> list[tuple[str, float, float, bool]]:
    """Exact checks against upstream's raw tables, before any correction."""
    one = lambda sql: float(con.execute(sql).fetchone()[0] or 0)  # noqa: E731
    up_ii = one("""SELECT sum(n) FROM up.oa_flows f
                   WHERE f.o_oa IN (SELECT OA21CD FROM up.oa_extent)
                     AND f.d_oa IN (SELECT OA21CD FROM up.oa_extent)""")
    up_in = one("""SELECT sum(n) FROM up.oa_flows f
                   WHERE f.o_oa NOT IN (SELECT OA21CD FROM up.oa_extent)
                     AND f.d_oa IN (SELECT OA21CD FROM up.oa_extent)""")
    up_out = one("""SELECT sum(n) FROM up.oa_flows f
                    WHERE f.o_oa IN (SELECT OA21CD FROM up.oa_extent)
                      AND f.d_oa NOT IN (SELECT OA21CD FROM up.oa_extent)""")
    lab = dict(con.execute("SELECT COALESCE(external,'internal'), sum(n) FROM hbw_base "
                           "GROUP BY 1").fetchall())
    # per destination MSOA, internal → internal, exact
    worst = one("""
        WITH l AS (SELECT d_msoa, sum(n) n FROM hbw_base WHERE external IS NULL GROUP BY 1),
             u AS (SELECT l.MSOA21CD d_msoa, sum(f.n) n FROM up.oa_flows f
                   JOIN up.oa_extent l ON l.OA21CD=f.d_oa
                   WHERE f.o_oa IN (SELECT OA21CD FROM up.oa_extent) GROUP BY 1)
        SELECT max(abs(COALESCE(l.n,0)-COALESCE(u.n,0))) FROM l FULL JOIN u USING (d_msoa)""")
    return [
        ("internal → internal total", lab.get("internal", 0), up_ii, lab.get("internal") == up_ii),
        ("internal → internal, worst per-destination-MSOA difference", worst, 0.0, worst == 0),
        ("external_in total", lab.get("external_in", 0), up_in, lab.get("external_in") == up_in),
        ("external_out total", lab.get("external_out", 0), up_out, lab.get("external_out") == up_out),
    ]
