"""
Which of an area's improvements come from the scheme (`lab map`).

For every output area (OA), over the destination zones it can reach in the AM skim: the jobs
in zones that became at least `min_gain` minutes faster (or newly reachable), how many of
those jobs sit in zones whose best itinerary now uses a scheme route that the old best
itinerary did not (a route in both is not credited: the saving came from elsewhere), and the jobs
that newly came inside 45 minutes by a real saving. A tip over 45 minutes by less than
`min_gain` is not counted: it is a threshold artefact, not an improvement.

Skims are the offset means (`AM.parquet`). Reads them read-only.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


def compute(lab_db: Path, skims_scenario: Path, skims_against: Path, route_ids: list[str],
            min_gain: float, period: str = "AM", limit_min: float = 45.0) -> pd.DataFrame:
    a, b = skims_against / f"{period}.parquet", skims_scenario / f"{period}.parquet"
    ids = ", ".join("'" + r.replace("'", "''") + "'" for r in route_ids)
    with duckdb.connect(str(lab_db), read_only=True) as con:
        return con.execute(f"""
            WITH j AS (SELECT d.LSOA21CD id, coalesce(b.jobs, 0) jobs
                       FROM skim_dest d LEFT JOIN nat_bres b USING (LSOA21CD)),
            p AS (SELECT a.from_id oa, a.to_id, a.unreachable ua, b.unreachable ub,
                         a.p50 t0, b.p50 t1,
                         regexp_split_to_array(coalesce(a.top_routes, ''), '[;|]') ra,
                         regexp_split_to_array(coalesce(b.top_routes, ''), '[;|]') rb
                  FROM read_parquet('{a}') a JOIN read_parquet('{b}') b USING (from_id, to_id)),
            s AS (SELECT p.*, j.jobs,
                         (ua AND NOT ub) OR (NOT ua AND NOT ub AND t0 - t1 >= {min_gain}) AS sel
                  FROM p JOIN j ON j.id = p.to_id)
            SELECT oa AS OA21CD,
                   coalesce(sum(jobs) FILTER (WHERE sel), 0) AS jobs_faster,
                   coalesce(sum(jobs) FILTER (WHERE sel AND len(list_filter([{ids}],
                       x -> list_contains(rb, x) AND NOT list_contains(ra, x))) > 0), 0) AS jobs_faster_via_scheme,
                   coalesce(sum(jobs) FILTER (WHERE sel AND NOT ub AND t1 <= {limit_min}
                                              AND (ua OR t0 > {limit_min})), 0) AS jobs_new_within
            FROM s GROUP BY 1""").df()


def zone_jobs(lab_db: Path) -> pd.DataFrame:
    with duckdb.connect(str(lab_db), read_only=True) as con:
        return con.execute("""SELECT d.LSOA21CD AS zone, coalesce(b.jobs, 0) AS jobs
                              FROM skim_dest d LEFT JOIN nat_bres b USING (LSOA21CD)""").df()


def reach(lab_db: Path, skims_scenario: Path, skims_against: Path, oas: list[str], min_gain: float,
          period: str = "AM", limit_min: float = 45.0) -> pd.DataFrame:
    """Per chosen OA, the destination zones that matter for a before-and-after reach picture:
    inside `limit_min` before or after, or at least `min_gain` minutes faster. `t0` / `t1` are the
    PT median minutes before (against) and after (scenario); NULL where unreachable."""
    a, b = skims_against / f"{period}.parquet", skims_scenario / f"{period}.parquet"
    ids = ", ".join("'" + o + "'" for o in oas)
    with duckdb.connect(str(lab_db), read_only=True) as con:
        return con.execute(f"""
            SELECT a.from_id AS oa, a.to_id AS zone,
                   CASE WHEN a.unreachable THEN NULL ELSE round(a.p50, 1) END AS t0,
                   CASE WHEN b.unreachable THEN NULL ELSE round(b.p50, 1) END AS t1
            FROM read_parquet('{a}') a JOIN read_parquet('{b}') b USING (from_id, to_id)
            WHERE a.from_id IN ({ids})
              AND ((NOT a.unreachable AND a.p50 <= {limit_min}) OR (NOT b.unreachable AND b.p50 <= {limit_min})
                   OR (NOT b.unreachable AND (a.unreachable OR a.p50 - b.p50 >= {min_gain})))""").df()
