"""
P1 — analysis-grade commute (HBW) demand (SPEC §6.1).

The matrix is **people present at a fixed workplace on an average weekday**, not
people by where they mainly work. Built from national raw census files and upstream's
own correction code, never from upstream's game-adjusted tables:

1. **Zones.** An LSOA is internal only if its ONS population-weighted centroid is
   inside the extent — the same rule upstream uses for points. Every other zone is
   external, at MSOA level (or country, for Scotland and Northern Ireland).
2. **Base matrix** from the national ODWP01EW OA file: LSOA → MSOA for internal
   origins, MSOA → MSOA for external ones, tagged `external` = NULL / 'external_in' /
   'external_out', with `ext_dist_km` from the external MSOA's centroid to the nearest
   internal LSOA centroid. Nothing is cut, clamped or spread; external ↔ external
   flows are not lab trips and are dropped (and counted).
3. **Lockdown correction** by upstream `covid.correct` — the code the game path runs —
   at the low / central / high BRES discount, with no cap. A destination factor above
   `commute.destination_factor_check` fails the build instead of being clipped.
4. **External_out on the same basis.** Each external workplace MSOA gets its own
   national factor, BRES × (1 − d) ÷ census fixed-workplace inflow from all England and
   Wales origins, applied to the base flow.
5. **Destination split** MSOA → internal LSOAs by BRES LSOA jobs. Assumption: within an
   MSOA every origin gets the same destination pattern.
6. **CA / NCA split** by ODWP14EW, after the correction: the pair's own split, else
   origin MSOA × destination LAD, else origin MSOA. Assumption: redistributed
   no-fixed-place workers share their origin's car-availability split.

Upstream's classification (OA polygon intersection) is rebuilt alongside, only to
reconcile with upstream step by step.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .. import upstream
from ..config import LabConfig
from . import bres_discount

PURPOSE = "HBW"
PERIOD = "DAY"            # daily totals; split into periods in P4
VARIANTS = ("low", "central", "high")

def _param(cfg: LabConfig, name: str):
    """A commute parameter from params/base.yaml (tagged there; see params.py)."""
    from .. import params
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    return ps[f"commute.{name}"]

# [MODELLED] how far the lab's median factor may sit from upstream's, over the MSOAs
# both have, once inputs are upstream-equivalent. The residual is grain (uplift at LSOA
# not OA) and destination edge folding; measured at 0.021 on 2026-09-27.
MEDIAN_TOLERANCE = 0.05
COUNTRY = {"S": "S92000003", "N": "N92000002"}


class DemandError(RuntimeError):
    pass


@dataclass(frozen=True)
class Build:
    discounts: dict[str, float]
    summaries: dict[str, dict]
    checks: list[tuple[str, float, float, bool]]


# --- zones --------------------------------------------------------------------------

def _zones(con, cfg: LabConfig, lsoa_pwc: Path) -> None:
    min_lon, min_lat, max_lon, max_lat = cfg.extent
    rows = [(f["properties"]["LSOA21CD"], *f["geometry"]["coordinates"][:2])
            for f in json.loads(lsoa_pwc.read_text())["features"]]
    con.execute("CREATE OR REPLACE TABLE lsoa_pwc (LSOA21CD VARCHAR, lon DOUBLE, lat DOUBLE)")
    con.executemany("INSERT INTO lsoa_pwc VALUES (?,?,?)", rows)
    missing = con.execute("""SELECT count(DISTINCT LSOA21CD) FROM up.oa_extent
                             WHERE LSOA21CD NOT IN (SELECT LSOA21CD FROM lsoa_pwc)""").fetchone()[0]
    if missing:
        raise DemandError(f"{missing} extent LSOAs have no population-weighted centroid")
    con.execute(f"""CREATE OR REPLACE TABLE int_lsoa AS
        SELECT l.LSOA21CD, l.lon, l.lat, m.MSOA21CD
        FROM lsoa_pwc l JOIN (SELECT DISTINCT LSOA21CD, MSOA21CD FROM up.oa_lu_all) m
          USING (LSOA21CD)
        WHERE l.lon BETWEEN {min_lon} AND {max_lon} AND l.lat BETWEEN {min_lat} AND {max_lat}""")
    con.execute("""CREATE OR REPLACE TABLE int_oa AS
        SELECT OA21CD, LSOA21CD, MSOA21CD FROM up.oa_lu_all
        WHERE LSOA21CD IN (SELECT LSOA21CD FROM int_lsoa)""")
    # upstream's classification, for reconciliation only
    con.execute("CREATE OR REPLACE TEMP VIEW up_oa AS SELECT OA21CD FROM up.oa_extent")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW up_inmap_pwc AS
        SELECT p.OA21CD, p.lon, p.lat FROM up.pwc p
        WHERE p.OA21CD IN (SELECT OA21CD FROM up_oa)
          AND p.lon BETWEEN {min_lon} AND {max_lon} AND p.lat BETWEEN {min_lat} AND {max_lat}""")


