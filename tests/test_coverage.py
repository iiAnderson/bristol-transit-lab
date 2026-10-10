"""Frequent-service coverage (plans/P3.md P3a) on synthetic inputs with known answers."""
import numpy as np
import pandas as pd
import pytest

from lab import coverage as cov

PERIODS = cov.parse_periods({"AM": "07:00-10:00", "IP": "10:00-16:00"})
ARE = {"walk_kmh": 4.8, "frequent_headway_min": [10, 15], "score_cap_dph": 12,
       "are_interval_bands_min": [5, 10, 20, 40, 60],
       "are_stop_category": {"rail_node": [1, 1, 2, 3, 4], "rail_line": [1, 2, 3, 4, 5],
                             "street": [2, 3, 4, 5, 5]},
       "are_distance_bands_m": [300, 500, 750, 1000],
       "are_class": {1: list("AABC"), 2: list("ABCD"), 3: ["B", "C", "D", "none"],
                     4: ["C", "D", "none", "none"], 5: ["D", "none", "none", "none"]},
       "rail_node_min_directions": 3,
       "served_cut_min": {"bus": 10, "rail": 20, "ferry": 10, "brt": 10, "tram": 20, "metro": 20}}
OBS = {"bus": {"mean_m": 580, "p85_m": 800}, "rail": {"mean_m": 1010, "p85_m": 1610}}


def hms(m):
    return f"{int(m // 60):02d}:{int(m % 60):02d}:00"


def feed(headway_e=10, headway_w=10, restricted=False):
    """One street, three stops W–M–E 400 m apart (plus a far stop X on a second route);
    route R runs both ways 07:00–16:00. With ``restricted`` every eastbound trip is
    written as two copies of one source trip, and calls at M are set-down only in one."""
    stops = pd.DataFrame({"stop_id": ["W", "M", "E", "X"], "stop_name": ["w", "m", "e", "x"],
                          "stop_lat": ["51.45"] * 3 + ["51.47"],
                          "stop_lon": ["-2.6058", "-2.6", "-2.5942", "-2.6"]})
    trips, st = [], []
    for d, seq, hw in ((0, ["W", "M", "E"], headway_e), (1, ["E", "M", "W"], headway_w)):
        for k, t0 in enumerate(np.arange(7 * 60, 16 * 60, hw)):
            copies = ["a", "b"] if restricted and d == 0 else [""]
            for c in copies:
                tid = f"t{d}_{k}{c}"
                trips.append((tid, "R", f"t{d}_{k}"))
                for i, s in enumerate(seq):
                    pick = "1" if (c == "b" and s == "M") else "0"
                    st.append((tid, s, i, hms(t0 + 2 * i), pick))
    for k, t0 in enumerate(np.arange(7 * 60, 16 * 60, 60)):          # coach, hourly, M -> X
        trips.append((f"c{k}", "C", f"c{k}"))
        st += [(f"c{k}", "M", 0, hms(t0), "0"), (f"c{k}", "X", 1, hms(t0 + 9), "0")]
    return {"stops": stops,
            "routes": pd.DataFrame({"route_id": ["R", "C"], "route_type": ["3", "200"]}),
            "trips": pd.DataFrame(trips, columns=["trip_id", "route_id", "original_trip_id"]),
            "stop_times": pd.DataFrame(st, columns=["trip_id", "stop_id", "stop_sequence",
                                                    "departure_time", "pickup_type"])}


def test_mode_classes():
    assert cov.mode_class("3", "r", {"r"}) == "brt"
    assert [cov.mode_class(t, "x", set()) for t in ("0", "1", "2", "3", "4", "200", "109", "715")] == \
        ["tram", "metro", "rail", "bus", "ferry", "coach", "rail", "bus"]
    with pytest.raises(ValueError):
        cov.mode_class("6", "x", set())


def test_departures_reconcile_with_an_independent_count():
    f = feed(10, 20)
    dep = cov.departures(f, set())
    # independent rebuild: every stop_time that is not its trip's last call
    st = f["stop_times"]
    last = st.groupby("trip_id").stop_sequence.transform("max")
    indep = st[st.stop_sequence != last].groupby("stop_id").size()
    assert dep.groupby("stop_id").size().to_dict() == indep.to_dict()
    ss = cov.stop_service(dep, pd.Series({"W": "c:W", "M": "c:M", "E": "c:E", "X": "c:X"}), PERIODS)
    m = ss[(ss.stop_id == "M") & (ss.period == "AM") & (ss.mode_class == "bus")].set_index("direction_group")
    assert m.loc[2, "departures_per_hour"] == 6 and m.loc[6, "departures_per_hour"] == 3   # east, west
    assert m.loc[6, "max_headway_min"] == 20
    assert "X" not in set(ss.stop_id)                     # a terminus has no departures


