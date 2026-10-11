"""Pairs are not people (plans/P3.md §8, P3b stop): the people-weighted comparison on a
skim small enough to work by hand."""
import pandas as pd

from lab import run as rn


def skim(times):
    return pd.DataFrame([{"from_id": o, "to_id": d, "p50": t, "unreachable": t is None} for (o, d), t in times.items()])


RES = pd.Series({"o1": 100, "o2": 300, "o3": 50})
JOBS = pd.Series({"C": 1000.0, "D": 1000.0})
PARENT = {("o1", "C"): 30, ("o1", "D"): 40, ("o2", "C"): 20, ("o2", "D"): 20, ("o3", "C"): None, ("o3", "D"): 25}


def test_residents_better_and_worse_off_are_both_counted():
    scen = PARENT | {("o1", "C"): 24, ("o2", "C"): 23, ("o2", "D"): 22, ("o3", "C"): 50}
    res, by_oa = rn.people_summary(skim(PARENT), skim(scen), RES, JOBS, "C")
    c, j = res["to_the_centre"], res["to_jobs_weighted_mean_time"]
    assert c["residents_faster_by_threshold"] == 100 and c["residents_faster_by_5_min"] == 100
    assert c["residents_slower_by_threshold"] == 300 and c["residents_newly_reachable"] == 50
    # jobs-weighted mean time: o1 35 → 32 (3 faster), o2 20 → 22.5 (2.5 slower), o3 unchanged on D
    assert j["residents_faster_by_threshold"] == 100 and j["residents_slower_by_threshold"] == 300
    assert by_oa.set_index("OA21CD").jobs_time_change_min.round(2).to_dict() == {"o1": -3.0, "o2": 2.5, "o3": 0.0}


def test_the_range_across_offsets_is_reported_beside_the_mean():
    a = PARENT | {("o1", "C"): 24}                       # this offset helps o1
    b = PARENT | {("o2", "C"): 15, ("o1", "C"): 33}      # this one helps o2 and costs o1
    per = [rn.people_summary(skim(PARENT), skim(x), RES, JOBS, "C")[0] for x in (a, b)]
    rg = rn.people_range(per)
    assert rg["to_the_centre"]["residents_faster_by_threshold"] == {"by_offset": [100, 300], "min": 100, "max": 300}
    assert rg["to_the_centre"]["residents_slower_by_threshold"]["by_offset"] == [0, 100]