def _national_flows(con, raw: Path) -> None:
    """Fixed-workplace OA flows touching either classification, from the national file."""
    con.execute(f"""CREATE OR REPLACE TABLE nat_oa_flows AS
        SELECT "Output Areas code" AS o_oa, "OA of workplace code" AS d_oa, "Count" AS n
        FROM read_csv_auto('{raw / "odwp01ew" / "ODWP01EW_OA.csv"}')
        WHERE "Place of work indicator (4 categories) code" = 3
          AND ("Output Areas code" IN (SELECT OA21CD FROM int_oa UNION SELECT OA21CD FROM up_oa)
            OR "OA of workplace code" IN (SELECT OA21CD FROM int_oa UNION SELECT OA21CD FROM up_oa))""")


def _base(con, out: str, inside: str, origin_zone: str) -> None:
    """Base matrix from `nat_oa_flows` under a classification.

    inside       SQL set of internal OA codes
    origin_zone  'LSOA' (lab) — internal origins at LSOA; the same for upstream steps
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE {out} AS
        WITH f AS (
            SELECT f.n, f.o_oa IN ({inside}) AS o_in, f.d_oa IN ({inside}) AS d_in,
                   lo.LSOA21CD AS o_lsoa, lo.MSOA21CD AS o_msoa,
                   COALESCE(ld.MSOA21CD,
                            CASE WHEN f.d_oa LIKE 'S%' THEN '{COUNTRY["S"]}'
                                 WHEN f.d_oa LIKE 'N%' THEN '{COUNTRY["N"]}' END) AS d_msoa
            FROM nat_oa_flows f
            LEFT JOIN up.oa_lu_all lo ON lo.OA21CD=f.o_oa
            LEFT JOIN up.oa_lu_all ld ON ld.OA21CD=f.d_oa)
        SELECT CASE WHEN o_in THEN o_lsoa ELSE o_msoa END AS o_zone,
               CASE WHEN o_in THEN 'LSOA' ELSE 'MSOA' END AS o_level,
               o_msoa, d_msoa,
               CASE WHEN d_msoa IN ('{COUNTRY["S"]}','{COUNTRY["N"]}') THEN 'COUNTRY'
                    ELSE 'MSOA' END AS d_msoa_level,
               CASE WHEN o_in AND d_in THEN NULL
                    WHEN d_in THEN 'external_in'
                    WHEN o_in THEN 'external_out' ELSE 'external_both' END AS external,
               sum(n)::DOUBLE AS n
        FROM f GROUP BY ALL
    """)
    bad = con.execute(f"""SELECT count(*), COALESCE(sum(n),0) FROM {out}
                          WHERE o_zone IS NULL OR o_msoa IS NULL OR d_msoa IS NULL""").fetchone()
    if bad[0]:
        raise DemandError(f"{out}: {bad[0]} rows ({bad[1]:,.0f} commuters) have an "
                          "unmapped origin or destination")


def _ext_dist(con, base: str, targets: str, out: str) -> None:
    """External MSOA centroid → nearest target centroid (planar, as upstream's clamp)."""
    con.execute(f"""
        CREATE OR REPLACE TABLE {out} AS
        WITH ext AS (
            SELECT DISTINCT CASE WHEN external='external_in' THEN o_msoa ELSE d_msoa END AS msoa
            FROM {base} WHERE external IN ('external_in','external_out'))
        SELECT e.msoa,
               min(111320*sqrt(pow((w.lon-m.lon)*cos(radians(m.lat)),2)
                             + pow(w.lat-m.lat,2))) / 1000 AS km
        FROM ext e JOIN up.msoa_pwc m ON m.MSOA21CD=e.msoa CROSS JOIN ({targets}) w
        GROUP BY 1""")
    missing = con.execute(f"""
        SELECT count(*) FROM {base} WHERE external IN ('external_in','external_out')
          AND d_msoa_level = 'MSOA'
          AND CASE WHEN external='external_in' THEN o_msoa ELSE d_msoa END
              NOT IN (SELECT msoa FROM {out})""").fetchone()[0]
    if missing:
        raise DemandError(f"{missing} external rows have no MSOA centroid to measure from")