def test_restriction_copies_are_one_journey_and_set_down_only_is_not_a_departure():
    plain = cov.departures(feed(), set())
    split = cov.departures(feed(restricted=True), set())
    key = ["stop_id", "journey", "dep_min"]
    assert plain[key].sort_values(key).reset_index(drop=True).equals(
        split[key].sort_values(key).reset_index(drop=True))
    f = feed(restricted=True)
    f["stop_times"].loc[f["stop_times"].stop_id == "M", "pickup_type"] = "1"
    none_at_m = cov.departures(f, set())
    assert "M" not in set(none_at_m.stop_id)


def test_cluster_service_halves_two_way_stops_but_not_termini():
    dep = cov.departures(feed(10, 10), set())
    cs = cov.cluster_service(dep, pd.Series({s: "c:" + s for s in "WMEX"}), PERIODS, 0.1)
    am = cs[(cs.period == "AM") & (cs.mode_class == "bus")].set_index("cluster_id")
    assert am.loc["c:M", "sides"] == 2 and am.loc["c:M", "dph_one_way"] == 6
    assert am.loc["c:W", "sides"] == 1 and am.loc["c:W", "dph_one_way"] == 6
    h = cov.headline(cs, ["AM", "IP"]).set_index(["cluster_id", "mode_class"])
    assert h.loc[("c:M", "bus"), "dph_one_way"] == 6


def test_headline_is_the_worse_period_and_zero_if_a_period_has_no_service():
    f = feed()
    st = f["stop_times"]
    f["stop_times"] = st[st.departure_time < "10:00:00"]              # peak-only service
    dep = cov.departures(f, set())
    cs = cov.cluster_service(dep, pd.Series({s: "c:" + s for s in "WMEX"}), PERIODS, 0.1)
    h = cov.headline(cs, ["AM", "IP"]).set_index(["cluster_id", "mode_class"])
    assert h.loc[("c:M", "bus"), "dph_one_way"] == 0


def test_clusters_pair_same_name_stops_and_the_barrier_test_cuts_them():
    stops = pd.DataFrame({"stop_id": ["a1", "a2", "b1", "z"], "stop_name": ["High St", "high st", "Quay", "High St"],
                          "lon": [-2.6, -2.6009, -2.6003, -2.62], "lat": [51.45, 51.45, 51.4501, 51.45]})
    pairs = cov.candidate_pairs(stops, 150, 40)
    assert {tuple(sorted(p)) for p in zip(pairs.a, pairs.b)} == {("a1", "a2"), ("a1", "b1")}
    c, cut = cov.clusters(list(stops.stop_id), pairs)
    assert c["a1"] == c["a2"] == c["b1"] != c["z"] and cut.empty
    walk = pd.Series({("a1", "a2"): 9.0, ("a2", "a1"): 2.0, ("a1", "b1"): 1.0})   # a river between a1 and a2
    c, cut = cov.clusters(list(stops.stop_id), pairs, walk, 4)
    assert c["a1"] == c["b1"] != c["a2"] and len(cut) == 1
    c, cut = cov.clusters(list(stops.stop_id), pairs, pd.Series({("a1", "a2"): 2.0}), 4)
    assert c["a1"] == c["a2"] != c["b1"]                    # no walk found = cut


@pytest.mark.parametrize("mode", ["bus", "rail"])
def test_logistic_decay_reproduces_the_mean_and_85th_percentile(mode):
    c = cov.fit_logistic(**OBS[mode])
    assert abs(c["fit_mean_m"] - OBS[mode]["mean_m"]) < 0.5
    assert abs(c["fit_p85_m"] - OBS[mode]["p85_m"]) < 0.5
    d = np.linspace(0, 20000, 400001)
    w = cov._logistic(d, c["mu_m"], c["s_m"])
    assert abs(np.trapezoid(w, d) - OBS[mode]["mean_m"]) < 1          # mean = ∫ survival
    assert w[0] == 1 and abs(float(cov._logistic(OBS[mode]["p85_m"], c["mu_m"], c["s_m"])) - 0.15) < 1e-6
    assert cov.fit_exponential(**OBS[mode])["fit_p85_m"] > OBS[mode]["p85_m"] * 1.15   # why not exponential


def test_interpolated_classes_sit_between_bus_and_rail():
    cu = cov.decay_curves(OBS, {"brt": 0.33, "tram": 0.67})
    h = {m: cov.half_weight_m(cu[m]) for m in cu}
    assert h["bus"] < h["brt"] < h["tram"] < h["rail"] and h["ferry"] == h["bus"]
    t = 10.0
    assert float(cov.weight(t, cu["bus"], 4.8)) < float(cov.weight(t, cu["rail"], 4.8))
    assert float(cov.weight(t, cu["bus"], 4.8, scale=0.5)) < float(cov.weight(t, cu["bus"], 4.8))


