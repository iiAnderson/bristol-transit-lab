"""Pairs are not people (plans/P3.md §8, P3b stop): the people-weighted comparison on a
skim small enough to work by hand."""
import pandas as pd
import pytest

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


def test_connectivity_counts_decay_weights_and_sets_pt_against_car():
    import math
    pt = skim({("o1", "C"): 20, ("o1", "D"): 40, ("o2", "C"): 50, ("o2", "D"): None, ("o3", "C"): 10, ("o3", "D"): 10})
    car = pd.DataFrame({"from_id": ["o1", "o1", "o2", "o3"], "to_id": ["C", "D", "C", "D"], "ivt_min": [10, 12, 44, 50]})
    cj = rn.car_jobs_within(car, JOBS, 45)
    assert cj.to_dict() == {"o1": 2000.0, "o2": 1000.0}
    edge = pd.Series({"o1": False, "o2": False, "o3": True})
    s, o = rn.connectivity(pt, cj, JOBS, RES, edge, [30, 45], 0.05, 45)
    assert o.pt_jobs_30.to_dict() == {"o1": 1000.0, "o2": 0.0, "o3": 2000.0} and o.pt_jobs_45["o1"] == 2000
    assert o.pt_jobs_decay["o1"] == pytest.approx(1000 * (math.exp(-1) + math.exp(-2)))
    assert o.pt_jobs_decay["o2"] == pytest.approx(1000 * math.exp(-2.5))
    assert o.pt_car_ratio.fillna(-1).to_dict() == {"o1": 1.0, "o2": 0.0, "o3": -1.0}        # o3: no jobs by car in 45
    r = s["pt_over_car_jobs_45min"]
    assert r["resident_weighted_mean"] == 0.25 and r["residents_with_no_jobs_by_car_in_the_threshold"] == 50
    assert r["residents_with_under_a_tenth_of_the_car's_jobs"] == 300
    assert s["jobs_30min"]["resident_weighted_mean"] == pytest.approx((100 * 1000 + 50 * 2000) / 450)