# --- correction ---------------------------------------------------------------------

def _nofixed(con, frac: float, oas: str) -> None:
    con.execute(f"""CREATE OR REPLACE TABLE hbw_nofixed_lsoa AS
        SELECT l.LSOA21CD AS zone, sum(t.nofixed_offshore * (1 - {frac})) AS nofixed
        FROM nat_ts058 t JOIN up.oa_lu_all l ON l.OA21CD=t.OA21CD
        WHERE t.OA21CD IN ({oas}) GROUP BY 1""")


def _bres_msoa(con, lsoas: str) -> None:
    """BRES by destination MSOA, over the given (internal) LSOAs only."""
    con.execute(f"""CREATE OR REPLACE TABLE hbw_bres_msoa AS
        SELECT m.MSOA21CD AS msoa, sum(b.jobs) AS jobs
        FROM nat_bres b JOIN (SELECT DISTINCT LSOA21CD, MSOA21CD FROM up.oa_lu_all) m
          USING (LSOA21CD)
        WHERE b.LSOA21CD IN ({lsoas}) GROUP BY 1""")


def _flows_view(con, base: str, where: str = "TRUE") -> None:
    # External_out destinations can share an MSOA code with an internal destination
    # where the MSOA straddles the boundary, so they are keyed apart; they have no BRES
    # row here and pass through the rescale unchanged (their factor comes in step 4).
    con.execute(f"""CREATE OR REPLACE TEMP VIEW hbw_for_correct AS
        SELECT o_zone, CASE WHEN external='external_out' THEN 'OUT:'||d_msoa
                            ELSE d_msoa END AS d_msoa, n
        FROM {base} WHERE external IS DISTINCT FROM 'external_both' AND ({where})""")


def correct_variant(con, covid, prefix: str, d: float, cap) -> dict:
    return covid.correct(
        con, flows="hbw_for_correct", origin_col="o_zone", size_col="n",
        nofixed="hbw_nofixed_lsoa", bres_msoa="hbw_bres_msoa", cap=cap,
        out=covid.Outputs.prefixed(prefix), bres_discount=d, unmatched="keep",
        verbose=False)


def _ext_out_factors(con, raw: Path) -> None:
    """National BRES and census inflow for every external workplace MSOA."""
    con.execute(f"""CREATE OR REPLACE TABLE ext_out_msoa AS
        WITH dest AS (SELECT DISTINCT d_msoa AS msoa FROM hbw_base
                      WHERE external='external_out' AND d_msoa_level='MSOA'),
        inflow AS (
            SELECT "MSOA of workplace code" AS msoa, sum("Count") AS inflow
            FROM read_csv_auto('{raw / "odwp01ew" / "ODWP01EW_MSOA.csv"}')
            WHERE "Place of work indicator (4 categories) code" = 3
              AND "MSOA of workplace code" IN (SELECT msoa FROM dest)
            GROUP BY 1),
        bres AS (
            SELECT m.MSOA21CD AS msoa, sum(b.jobs) AS jobs
            FROM nat_bres b JOIN (SELECT DISTINCT LSOA21CD, MSOA21CD FROM up.oa_lu_all) m
              USING (LSOA21CD)
            WHERE m.MSOA21CD IN (SELECT msoa FROM dest) GROUP BY 1)
        SELECT d.msoa, i.inflow, b.jobs FROM dest d
        LEFT JOIN inflow i USING (msoa) LEFT JOIN bres b USING (msoa)""")


def _ext_out_for_variant(con, v: str, d: float) -> dict:
    """Factor per external workplace; the in-extent overall factor where none exists."""
    fallback = con.execute(f"""SELECT sum(bres) / sum(modelled)
                               FROM hbw_{v}_dest_factor""").fetchone()[0]
    con.execute(f"""CREATE OR REPLACE TABLE hbw_{v}_ext_out_factor AS
        SELECT b.d_msoa AS msoa,
               CASE WHEN e.inflow > 0 AND e.jobs IS NOT NULL
                    THEN e.jobs * (1 - {d}) / e.inflow ELSE {fallback} END AS factor,
               CASE WHEN e.inflow > 0 AND e.jobs IS NOT NULL
                    THEN 'national' ELSE 'in-extent fallback [MODELLED]' END AS basis
        FROM (SELECT DISTINCT d_msoa FROM hbw_base WHERE external='external_out') b
        LEFT JOIN ext_out_msoa e ON e.msoa=b.d_msoa""")
    r = con.execute(f"""
        SELECT median(factor) FILTER (WHERE basis='national'),
               quantile_cont(factor, 0.9) FILTER (WHERE basis='national'),
               max(factor) FILTER (WHERE basis='national'),
               sum(n) FILTER (WHERE basis<>'national')
        FROM hbw_{v}_ext_out_factor f
        JOIN (SELECT d_msoa, sum(n) n FROM hbw_base WHERE external='external_out'
              GROUP BY 1) b ON b.d_msoa=f.msoa""").fetchone()
    return {"median": r[0], "p90": r[1], "max": r[2], "fallback_factor": fallback,
            "fallback_commuters": r[3] or 0.0}


