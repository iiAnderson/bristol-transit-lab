"""The scenario generator (plans/P3.md P3b-2) on synthetic feeds with known answers."""
import json

import pandas as pd
import pytest

from lab import generator as gen

PERIODS = {"AM": (420, 600), "IP": (600, 960), "PM": (960, 1140)}


def ctx(runs=None, dwell=None):
    return {"periods": PERIODS, "service_date": "20260923", "runs": runs or {}, "dwell": dwell or {None: 60.0},
            "min_trains": 5}


def fc(*feats):
    return json.dumps({"type": "FeatureCollection", "features": list(feats)})


def line(coords, **props):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props}


def point(lon, lat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def bus_feed(headway=15, restricted=False, start=420, end=960):
    """Route R, stops a–b–c both ways, a trip every ``headway`` min, 5 min between
    stops. With ``restricted`` each eastbound source trip is two copies."""
    trips, st = [], []
    for d, seq in ((0, "abc"), (1, "cba")):
        for n, t0 in enumerate(range(start, end, headway)):
            src = f"t{d}_{n}"
            for c in (["#0", "#1"] if restricted and d == 0 else [""]):
                tid = src + c
                trips.append({"route_id": "R", "service_id": "S", "trip_id": tid, "direction_id": str(d),
                              "original_trip_id": src})
                for i, s in enumerate(seq):
                    if c == "#1" and i == 0:
                        continue                      # the second copy starts at the second stop
                    st.append({"trip_id": tid, "arrival_time": gen.hms(t0 + 5 * i), "departure_time": gen.hms(t0 + 5 * i),
                               "stop_id": s, "stop_sequence": str(i + 1), "pickup_type": "0", "drop_off_type": "0"})
    return {"bus": {"stops": pd.DataFrame({"stop_id": list("abc"), "stop_name": list("ABC"),
                                           "stop_lat": ["51.45"] * 3, "stop_lon": ["-2.60", "-2.59", "-2.58"]}),
                    "routes": pd.DataFrame([{"route_id": "R", "agency_id": "X", "route_type": "3"},
                                            {"route_id": "Q", "agency_id": "X", "route_type": "3"}]),
                    "trips": pd.DataFrame(trips), "stop_times": pd.DataFrame(st)}}


def times(feed, route="R"):
    """Stop times of a route as a set of (direction, stop, departure): ids ignored."""
    t = feed["trips"][feed["trips"].route_id == route]
    s = feed["stop_times"].merge(t[["trip_id", "direction_id"]], on="trip_id")
    return sorted(zip(s.direction_id, s.stop_id, s.departure_time, s.stop_sequence))


def test_leg_time_follows_the_speed_profile():
    assert gen.leg_time_s(1000, 72, 1.0) == pytest.approx(1000 / 20 + 20)        # reaches 20 m/s
    assert gen.leg_time_s(100, 72, 1.0) == pytest.approx(20)                       # never reaches it
    assert gen.leg_time_s(400, 72, 1.0) == pytest.approx(40)                       # exactly the threshold


def test_period_windows_and_departures_with_offsets():
    w = gen.period_windows(PERIODS | {"OP": (0, 0)}, "05:30-00:00")
    assert w["AM"] == [(420, 600)] and w["OP"] == [(330, 420), (1140, 1440)]
    d0 = gen.departures(w, {"AM": 20}, 0.0)
    assert [t for t, _ in d0] == [420, 440, 460, 480, 500, 520, 540, 560, 580]
    d1 = gen.departures(w, {"AM": 20}, 1 / 3)
    assert d1[0][0] == pytest.approx(426.6667, abs=1e-3) and len(d1) == 9
    # same phase fraction of each headway, whatever the headway
    assert gen.departures(w, {"AM": 30}, 0.5)[0][0] == 435
    assert gen.departures(w, {"AM": 20}, 0.0, anchor={"AM": 427})[0][0] == 427


def test_no_ops_leave_the_parent_feed_identical():
    f = bus_feed()
    out, rep = gen.apply(f, [], None, ctx(), 0.0)
    assert rep == [] and all(out["bus"][k].equals(f["bus"][k]) for k in f["bus"])


@pytest.mark.parametrize("restricted", [False, True])
def test_retiming_at_the_same_headway_reproduces_the_stop_times(restricted):
    f = bus_feed(15, restricted)
    out, rep = gen.apply(f, [("modify_route", {"route_id": "R", "headways_min": {"AM": 15, "IP": 15}})], None, ctx(), 0.0)
    assert times(out["bus"]) == times(f["bus"])
    r = rep[0]["headways"]
    assert r["journeys_removed"] == r["journeys_added"] == 72
    # a source trip and its copies stay one journey
    assert out["bus"]["trips"].original_trip_id.nunique() == f["bus"]["trips"].original_trip_id.nunique()
    assert len(out["bus"]["trips"]) == len(f["bus"]["trips"])


def test_retiming_changes_the_frequency_and_offsets_shift_it():
    f = bus_feed(15)
    out, rep = gen.apply(f, [("modify_route", {"route_id": "R", "headways_min": {"IP": 10}})], None, ctx(), 0.0)
    t = out["bus"]["trips"].merge(out["bus"]["stop_times"][out["bus"]["stop_times"].stop_sequence == "1"])
    starts = sorted(gen.mins(t[t.direction_id == "0"].departure_time))
    ip = [x for x in starts if 600 <= x < 960]
    assert len(ip) == 36 and ip[:3] == [600, 610, 620]
    assert [x for x in starts if x < 600] == list(range(420, 600, 15))            # AM untouched
    assert rep[0]["headways"]["periods"]["IP"]["0"] == {"journeys_before": 24, "journeys_after": 36, "headway_min": 10}
    out2, _ = gen.apply(f, [("modify_route", {"route_id": "R", "headways_min": {"IP": 10}})], None, ctx(), 0.5)
    t2 = out2["bus"]["trips"].merge(out2["bus"]["stop_times"][out2["bus"]["stop_times"].stop_sequence == "1"])
    ip2 = sorted(x for x in gen.mins(t2[t2.direction_id == "0"].departure_time) if 600 <= x < 960)
    assert ip2[0] == 605 and len(ip2) == 36


def test_remove_route_and_unbuilt_ops_fail_loudly(tmp_path):
    f = bus_feed()
    out, rep = gen.apply(f, [("remove_route", {"route_id": "R"})], None, ctx(), 0.0)
    assert out["bus"]["trips"].empty and out["bus"]["stop_times"].empty and rep[0]["trips_removed"] == 72
    assert "R" not in set(out["bus"]["routes"].route_id) and len(f["bus"]["trips"]) == 72      # input untouched
    for kind, op in (("reroute", {"route_id": "R"}), ("add_stop", {"route_id": "R"}),
                     ("modify_route", {"route_id": "R", "truncate_at": "b"}),
                     ("modify_route", {"route_id": "R", "stopping_pattern": {"add": ["x"]}})):
        with pytest.raises(gen.GeneratorError):
            gen.apply(f, [(kind, op)], tmp_path, ctx(), 0.0)


def test_removing_a_call_gives_back_its_dwell():
    f = bus_feed()
    st = f["bus"]["stop_times"]
    mid = st.stop_id == "b"
    st.loc[mid, "departure_time"] = (gen.mins(st.departure_time[mid]) + 2).map(gen.hms)        # 2 min dwell at b
    late = st.stop_sequence == "3"
    for c in ("arrival_time", "departure_time"):
        st.loc[late, c] = (gen.mins(st.loc[late, c]) + 2).map(gen.hms)
    out, rep = gen.apply(f, [("modify_route", {"route_id": "R", "stopping_pattern": {"remove": ["b"]}})], None, ctx(), 0.0)
    s = out["bus"]["stop_times"]
    one = s[s.trip_id == "t0_0"]
    assert list(one.stop_id) == ["a", "c"] and list(one.arrival_time) == ["07:00:00", "07:10:00"]
    assert list(one.stop_sequence) == ["1", "2"] and rep[0]["stopping_pattern"]["minutes_saved_per_trip_median"] == 2
    with pytest.raises(gen.GeneratorError):
        gen.apply(f, [("modify_route", {"route_id": "R", "stopping_pattern": {"remove": ["a"]}})], None, ctx(), 0.0)


@pytest.fixture
def geo(tmp_path):
    # a straight east–west line: at this latitude 0.01° of longitude is about 695 m
    (tmp_path / "l.geojson").write_text(fc(
        line([[-2.60, 51.45], [-2.59, 51.45]], alignment_type="existing_rail", timing_points=["TA", "TB"]),
        line([[-2.59, 51.45], [-2.57, 51.45]], alignment_type="tunnel")))
    (tmp_path / "s.geojson").write_text(fc(point(-2.60, 51.45, stop_id="A", tiploc="TA"),
                                           point(-2.59, 51.45, stop_id="B", tiploc="TB"),
                                           point(-2.57, 51.45, stop_id="C")))
    (tmp_path / "ext.geojson").write_text(fc(line([[-2.58, 51.45], [-2.56, 51.45]], alignment_type="at_grade_segregated")))
    (tmp_path / "ext_s.geojson").write_text(fc(point(-2.58, 51.45, stop_id="c", existing=True),
                                               point(-2.57, 51.45, stop_id="d"), point(-2.56, 51.45, stop_id="e")))
    return tmp_path


LINE = {"route_id": "L1", "name": "New", "mode": "heavy_rail", "alignment": "l.geojson", "stops": "s.geojson",
        "speed_profile": {"max_kmh": 72, "accel_ms2": 1.0, "dwell_s": 30},
        "headways_min": {"AM": 30}, "span": "06:00-22:00"}


def test_add_line_uses_working_times_on_timed_track_and_the_profile_elsewhere(geo):
    runs = {("TA", "TB", None): (120.0, 9), ("TB", "TA", None): (150.0, 9)}
    out, rep = gen.apply(bus_feed(), [("add_line", LINE)], geo, ctx(runs), 0.0)
    r = rep[0]
    l1, l2 = r["legs"]
    assert l1["fwd_rule"].startswith("working times") and l1["fwd_s"] == 120 and l1["rev_s"] == 150
    assert l2["fwd_rule"] == "speed profile" and l2["types_m"].keys() == {"tunnel"}
    d2 = l2["dist_m"]
    assert l2["fwd_s"] == pytest.approx(d2 / 20 + 20, abs=0.1) and 1380 < d2 < 1400
    g = out["generated"]
    assert set(g["stops"].stop_id) == {"A", "B", "C"} and g["routes"].route_type.iloc[0] == "2"
    assert r["trips"] == 12 and r["periods_without_service"] == ["IP", "OP", "PM"]       # 6 each way in the AM
    first = g["stop_times"][g["stop_times"].trip_id == "gen:L1:0:0"]
    assert list(first.stop_id) == ["A", "B", "C"]
    assert list(first.arrival_time)[:2] == ["07:00:00", "07:02:00"] and first.departure_time.iloc[1] == "07:02:30"
    back = g["stop_times"][g["stop_times"].trip_id == "gen:L1:1:0"]
    assert list(back.stop_id) == ["C", "B", "A"]
    assert out["bus"]["trips"].equals(bus_feed()["bus"]["trips"])                           # parent untouched
    # too few trains → the profile, and no profile → an error
    few, rep2 = gen.apply(bus_feed(), [("add_line", LINE)], geo, ctx({("TA", "TB", None): (120.0, 2)}), 0.0)
    assert rep2[0]["legs"][0]["fwd_rule"] == "speed profile"
    with pytest.raises(gen.GeneratorError):
        gen.apply(bus_feed(), [("add_line", {k: v for k, v in LINE.items() if k != "speed_profile"})], geo, ctx(), 0.0)


def test_an_on_street_bus_line_needs_the_bus_speed_ratio(geo):
    (geo / "st.geojson").write_text(fc(line([[-2.60, 51.45], [-2.57, 51.45]], alignment_type="at_grade_street")))
    with pytest.raises(gen.GeneratorError, match="bus_speed_ratio"):
        gen.apply(bus_feed(), [("add_line", LINE | {"mode": "bus", "alignment": "st.geojson"})], geo, ctx(), 0.0)


def test_extend_to_runs_terminating_trips_on_and_starts_the_others_earlier(geo):
    f = bus_feed(60, start=420, end=540)
    ext = {"from_stop": "c", "alignment": "ext.geojson", "stops": "ext_s.geojson",
           "speed_profile": {"max_kmh": 72, "accel_ms2": 1.0, "dwell_s": 60}}
    out, rep = gen.apply(f, [("modify_route", {"route_id": "R", "extend_to": ext})], geo, ctx(), 0.0)
    r = rep[0]["extend_to"]
    assert r["new_stops"] == ["d", "e"] and r["trips_extended_at_end"] == 2 and r["trips_extended_at_start"] == 2
    leg = r["legs"][0]["fwd_s"] / 60
    s = out["bus"]["stop_times"]
    east = s[s.trip_id == "t0_0"]
    assert list(east.stop_id) == ["a", "b", "c", "d", "e"] and list(east.stop_sequence) == list("12345")
    assert east.departure_time.iloc[2] == "07:11:00"                                    # arrives 07:10, dwells 1 min
    assert gen.mins(east.arrival_time).iloc[3] == pytest.approx(431 + leg, abs=0.02)
    west = s[s.trip_id == "t1_0"]
    assert list(west.stop_id) == ["e", "d", "c", "b", "a"]
    assert west.departure_time.iloc[2] == "07:00:00" and west.arrival_time.iloc[2] == "06:59:00"   # as timetabled
    assert gen.mins(west.departure_time).iloc[0] == pytest.approx(419 - 2 * leg - 1, abs=0.03)
    assert {"d", "e"} <= set(out["bus"]["stops"].stop_id)


def test_replace_route_in_one_period_keeps_the_rest_and_links_the_new_route(geo):
    f = bus_feed(15)
    op = LINE | {"route_id": "R", "new_route_id": "R-new", "periods": ["AM"], "headways_min": {"AM": 10, "IP": 10}}
    del op["name"]
    out, rep = gen.apply(f, [("replace_route", op)], geo, ctx(), 0.0)
    t = out["bus"]["trips"].merge(out["bus"]["stop_times"][out["bus"]["stop_times"].stop_sequence == "1"])
    assert gen.mins(t.departure_time).min() == 600 and rep[0]["journeys_removed"] == 24
    g = out["generated"]
    assert g["routes"].route_desc.iloc[0] == "replaces R in AM" and rep[0]["trips"] == 36     # AM only, 18 each way


def test_working_times_take_only_trains_calling_at_both_ends():
    rows = []
    for k, run in enumerate([100, 110, 120, 130, 140]):
        rows += [dict(rid=f"r{k}", seq=0, tpl="A", kind="OR", arr_s=None, dep_s=1000, toc="XX", public=True),
                 dict(rid=f"r{k}", seq=1, tpl="J", kind="PP", arr_s=1040, dep_s=1040, toc="XX", public=False),
                 dict(rid=f"r{k}", seq=2, tpl="B", kind="IP", arr_s=1000 + run, dep_s=1060 + run, toc="XX", public=True),
                 dict(rid=f"r{k}", seq=3, tpl="C", kind="DT", arr_s=1500, dep_s=None, toc="XX", public=True)]
    rows += [dict(rid="fast", seq=0, tpl="A", kind="OR", arr_s=None, dep_s=1000, toc="XX", public=True),
             dict(rid="fast", seq=1, tpl="B", kind="PP", arr_s=1050, dep_s=1050, toc="XX", public=False),
             dict(rid="fast", seq=2, tpl="C", kind="DT", arr_s=1200, dep_s=None, toc="XX", public=True),
             dict(rid="ecs", seq=0, tpl="A", kind="OR", arr_s=None, dep_s=1000, toc="XX", public=True, exclusion="not_passenger"),
             dict(rid="ecs", seq=1, tpl="B", kind="DT", arr_s=1010, dep_s=None, toc="XX", public=True, exclusion="not_passenger")]
    tp = pd.DataFrame(rows).assign(journey_cancelled=False, cancelled=False)
    tp["exclusion"] = tp.get("exclusion")
    runs, dwell = gen.working_times(tp)
    assert runs[("A", "B", "XX")] == (120.0, 5)                 # the non-stop train and the empty stock do not count
    assert runs[("A", "C", None)] == (200.0, 1) and dwell["XX"] == 60
