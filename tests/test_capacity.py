"""Track-capacity check (plans/P3.md D6) on a synthetic section graph."""
import pandas as pd

from lab import capacity as cap


def tp(rid, pts, hour, exclusion=None, cancelled=False):
    return [dict(rid=rid, seq=i, tpl=t, dep_s=hour * 3600 + 60 * i, arr_s=hour * 3600 + 60 * i,
                 exclusion=exclusion, journey_cancelled=cancelled) for i, t in enumerate(pts)]


def table():
    rows = []
    for k in range(6):                                    # six trains A→J→B in hour 8, two in hour 12
        rows += tp(f"p{k}", ["A", "J", "B"], 8)
    for k in range(2):
        rows += tp(f"q{k}", ["A", "J", "B"], 12)
    rows += tp("ecs", ["A", "J", "B"], 8, exclusion="not_passenger")       # counts
    rows += tp("bus", ["A", "J", "B"], 8, exclusion="road_BS")             # does not
    rows += tp("can", ["A", "J", "B"], 8, cancelled=True)                  # does not
    return pd.DataFrame(rows)


SECTIONS = [{"from": "A", "to": "J"}, {"from": "J", "to": "B"}, {"from": "B", "to": "C", "timed": False},
            {"from": "C", "to": "B", "timed": False}]
CAP = {"defaults": {"tracks": 2, "double_track_tph": 6, "single_track_margin_min": 3, "single_track_default_tph": 2},
       "sections": {"B-C": {"tracks": 1}, "C-B": {"tracks": 1}},
       "freight": [{"name": "x", "tph": 1, "sections": ["J-B", "B-C"]}]}
PERIODS = {"AM": (420, 600), "IP": (600, 960)}


def test_base_use_counts_every_train_that_uses_track():
    b = cap.base_use(table()).set_index(["from", "to", "hour"]).trains
    assert b[("A", "J", 8)] == 7 and b[("J", "B", 8)] == 7 and b[("A", "J", 12)] == 2
    assert cap.base_use(table(), {"p0", "p1"}).set_index(["from", "to", "hour"]).trains[("A", "J", 8)] == 5


def test_limits_default_single_track_and_floor_at_today():
    lim = cap.limits(SECTIONS, CAP, cap.base_use(table()), {("B", "C"): 5.0, ("C", "B"): 5.0}).set_index(["from", "to"])
    assert lim.loc[("A", "J"), "limit_tph"] == 7 and "busiest hour" in lim.loc[("A", "J"), "limit_basis"]
    assert lim.loc[("B", "C"), "limit_tph"] == 3 and lim.loc[("B", "C"), "tracks"] == 1       # 60 / (10 + 6)
    assert lim.loc[("J", "B"), "freight_allowance_tph"] == 1 and lim.loc[("A", "J"), "freight_allowance_tph"] == 0
    assert not lim.loc[("B", "C"), "timed_today"]


def feeds(headway):
    trips, st = [], []
    for n, t0 in enumerate(range(480, 540, headway)):
        trips.append({"route_id": "G", "trip_id": f"g{n}"})
        for i, (s, t) in enumerate((("sB", t0), ("sC", t0 + 5))):
            st.append({"trip_id": f"g{n}", "stop_id": s, "stop_sequence": str(i + 1),
                       "departure_time": f"{t // 60:02d}:{t % 60:02d}:00"})
    return {"generated": {"routes": pd.DataFrame([{"route_id": "G"}]), "trips": pd.DataFrame(trips),
                          "stop_times": pd.DataFrame(st)}}


REPORT = [{"type": "add_line", "route_id": "G", "legs": [{"from": "sB", "to": "sC", "timing_points": ["J", "B", "C"]}]}]


def test_scenario_use_and_the_check_flags_only_the_overloaded_section():
    base = cap.base_use(table())
    lim = cap.limits(SECTIONS, CAP, base, {("B", "C"): 5.0, ("C", "B"): 5.0})
    s = cap.scenario_use(feeds(30), REPORT)                                # two trains in hour 8
    assert s.set_index(["from", "to", "hour"]).trains.to_dict() == {("B", "C", 8): 2, ("J", "B", 8): 2}
    c = cap.check(lim, base, s, PERIODS).set_index(["from", "to", "period"])
    assert set(c.index.get_level_values(0)) == {"J", "B"}                  # only sections the scenario uses
    jb = c.loc[("J", "B", "AM")]
    assert (jb.base_tph, jb.freight_allowance_tph, jb.scenario_tph, jb.total_tph, jb.limit_tph) == (7, 1, 2, 10, 7)
    assert jb.over and not c.loc[("J", "B", "IP")].over                    # 2 + 1 + 0 against 7
    bc = c.loc[("B", "C", "AM")]
    assert bc.total_tph == 3 and bc.limit_tph == 3 and not bc.over         # 2 + 1 freight on single track
    s4 = cap.scenario_use(feeds(15), REPORT)
    assert cap.check(lim, base, s4, PERIODS).set_index(["from", "to", "period"]).loc[("B", "C", "AM")].over