# --- destinations and segments --------------------------------------------------------

def split_destinations(con, v: str) -> None:
    """Internal: MSOA → internal LSOA by BRES jobs. External_out: base × national factor."""
    con.execute("""
        CREATE OR REPLACE TEMP VIEW d_share AS
        WITH j AS (SELECT i.LSOA21CD, i.MSOA21CD, b.jobs
                   FROM int_lsoa i JOIN nat_bres b USING (LSOA21CD))
        SELECT LSOA21CD, MSOA21CD, jobs / sum(jobs) OVER (PARTITION BY MSOA21CD) AS share
        FROM j WHERE jobs > 0
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE hbw_{v}_od AS
        WITH b AS (SELECT * FROM hbw_base WHERE external IS DISTINCT FROM 'external_out'
                                              AND external IS DISTINCT FROM 'external_both'),
        m AS (SELECT * FROM hbw_{v}_matrix WHERE d_msoa NOT LIKE 'OUT:%')
        SELECT m.o_zone, b.o_level, b.o_msoa, s.LSOA21CD AS d_zone, 'LSOA' AS d_level,
               b.d_msoa, b.external, m.n * s.share AS trips
        FROM m JOIN b ON b.o_zone=m.o_zone AND b.d_msoa=m.d_msoa
        JOIN d_share s ON s.MSOA21CD=b.d_msoa
        UNION ALL
        SELECT b.o_zone, b.o_level, b.o_msoa, b.d_msoa, b.d_msoa_level, b.d_msoa,
               b.external, b.n * f.factor
        FROM hbw_base b JOIN hbw_{v}_ext_out_factor f ON f.msoa=b.d_msoa
        WHERE b.external='external_out'
    """)
    lost = con.execute(f"""
        SELECT (SELECT sum(n) FROM hbw_{v}_matrix WHERE d_msoa NOT LIKE 'OUT:%')
             - (SELECT sum(trips) FROM hbw_{v}_od WHERE external IS DISTINCT FROM 'external_out')
    """).fetchone()[0]
    if abs(lost) > 1e-6:
        raise DemandError(f"destination split lost {lost:,.3f} trips — an internal "
                          "destination MSOA has no BRES LSOA")


