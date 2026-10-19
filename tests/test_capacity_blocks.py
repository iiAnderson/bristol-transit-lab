import pandas as pd
import pytest

from lab import capacity as cp


def _cap(**over):
    cap = {"defaults": {"tracks": 2, "double_track_tph": 12, "single_track_margin_min": 3,
                        "single_track_default_tph": 2, "block_station_dwell_min": 0.5},
           "sections": {}, "freight": [],
           "blocks": {"line": {"points": ["A", "B", "C", "D"]}}}
    cap.update(over)
    return cap


RUNS = {("A", "B"): 2.0, ("B", "C"): 1.5, ("C", "D"): 2.0, ("B", "A"): 2.0, ("C", "B"): 1.5, ("D", "C"): 2.0}


def test_block_limit_uses_the_whole_block_not_the_station_pair():
    lim = cp.block_limits(_cap(), RUNS)
    # 5.5 min run + 2 × 0.5 dwell = 6.5; 60 ÷ (13 + 6) = 3.16 → 3, for every section both ways
    assert {v["limit"] for v in lim.values()} == {3}
    assert set(lim) == {"A-B", "B-A", "B-C", "C-B", "C-D", "D-C"}
    # the per-section formula would have allowed floor(60 ÷ (2 × 2 + 6)) = 6 on A–B


def test_a_missing_run_time_is_an_error():
    with pytest.raises(cp.CapacityError, match="no run time"):
        cp.block_limits(_cap(), {("A", "B"): 2.0})


def test_limits_report_one_track_and_the_block_limit():
    secs = [{"from": a, "to": b} for a, b in [("A", "B"), ("B", "C"), ("X", "Y")]]
    base = pd.DataFrame({"from": ["A"], "to": ["B"], "hour": [8], "trains": [2]})
    out = cp.limits(secs, _cap(), base, tp_runs=RUNS).set_index(["from", "to"])
    assert out.loc[("A", "B"), "tracks"] == 1 and out.loc[("A", "B"), "limit_tph"] == 3
    assert out.loc[("X", "Y"), "tracks"] == 2 and out.loc[("X", "Y"), "limit_tph"] == 12


def test_a_scenario_run_time_replaces_the_timetable_one():
    lim = cp.block_limits(_cap(), RUNS | {("A", "B"): 6.0, ("B", "A"): 6.0})
    assert {v["limit"] for v in lim.values()} == {2}          # 60 ÷ (2 × (9.5 + 1) + 6) = 2.2


def test_a_passing_loop_splits_the_block():
    cap = cp.apply_works(_cap(), [{"name": "loop", "loops": [{"at": "C"}]}])
    assert {n: b["points"] for n, b in cap["blocks"].items()} == {"line/a": ["A", "B", "C"], "line/b": ["C", "D"]}
    lim = cp.block_limits(cap, RUNS)
    assert lim["A-B"]["limit"] > cp.block_limits(_cap(), RUNS)["A-B"]["limit"]


def test_a_loop_outside_a_block_or_at_its_end_is_an_error():
    for at in ("A", "D", "Z"):
        with pytest.raises(cp.CapacityError, match="exactly one"):
            cp.apply_works(_cap(), [{"name": "x", "loops": [{"at": at}]}])


def test_redoubling_part_of_a_block_leaves_the_rest_single():
    cap = cp.apply_works(_cap(), [{"name": "w", "track": [{"sections": ["B-C"], "tracks": 2}]}])
    assert {n: b["points"] for n, b in cap["blocks"].items()} == {"line/1": ["A", "B"], "line/2": ["C", "D"]}
    secs = [{"from": "B", "to": "C"}, {"from": "A", "to": "B"}]
    out = cp.limits(secs, cap, pd.DataFrame(columns=["from", "to", "hour", "trains"]), tp_runs=RUNS).set_index(["from", "to"])
    assert out.loc[("B", "C"), "tracks"] == 2 and out.loc[("B", "C"), "limit_tph"] == 12
    assert out.loc[("A", "B"), "tracks"] == 1


def test_redoubling_a_whole_block_removes_it():
    cap = cp.apply_works(_cap(), [{"name": "w", "track": [{"sections": ["A-B", "B-C", "C-D"], "tracks": 2}]}])
    assert cap["blocks"] == {}


def test_works_never_reduce_tracks_and_never_touch_the_input():
    orig = _cap()
    with pytest.raises(cp.CapacityError, match="may not reduce"):
        cp.apply_works(orig, [{"name": "w", "track": [{"sections": ["X-Y"], "tracks": 1}]}])
    cp.apply_works(orig, [{"name": "w", "track": [{"sections": ["A-B"], "tracks": 2}]}])
    assert list(orig["blocks"]) == ["line"] and orig["sections"] == {}


def test_a_truncated_journey_counts_only_the_part_that_still_runs():
    tp = pd.DataFrame({"rid": ["r1"] * 4, "seq": [0, 1, 2, 3], "tpl": ["A", "B", "C", "D"],
                       "arr_s": [None, 3600.0 + 60, 3600.0 + 120, 3600.0 + 180], "dep_s": [3600.0, 3600.0 + 60, 3600.0 + 120, None],
                       "journey_cancelled": False, "exclusion": ""})
    before = cp.copied_use(tp, [{"of": "r1", "shift_min": 0, "trim": {"tiploc": "C", "keep": "before"}}])
    assert set(zip(before["from"], before["to"])) == {("A", "B"), ("B", "C")}
    after = cp.copied_use(tp, [{"of": "r1", "shift_min": 0, "trim": {"tiploc": "C", "keep": "after"}}])
    assert set(zip(after["from"], after["to"])) == {("C", "D")}
    with pytest.raises(ValueError, match="does not pass"):
        cp.copied_use(tp, [{"of": "r1", "shift_min": 0, "trim": {"tiploc": "Z", "keep": "before"}}])


def test_a_generated_route_replaced_by_a_later_op_adds_no_use():
    feed = {"stop_times": pd.DataFrame(columns=["trip_id", "stop_id", "stop_sequence", "departure_time"]),
            "trips": pd.DataFrame(columns=["trip_id", "route_id"]), "routes": pd.DataFrame({"route_id": ["GEN-OLD"]})}
    ops = [{"route_id": "GEN-OLD", "legs": [{"from": "a", "to": "b", "timing_points": ["TPA", "TPB"]}]}]
    out = cp.scenario_use({"generated": feed}, ops)
    assert out.empty
