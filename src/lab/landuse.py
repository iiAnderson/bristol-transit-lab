"""Land use (SPEC §4; plans/P3.md Q8): the minimal baseline ``L2026`` — residents by OA
from Census 2021 TS001, jobs by OA from BRES LSOA jobs split by Census workplace counts.
Students and floorspace arrive in P4. ``landuse_delta`` ops are recorded by the scenario
engine but not yet applied to this table."""
from __future__ import annotations

import pandas as pd

from . import coverage as cov


def build(oa: pd.DataFrame, residents: pd.DataFrame, bres: pd.DataFrame, workplace: pd.DataFrame,
          version: str) -> tuple[pd.DataFrame, dict]:
    """``oa``: OA21CD, LSOA21CD (the internal OAs); ``residents``: OA21CD, residents;
    ``bres``: LSOA21CD, jobs; ``workplace``: OA21CD, workers. One row per OA."""
    o = oa.merge(residents, on="OA21CD", how="left")
    missing = o.residents.isna().sum()
    if missing:
        raise ValueError(f"{missing} internal OAs have no resident count")
    jobs, info = cov.oa_jobs(bres, o.merge(workplace, on="OA21CD", how="left").fillna({"workers": 0}))
    o = o.merge(jobs, on="OA21CD")
    out = pd.DataFrame({"landuse_version": version, "zone_id": o.OA21CD, "level": "OA", "parent_id": o.LSOA21CD,
                        "residents": o.residents.astype(int), "jobs": o.jobs,
                        "source_tag": "residents: Census 2021 TS001; jobs: BRES LSOA split by Census 2021 workplace counts"})
    info |= {"zones": int(len(out)), "residents": int(out.residents.sum())}
    return out, info