def build_ca_shares(con, odwp14: Path, ca_min: int) -> dict:
    """CA share: pair ≥ ca_min, else origin MSOA × destination LAD ≥ ca_min, else origin."""
    con.execute(f"""
        CREATE OR REPLACE TABLE odwp14 AS
        SELECT "Middle layer Super Output Areas code" AS o_msoa,
               "MSOA of workplace code" AS d_msoa,
               "Car or van availability (5 categories) code" AS cars,
               "Count" AS n
        FROM read_csv_auto('{odwp14}')
        WHERE "Place of work indicator (4 categories) code" = 3
    """)
    con.execute("""CREATE OR REPLACE TEMP VIEW msoa_lad AS
                   SELECT DISTINCT MSOA21CD AS msoa, LAD22CD AS lad FROM up.oa_lu_all""")
    con.execute(f"""
        CREATE OR REPLACE TABLE hbw_ca_share AS
        WITH pairs AS (SELECT DISTINCT o_msoa, d_msoa FROM hbw_base
                       WHERE external IS DISTINCT FROM 'external_both'),
        o14 AS (SELECT o.*, l.lad AS d_lad FROM odwp14 o LEFT JOIN msoa_lad l ON l.msoa=o.d_msoa),
        pr AS (SELECT o_msoa, d_msoa, sum(n) n, sum(n) FILTER (WHERE cars > 0) ca
               FROM o14 GROUP BY 1,2),
        pl AS (SELECT o_msoa, d_lad, sum(n) n, sum(n) FILTER (WHERE cars > 0) ca
               FROM o14 WHERE d_lad IS NOT NULL GROUP BY 1,2),
        org AS (SELECT o_msoa, sum(n) n, sum(n) FILTER (WHERE cars > 0) ca
                FROM o14 GROUP BY 1)
        SELECT p.o_msoa, p.d_msoa,
               CASE WHEN pr.n >= {ca_min} THEN pr.ca / pr.n
                    WHEN pl.n >= {ca_min} THEN pl.ca / pl.n
                    ELSE org.ca / org.n END AS ca_share,
               CASE WHEN pr.n >= {ca_min} THEN 'pair'
                    WHEN pl.n >= {ca_min} THEN 'origin MSOA x destination LAD'
                    ELSE 'origin MSOA' END AS basis
        FROM pairs p
        LEFT JOIN pr ON pr.o_msoa=p.o_msoa AND pr.d_msoa=p.d_msoa
        LEFT JOIN msoa_lad dl ON dl.msoa=p.d_msoa
        LEFT JOIN pl ON pl.o_msoa=p.o_msoa AND pl.d_lad=dl.lad
        LEFT JOIN org ON org.o_msoa=p.o_msoa
    """)
    miss = con.execute("SELECT count(*) FROM hbw_ca_share WHERE ca_share IS NULL").fetchone()[0]
    if miss:
        raise DemandError(f"{miss} MSOA pairs have no ODWP14EW car-availability share")
    r = con.execute("""
        SELECT basis, count(*), sum(b.n) FROM hbw_ca_share s
        JOIN (SELECT o_msoa, d_msoa, sum(n) n FROM hbw_base
              WHERE external IS DISTINCT FROM 'external_both' GROUP BY 1,2) b
          USING (o_msoa, d_msoa) GROUP BY 1 ORDER BY 3 DESC""").fetchall()
    return {basis: {"pairs": p, "commuters": c} for basis, p, c in r}


def write_demand(con, v: str) -> None:
    version = f"p1-{v}"
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
        FROM hbw_{v}_od o
        JOIN hbw_ca_share s ON s.o_msoa=o.o_msoa AND s.d_msoa=o.d_msoa
        CROSS JOIN (VALUES ('CA'), ('NCA')) seg(segment)
        LEFT JOIN hbw_ext_dist x
          ON x.msoa = CASE o.external WHEN 'external_in' THEN o.o_msoa
                                      WHEN 'external_out' THEN o.d_msoa END
    """)


# --- reconciliation -----------------------------------------------------------------

def _upstream_equivalent_view(con, max_km: float) -> None:
    """Upstream's inputs to the covid stage, rebuilt at the lab's grain.

    FOR RECONCILIATION ONLY — this reproduces the game path's external clamp
    (external_out ends moved onto the nearest in-map MSOA, both directions cut at
    `max_km`) so the lab can be compared with upstream's figures. Nothing built from
    it feeds the demand table (rule 9).
    """
    con.execute("""CREATE OR REPLACE TEMP TABLE recon_near AS
        SELECT m.MSOA21CD AS ext, w.MSOA21CD AS tgt,
               111320*sqrt(pow((w.lon-m.lon)*cos(radians(m.lat)),2)
                         + pow(w.lat-m.lat,2)) AS dist_m
        FROM up.msoa_pwc m
        CROSS JOIN (SELECT p.*, e.MSOA21CD FROM up_inmap_pwc p
                    JOIN up.oa_extent e USING (OA21CD)) w
        WHERE m.MSOA21CD IN (SELECT d_msoa FROM hbw_base_up WHERE external='external_out')
        QUALIFY row_number() OVER (PARTITION BY m.MSOA21CD ORDER BY dist_m, w.OA21CD) = 1""")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW hbw_for_correct AS
        SELECT o_zone, d_msoa, n FROM hbw_base_up
        WHERE external IS NULL OR (external='external_in' AND o_msoa IN
              (SELECT msoa FROM hbw_ext_dist_up WHERE km <= {max_km}))
        UNION ALL
        SELECT b.o_zone, r.tgt, b.n FROM hbw_base_up b JOIN recon_near r ON r.ext=b.d_msoa
        WHERE b.external='external_out' AND r.dist_m <= {max_km * 1000}""")