def test_are_tables():
    k = (ARE["are_interval_bands_min"], ARE["are_stop_category"], 3)
    assert cov.stop_category("bus", 2, 4, *k) == 2 and cov.stop_category("bus", 2, 5, *k) == 3
    assert cov.stop_category("bus", 2, 15, *k) == 4 and cov.stop_category("bus", 2, 60, *k) == 5
    assert cov.stop_category("bus", 2, 61, *k) is None and cov.stop_category("coach", 2, 5, *k) is None
    assert cov.stop_category("rail", 2, 30, *k) == 4 and cov.stop_category("rail", 4, 30, *k) == 3
    d = (4.8, ARE["are_distance_bands_m"], ARE["are_class"])
    assert cov.are_class(2, 3, *d) == "A" and cov.are_class(2, 5, *d) == "B"       # 240 m, 400 m
    assert cov.are_class(4, 5, *d) == "D" and cov.are_class(4, 8, *d) == "none"
    assert cov.are_class(1, 13, *d) == "none" and cov.are_class(None, 1, *d) == "none"


def test_score_on_a_line_of_origins_with_known_answers():
    cu = cov.decay_curves(OBS, {})
    # one bus cluster every 10 min each way, one hourly; origins 2, 8, 12 min from the
    # first and 1 min from the second; a fourth origin near a coach-only stop
    level = pd.DataFrame({"cluster_id": ["c:f", "c:h", "c:coach"], "mode_class": ["bus", "bus", "coach"],
                          "dph_one_way": [6.0, 1.0, 6.0], "directions": [2, 2, 2]})
    walk = pd.DataFrame({"OA21CD": ["o2", "o8", "o12", "o12", "oc"], "cluster_id": ["c:f", "c:f", "c:f", "c:h", "c:coach"],
                         "walk_min": [2.0, 8.0, 12.0, 1.0, 1.0]})
    s = cov.score(walk, level, cu, ARE).set_index("OA21CD")
    assert list(s.loc[["o2", "o8", "o12", "oc"], "frequent_10"]) == [True, True, False, False]   # cut-off 10 min
    assert s.loc["o2", "are_class"] == "C" and s.loc["o8", "are_class"] == "none"     # category IV at 160 m, 640 m
    assert s.loc["o12", "are_class"] == "D" and s.loc["oc", "are_class"] == "none"    # hourly (V) at 80 m; coach
    assert s.loc["oc", "score"] == 0
    w2 = float(cov.weight(2.0, cu["bus"], 4.8))
    assert abs(s.loc["o2", "score"] - 6 * w2) < 1e-9 and abs(s.loc["o2", "frequent_10_weight"] - w2) < 1e-9
    assert s.loc["o8", "frequent_10_walk_min"] == 8
    r = cov.score(walk, level, cu, ARE | {"scale": 0.5}).set_index("OA21CD")       # reduced mobility
    assert list(r.loc[["o2", "o8"], "frequent_10"]) == [True, False]


def test_whole_minute_walk_times_change_little_when_rounded():
    cu = cov.decay_curves(OBS, {})
    for m in ("bus", "rail"):
        e = cov.rounding_effect(cu[m], 4.8, OBS[m]["p85_m"])
        assert abs(e["rounded"]["weighted_area_vs_exact_pct"]) < 3
        assert e["truncated"]["weighted_area_vs_exact_pct"] > 0       # truncation flatters access


def test_oa_jobs_split_and_summary():
    j, info = cov.oa_jobs(pd.DataFrame({"LSOA21CD": ["L1", "L2"], "jobs": [100, 30]}),
                          pd.DataFrame({"OA21CD": ["a", "b", "c", "d"], "LSOA21CD": ["L1", "L1", "L2", "L2"],
                                        "workers": [30, 10, 0, 0]}))
    assert j.set_index("OA21CD").jobs.to_dict() == {"a": 75, "b": 25, "c": 15, "d": 15}
    assert info["lsoas_split_equally"] == 1 and info["jobs_total"] == 130
    oa = pd.DataFrame({"residents": [100, 300], "jobs": [10, 20], "are_class": ["A", "none"], "score": [6.0, 0.0],
                       "frequent_10": [True, False], "frequent_10_weight": [0.5, 0.0], "dec": [1, 1]})
    s = cov.summarise(oa, "dec").iloc[0]
    assert s.residents_frequent_10 == 100 and s.residents_not_frequent_10 == 300
    assert s.weighted_residents_frequent_10 == 50 and s.mean_score_resident_weighted == 1.5
