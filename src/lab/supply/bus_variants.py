"""
Resolve same-start bus timetable variants against what ran (plans/P2.md A4: First
Bristol 5/77).

For each operator × line in the variant groups, count distinct vehicle journeys on the
modelled day by SIRI-VM ``destination_ref``, and compare with the terminal stops of the
competing timetable variants. A variant whose terminus no vehicle was bound for is the
superseded one.
"""
from __future__ import annotations

import duckdb
import pandas as pd


def observed_destinations(day_parquet, operator: str, lines: list[str]) -> pd.DataFrame:
    q = ",".join(f"'{x}'" for x in lines)
    return duckdb.connect().execute(f"""
        SELECT published_line_name line, destination_ref,
               count(DISTINCT vehicle_ref || '|' || coalesce(dated_vehicle_journey_ref, '')) journeys,
               count(*) positions
        FROM read_parquet('{day_parquet}')
        WHERE NOT stale AND operator_ref = '{operator}' AND published_line_name IN ({q})
        GROUP BY ALL ORDER BY 1, 3 DESC""").df()


def resolve(groups: list, observed: pd.DataFrame) -> pd.DataFrame:
    """groups: rows (noc, line, first stop, first departure, [terminals], [trip ids])
    from the bus build. Returns one row per trip: keep or drop, and why."""
    seen = observed.groupby("line")["destination_ref"].apply(set).to_dict()
    out = []
    for noc, line, s0, d0, terms, trips in groups:
        ran = seen.get(line, set())
        hit = [t in ran for t in terms]
        for t, tid, h in zip(terms, trips, hit):
            decision = ("keep" if h else "drop") if any(hit) and not all(hit) else "unresolved"
            out.append({"noc": noc, "line": line, "first_stop": s0, "first_departure": d0,
                        "terminal": t, "trip_id": tid, "vehicles_bound_there": h,
                        "decision": decision})
    return pd.DataFrame(out)