def reconcile_upstream(con, covid, frac: float, cap: float, max_km: float) -> list[dict]:
    """From upstream-equivalent inputs to the lab's, one difference at a time (d = 0).

    Steps 0–3 use upstream's classification (OA polygon intersection); step 4 switches
    to the lab's (LSOA population-weighted centroid). Step 0 must reproduce upstream's
    covid-stage inputs exactly (checked in `recon_checks`).
    """
    near30 = (f"external IS NULL OR (external='external_in' AND o_msoa IN "
              f"(SELECT msoa FROM hbw_ext_dist_up WHERE km <= {max_km}))")
    up_oas = "SELECT OA21CD FROM up_oa"
    up_oas_quirk = "SELECT OA21CD FROM up_inmap_pwc"
    steps = [
        ("0 upstream-equivalent: externals clamped/cut at 30 km, TS058 quirk",
         "hbw_base_up", up_oas_quirk, "SELECT DISTINCT LSOA21CD FROM up.oa_extent", None),
        ("1 external_out kept external (not clamped onto edge MSOAs)",
         "hbw_base_up", up_oas_quirk, "SELECT DISTINCT LSOA21CD FROM up.oa_extent", near30),
        ("2 + full TS058 (edge OAs' no-fixed-place workers counted)",
         "hbw_base_up", up_oas, "SELECT DISTINCT LSOA21CD FROM up.oa_extent", near30),
        ("3 + all externals, uncut",
         "hbw_base_up", up_oas, "SELECT DISTINCT LSOA21CD FROM up.oa_extent", "TRUE"),
        ("4 + zones by LSOA population-weighted centroid (the lab's inputs)",
         "hbw_base", "SELECT OA21CD FROM int_oa", "SELECT LSOA21CD FROM int_lsoa", "TRUE"),
    ]
    out = []
    for label, base, oas, lsoas, where in steps:
        _nofixed(con, frac, oas)
        _bres_msoa(con, lsoas)
        if where is None:
            _upstream_equivalent_view(con, max_km)
        else:
            _flows_view(con, base, where)
        s = correct_variant(con, covid, "hbw_recon_", 0.0, cap if base == "hbw_base_up" else None)
        row = {"step": label, **{k: float(s[k]) for k in
               ("fixed", "median_raw_factor", "p90_raw_factor", "max_raw_factor",
                "after_step1", "after_step2")}}
        row["n_dest"] = int(s["n_dest"])
        row["median_shared_msoas"], row["n_shared"] = con.execute("""
            SELECT median(x.raw_factor), count(*) FROM hbw_recon_dest_factor x
            WHERE x.msoa IN (SELECT msoa FROM up.dest_factor)""").fetchone()
        out.append(row)
    return out


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
    """The national file against upstream's raw flows, and the reclassification."""
    one = lambda sql: float(con.execute(sql).fetchone()[0] or 0)  # noqa: E731
    up_raw = dict(con.execute("""
        SELECT CASE WHEN o_oa IN (SELECT OA21CD FROM up_oa) AND d_oa IN (SELECT OA21CD FROM up_oa)
                    THEN 'internal' WHEN d_oa IN (SELECT OA21CD FROM up_oa) THEN 'external_in'
                    ELSE 'external_out' END, sum(n)
        FROM up.oa_flows GROUP BY 1""").fetchall())
    lab_up = dict(con.execute("SELECT COALESCE(external,'internal'), sum(n) FROM hbw_base_up "
                              "GROUP BY 1").fetchall())
    worst = one("""
        WITH l AS (SELECT d_msoa, sum(n) n FROM hbw_base_up WHERE external IS NULL GROUP BY 1),
             u AS (SELECT e.MSOA21CD d_msoa, sum(f.n) n FROM up.oa_flows f
                   JOIN up.oa_extent e ON e.OA21CD=f.d_oa
                   WHERE f.o_oa IN (SELECT OA21CD FROM up_oa) GROUP BY 1)
        SELECT max(abs(COALESCE(l.n,0)-COALESCE(u.n,0))) FROM l FULL JOIN u USING (d_msoa)""")
    checks = [
        ("national file, upstream classification: internal → internal",
         lab_up.get("internal", 0), up_raw["internal"], lab_up.get("internal") == up_raw["internal"]),
        ("national file, upstream classification: worst per-destination-MSOA difference",
         worst, 0.0, worst == 0),
        ("national file, upstream classification: external_in",
         lab_up.get("external_in", 0), up_raw["external_in"],
         lab_up.get("external_in") == up_raw["external_in"]),
        ("national file, upstream classification: external_out",
         lab_up.get("external_out", 0), up_raw["external_out"],
         lab_up.get("external_out") == up_raw["external_out"]),
    ]
    # reclassification: every commuter accounted for between the two classifications
    t = con.execute("""
        SELECT COALESCE(u.k,'none') AS was, COALESCE(l.k,'none') AS now, sum(f.n)
        FROM nat_oa_flows f
        LEFT JOIN (SELECT o_oa, d_oa,
                   CASE WHEN o_oa IN (SELECT OA21CD FROM up_oa) AND d_oa IN (SELECT OA21CD FROM up_oa)
                        THEN 'internal' WHEN d_oa IN (SELECT OA21CD FROM up_oa) THEN 'external_in'
                        WHEN o_oa IN (SELECT OA21CD FROM up_oa) THEN 'external_out' END AS k
                   FROM nat_oa_flows) u USING (o_oa, d_oa)
        LEFT JOIN (SELECT o_oa, d_oa,
                   CASE WHEN o_oa IN (SELECT OA21CD FROM int_oa) AND d_oa IN (SELECT OA21CD FROM int_oa)
                        THEN 'internal' WHEN d_oa IN (SELECT OA21CD FROM int_oa) THEN 'external_in'
                        WHEN o_oa IN (SELECT OA21CD FROM int_oa) THEN 'external_out' END AS k
                   FROM nat_oa_flows) l USING (o_oa, d_oa)
        GROUP BY 1,2 ORDER BY 1,2""").fetchall()
    con.execute("CREATE OR REPLACE TABLE p1_reclass_flows (was VARCHAR, now VARCHAR, commuters DOUBLE)")
    con.executemany("INSERT INTO p1_reclass_flows VALUES (?,?,?)", t)
    lab = dict(con.execute("SELECT COALESCE(external,'internal'), sum(n) FROM hbw_base "
                           "GROUP BY 1").fetchall())
    now_int = sum(c for w, n, c in t if n == "internal")
    checks.append(("reclassification accounts for every internal → internal commuter",
                   lab.get("internal", 0), float(now_int), lab.get("internal") == now_int))
    return checks


def _reclass_zones(con) -> None:
    """The LSOAs the centroid rule moves out of the map, and the commuters they carry."""
    con.execute("""
        CREATE OR REPLACE TABLE p1_reclass_zones AS
        WITH moved AS (SELECT DISTINCT e.LSOA21CD, e.MSOA21CD FROM up.oa_extent e
                       WHERE e.LSOA21CD NOT IN (SELECT LSOA21CD FROM int_lsoa)),
        f AS (SELECT f.n, lo.LSOA21CD ol, ld.LSOA21CD dl FROM nat_oa_flows f
              LEFT JOIN up.oa_lu_all lo ON lo.OA21CD=f.o_oa
              LEFT JOIN up.oa_lu_all ld ON ld.OA21CD=f.d_oa
              WHERE f.o_oa IN (SELECT OA21CD FROM up_oa) AND f.d_oa IN (SELECT OA21CD FROM up_oa))
        SELECT m.LSOA21CD, m.MSOA21CD, n.MSOA21NM, p.lon, p.lat,
               COALESCE((SELECT sum(n) FROM f WHERE f.ol=m.LSOA21CD), 0) AS as_origin,
               COALESCE((SELECT sum(n) FROM f WHERE f.dl=m.LSOA21CD), 0) AS as_destination
        FROM moved m JOIN lsoa_pwc p USING (LSOA21CD)
        LEFT JOIN (SELECT DISTINCT MSOA21CD, MSOA21NM FROM up.oa_lu_all) n USING (MSOA21CD)
        ORDER BY n.MSOA21NM, m.LSOA21CD""")


# --- run ----------------------------------------------------------------------------

def run(cfg: LabConfig, con: duckdb.DuckDBPyConnection | None = None) -> Build:
    pkg = upstream.import_package(cfg)
    from importlib import import_module
    covid = import_module(f"{cfg.upstream_package}.covid")
    if not hasattr(covid, "correct"):
        raise DemandError("upstream has no covid.correct — check out a commit that has it")

    raw = cfg.root / "data" / "raw"
    up_raw = cfg.upstream_raw
    need = [raw / "census2021" / "ODWP14EW_MSOA.csv", raw / "ons_geo" / "lsoa21_pwc_extent.geojson",
            up_raw / "odwp01ew" / "ODWP01EW_OA.csv", up_raw / "odwp01ew" / "ODWP01EW_MSOA.csv",
            up_raw / "ts058" / "census2021-ts058-oa.csv", up_raw / "bres_lsoa.csv"]
    for p in need:
        if not p.is_file():
            raise DemandError(f"missing input {p}; see sources.md P1")
    disc = bres_discount.derive(raw)
    discounts = {"low": disc.d_low, "central": disc.d_central, "high": disc.d_high}

    own = con is None
    con = con or duckdb.connect(str(cfg.lab_db))
    upstream.attach(con, cfg)
    _zones(con, cfg, raw / "ons_geo" / "lsoa21_pwc_extent.geojson")
    _national_flows(con, up_raw)
    con.execute(f"""CREATE OR REPLACE TABLE nat_ts058 AS
        SELECT "geography code" AS OA21CD,
               "Distance travelled to work: Works mainly at an offshore installation, in no fixed place, or outside the UK" AS nofixed_offshore
        FROM read_csv_auto('{up_raw / "ts058" / "census2021-ts058-oa.csv"}')""")
    con.execute(f"""CREATE OR REPLACE TABLE nat_bres AS
        SELECT GEOGRAPHY_CODE AS LSOA21CD, CAST(OBS_VALUE AS BIGINT) AS jobs
        FROM read_csv_auto('{up_raw / "bres_lsoa.csv"}')""")

    _base(con, "hbw_base_up", "SELECT OA21CD FROM up_oa", "LSOA")
    _ext_dist(con, "hbw_base_up", "SELECT lon, lat FROM up_inmap_pwc", "hbw_ext_dist_up")
    _base(con, "hbw_base", "SELECT OA21CD FROM int_oa", "LSOA")
    _ext_dist(con, "hbw_base", "SELECT lon, lat FROM int_lsoa", "hbw_ext_dist")
    _reclass_zones(con)

    # offshore_fraction reads unqualified ts058 / resident_cats; temp views point it
    # at the attached upstream tables without writing anything there.
    con.execute("CREATE OR REPLACE TEMP VIEW ts058 AS SELECT * FROM up.ts058")
    con.execute("CREATE OR REPLACE TEMP VIEW resident_cats AS SELECT * FROM up.resident_cats")
    frac, _, _ = covid.offshore_fraction(con)

    city = pkg.CityConfig.from_yaml(cfg.upstream_city_config, root=cfg.upstream_repo)
    recon = reconcile_upstream(con, covid, frac, city.covid_cap, city.max_clamp_km)

    _nofixed(con, frac, "SELECT OA21CD FROM int_oa")
    _bres_msoa(con, "SELECT LSOA21CD FROM int_lsoa")
    _flows_view(con, "hbw_base")
    _ext_out_factors(con, up_raw)
    factor_check = float(_param(cfg, "destination_factor_check"))
    summaries = {}
    for v, d in discounts.items():
        s = correct_variant(con, covid, f"hbw_{v}_", d, None)
        over = con.execute(f"""SELECT msoa, raw_factor FROM hbw_{v}_dest_factor
                               WHERE raw_factor > {factor_check} ORDER BY 2 DESC""").fetchall()
        if over:
            raise DemandError(
                f"d={d:.3f} ({v}): destination factor above {factor_check} in "
                + ", ".join(f"{m} x{f:.1f}" for m, f in over)
                + " — not clipped; look at these zones")
        eo = _ext_out_for_variant(con, v, d)
        summaries[v] = {**s, "d": d, "ext_out": eo}
        split_destinations(con, v)
    ca = build_ca_shares(con, raw / "census2021" / "ODWP14EW_MSOA.csv",
                         int(_param(cfg, "ca_min_commuters")))
    for v in VARIANTS:
        write_demand(con, v)

    checks = reconcile_checks(con) + recon_checks(con, recon)
    con.execute("""CREATE OR REPLACE TABLE p1_reconciliation (step VARCHAR, fixed DOUBLE,
        after_step1 DOUBLE, after_step2 DOUBLE, median DOUBLE, median_shared_msoas DOUBLE,
        n_shared BIGINT, n_dest BIGINT, p90 DOUBLE, max DOUBLE)""")
    for r in recon:
        con.execute("INSERT INTO p1_reconciliation VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [r["step"], r["fixed"], r["after_step1"], r["after_step2"],
                     r["median_raw_factor"], r["median_shared_msoas"], r["n_shared"],
                     r["n_dest"], r["p90_raw_factor"], r["max_raw_factor"]])
    failed = [c for c in checks if not c[3]]
    if own:
        con.close()
    if failed:
        raise DemandError("P1 reconciliation failed: " + "; ".join(
            f"{name}: lab {a:,.3f} vs upstream {b:,.3f}" for name, a, b, _ in failed))
    return Build(discounts, {**summaries, "reconciliation": recon, "ca_basis": ca}, checks)
