"""The cost engine (plans/P3.md P3c-2) on a small library with round numbers, and the
real library's own consistency."""
from pathlib import Path

import pandas as pd
import pytest
import yaml

from lab import costs

DOC = yaml.safe_load("""
_meta: {price_base_year: 2025}
indices:
  construction_infrastructure: {value: {2014: 100.0, 2020: 125.0, 2025: 150.0}, tag: SOURCED, source: x}
  gdp_deflator: {value: {2011: 80.0, 2014: 88.0, 2020: 100.0, 2025: 110.0}, tag: SOURCED, source: x}
  construction_real_growth_before_2014: {value: 0.02, tag: SOURCED, source: x}
optimism_bias:
  default_stage: {value: 1, tag: MODELLED, reasoning: x}
  rail: {value: [0.5, 0.3, 0.2], tag: SOURCED, source: x}
  fixed_links: {value: [0.6, 0.3, 0.2], tag: SOURCED, source: x}
  buildings: {value: [0.7, 0.4, 0.3], tag: SOURCED, source: x}
  rolling_stock: {value: [0.6, 0.4, 0.3], tag: SOURCED, source: x}
capex_per_route_km:
  tram:
    at_grade_street: {value: 10.0e+6, range: [8.0e+6, 20.0e+6], unit: GBP per route-km, price_year: 2020, ob_category: rail, tag: SOURCED, source: a tram scheme}
  heavy_rail:
    at_grade_segregated: {value: null, unit: GBP per route-km, tag: PLACEHOLDER}
tunnel:
  twin_bore_construction: {value: 20.0e+6, unit: GBP per route-km, price_year: 2011, ob_category: fixed_links, tag: SOURCED, source: g}
  spoil_disposal: {value: 3.0e+6, unit: GBP per route-km, price_year: 2011, ob_category: fixed_links, tag: SOURCED, source: g}
  systems: {value: 4.0e+6, unit: GBP per route-km, price_year: 2011, ob_category: fixed_links, tag: SOURCED, source: g}
  time_related_per_week: {value: 1.0e+6, unit: GBP per week, price_year: 2011, ob_category: fixed_links, tag: SOURCED, source: g}
  weeks_per_km: {value: 15.0, tag: SOURCED, source: g}
  fixed_per_drive: {value: 30.0e+6, unit: GBP, price_year: 2011, ob_category: fixed_links, tag: SOURCED, source: g}
  tbm_each: {value: 15.0e+6, unit: GBP, price_year: 2011, ob_category: fixed_links, tag: SOURCED, source: g}
  portal_each: {value: 20.0e+6, unit: GBP, price_year: 2011, ob_category: fixed_links, tag: SOURCED, source: g}
  vent_shaft_each: {value: 10.0e+6, unit: GBP, price_year: 2011, ob_category: fixed_links, tag: SOURCED, source: g}
  vent_shaft_spacing_km: {value: 3.0, tag: SOURCED, source: g}
  track_per_track_km: {value: null, unit: GBP per track-km, tag: PLACEHOLDER}
  electrification_per_track_km: {value: null, unit: GBP per track-km, tag: PLACEHOLDER}
stations:
  heavy_rail:
    surface: {value: 10.0e+6, unit: GBP, price_year: 2025, ob_category: buildings, tag: SOURCED, source: s}
    underground_mined: {value: 100.0e+6, range: [80.0e+6, 150.0e+6], unit: GBP, bound: upper, price_year: 2025, ob_category: buildings, tag: SOURCED, source: s}
    underground_box: {value: 50.0e+6, unit: GBP, price_year: 2025, ob_category: buildings, tag: SOURCED, source: s}
  tram:
    at_grade: {value: 0.0, unit: GBP, tag: MODELLED, reasoning: in the rate}
  default_underground: {value: underground_mined, tag: MODELLED, reasoning: the dearer}
depot:
  any: {value: 50.0e+6, unit: GBP, price_year: 2025, ob_category: buildings, tag: SOURCED, source: d}
fleet:
  tram_vehicle: {value: 4.0e+6, unit: GBP, price_year: 2025, ob_category: rolling_stock, tag: SOURCED, source: f}
  spare_ratio: {value: 0.1, tag: PLACEHOLDER}
  layover_min: {value: 5.0, tag: PLACEHOLDER}
opex:
  annualisation: {value: 300.0, tag: PLACEHOLDER}
  bus: {per_vehicle_km: {value: 3.0, unit: GBP per vehicle-km, price_year: 2020, index: gdp, tag: SOURCED, source: b}}
  tram: {per_vehicle_km: {value: null, unit: GBP per vehicle-km, tag: PLACEHOLDER}}
  heavy_rail:
    per_train_km: {value: 2.0, unit: GBP, price_year: 2025, index: gdp, tag: SOURCED, source: w}
    lease_per_unit_year: {value: 600000.0, unit: GBP, price_year: 2025, index: gdp, tag: SOURCED, source: w}
    staff_per_unit_year: {value: 500000.0, unit: GBP, price_year: 2025, index: gdp, tag: SOURCED, source: w}
    station_per_year: {value: 100000.0, unit: GBP, price_year: 2025, index: gdp, tag: SOURCED, source: w}
  tunnel_maintenance_per_km_year: {value: 1.0e+6, unit: GBP, price_year: 2025, index: gdp, tag: SOURCED, source: w}
  renewals_share_of_capex: {value: 0.3, tag: SOURCED, source: w}
""")
LIB = costs.Library(DOC)


def leg(a, b, **types):
    return {"from": a, "to": b, "types_m": {k: float(v) for k, v in types.items()}}


def test_rebasing_uses_the_construction_index_and_tags_rule_before_it_starts():
    assert LIB.factor(2020) == pytest.approx(150 / 125)
    assert LIB.factor(2020, "gdp") == pytest.approx(1.1)
    # 2011: GDP deflator to 2014 with 2% real growth a year, then the construction index
    assert LIB.factor(2011) == pytest.approx(88 / 80 * 1.02 ** 3 * 1.5)
    u = LIB.unit("capex_per_route_km.tram.at_grade_street")
    assert (u["low"], u["central"], u["high"]) == pytest.approx((9.6e+6, 12.0e+6, 24.0e+6))
    with pytest.raises(costs.CostError):
        LIB.factor(2012)                                     # no index value for that year
    assert LIB.optimism_bias("rail") == 0.5 and LIB.optimism_bias("rail", 3) == 0.2


def test_tunnel_runs_count_portals_by_where_the_tunnel_meets_the_surface():
    surface, tun = leg("a", "b", at_grade_street=1000), leg("b", "c", tunnel=2000)
    assert costs.tunnel_runs([surface, tun, leg("c", "d", tunnel=1500), surface]) == [{"km": 3.5, "portals": 2}]
    assert costs.tunnel_runs([surface, tun]) == [{"km": 2.0, "portals": 1}]              # underground terminus
    assert costs.tunnel_runs([tun]) == [{"km": 2.0, "portals": 0}]
    assert costs.tunnel_runs([leg("a", "b", tunnel=600, existing_rail=400)]) == [{"km": 0.6, "portals": 2}]
    assert costs.tunnel_runs([tun, surface, tun]) == [{"km": 2.0, "portals": 1}, {"km": 2.0, "portals": 1}]


def tram_service():
    rep = {"legs": [leg("a", "b", at_grade_street=2000), leg("b", "c", at_grade_street=3000)],
           "end_to_end_min": {"fwd": 20.0, "rev": 20.0}}
    stops = [{"stop_id": "a", "existing": True}, {"stop_id": "b"}, {"stop_id": "c"}]
    q = costs.service_quantities(rep, "tram", stops, {"AM": 10, "IP": 15})
    return q | {"fleet": costs.fleet(q, LIB), "name": "T"}


def test_a_tram_line_costs_its_way_fleet_and_depot_with_optimism_bias_part_by_part():
    q = tram_service()
    assert q["km_by_type"] == {"at_grade_street": 5.0} and [s["stop_id"] for s in q["new_stops"]] == ["b", "c"]
    assert q["fleet"]["in_service"] == 5                                              # (20 + 20 + 10) / 10
    assert costs.with_spares([q, q], LIB) == {"tram": {"in_service": 10, "with_spares": 11}}   # spares once, over the mode
    lines, notes = costs.capex([q], LIB)
    by = {x["item"]: x for x in lines}
    way = by["T: new at grade street way"]
    assert way["cost"] == pytest.approx(60e6) and way["cost_with_ob"] == pytest.approx(90e6) and way["high"] == pytest.approx(120e6)
    assert by["tram: 6 vehicles (5 in service)"]["cost_with_ob"] == pytest.approx(24e6 * 1.6)
    assert by["depot (tram)"]["cost_with_ob"] == pytest.approx(50e6 * 1.7)
    assert by["T: 2 new stations (at grade)"]["cost"] == 0
    s = costs.summarise(lines, [], notes, LIB)
    assert s["capex"]["raw"] == {"low": 48e6 + 74e6, "central": 134e6, "high": 120e6 + 74e6}
    assert s["capex"]["with_optimism_bias"]["central"] == round(90e6 + 38.4e+6 + 85e6) and s["complete"]
    assert s["capex"]["renewals_over_60_years_central"] == round(134e6 * 0.3)
    assert "fleet.spare_ratio" in s["unit_costs_used_by_tag"]["PLACEHOLDER"]
    # a later stage takes a smaller uplift
    assert costs.capex([q], LIB, 3)[0][0]["cost_with_ob"] == pytest.approx(72e6)


def test_a_rail_tunnel_is_built_up_and_what_has_no_unit_cost_is_listed_not_guessed():
    rep = {"legs": [leg("x", "y", existing_rail=1000), leg("y", "u1", tunnel=3000), leg("u1", "u2", tunnel=3000)],
           "end_to_end_min": {"fwd": 10.0, "rev": 10.0}}
    stops = [{"stop_id": "x", "existing": True}, {"stop_id": "y", "existing": True}, {"stop_id": "u1", "station_type": "underground_box"},
             {"stop_id": "u2"}]
    q = costs.service_quantities(rep, "heavy_rail", stops, {"AM": 15})
    q |= {"fleet": costs.fleet(q, LIB), "name": "R"}
    assert q["tunnel_runs"] == [{"km": 6.0, "portals": 1}] and q["fleet"]["in_service"] == 2
    lines, notes = costs.capex([q], LIB)
    by = {x["item"]: x for x in lines}
    f = LIB.factor(2011)
    assert by["R: tunnel 1: twin bore construction"]["cost"] == pytest.approx(120e6 * f)
    assert by["R: tunnel 1: time related per week"]["quantity"] == 90 and by["R: tunnel 1: vent shaft each"]["quantity"] == 2
    assert by["R: tunnel 1: portal each"]["quantity"] == 1 and by["R: tunnel 1: tbm each"]["cost"] == pytest.approx(30e6 * f)
    assert by["R: 1 new stations (underground box)"]["cost"] == 50e6
    mined = by["R: 1 new stations (underground mined)"]                     # an upper bound only: the top of its range
    assert mined["upper_bound_only"] and mined["cost"] is None and mined["low"] is None and mined["high"] == 150e6
    assert by["R: tunnel 1: track"]["cost"] is None and by["R: tunnel 1: track"]["quantity"] == 12.0
    assert by["R: tunnel 1: electrification"]["cost"] is None
    assert any("existing railway" in n for n in notes) and any("leased" in n for n in notes)
    ol, on = costs.opex(pd.Series({"2": 1000.0, "3": 500.0}), pd.Series({"2": 1100.0, "3": 400.0}), [q], LIB)
    ob = {x["item"].split(" (")[0]: x for x in ol}
    assert ob["heavy_rail: change in vehicle-km"]["cost"] == pytest.approx(100 * 300 * 2.0)
    assert ob["bus: change in vehicle-km"]["cost"] == pytest.approx(-100 * 300 * 3.0 * 1.1)      # fewer bus km: a saving
    assert ob["heavy rail: units leased"]["cost"] == 1.8e+6 and ob["heavy rail: train crew"]["cost"] == 1.0e+6
    assert ob["heavy rail: new stations"]["cost"] == 200000 and ob["tunnel maintenance"]["cost"] == 6e6
    s = costs.summarise(lines, ol, notes + on, LIB)
    assert not s["complete"] and len(s["not_costed"]) == 2 and "PLACEHOLDER" in s["not_costed"][0]
    assert s["capex"]["raw"]["central"] is None and s["capex"]["with_optimism_bias"]["central"] is None
    assert s["capex"]["raw"]["high"] > s["capex"]["raw"]["low"] and any("upper bound only" in n for n in s["notes"])
    assert s["opex_per_year"]["central"] == round(60000 - 99000 + 1.8e+6 + 1.0e+6 + 200000 + 6e6)


def test_an_extension_needs_at_most_the_extra_round_trip_over_the_headway():
    rep = {"legs": [leg("f", "n", existing_rail=2300)], "extension_min": {"out": 7.4, "back": 7.4}}
    q = costs.service_quantities(rep, "heavy_rail", [{"stop_id": "f", "existing": True}, {"stop_id": "n"}], {"0": 60})
    assert costs.fleet(q, LIB)["in_service"] == 1 and q["is_extension"]
    assert costs.fleet(costs.service_quantities(rep, "heavy_rail", [], None), LIB)["in_service"] is None


def test_feed_km_counts_each_journey_once():
    stops = pd.DataFrame({"stop_id": ["a", "b"], "stop_lat": ["51.0", "51.0"], "stop_lon": ["0.0", "0.1"]})
    trips = pd.DataFrame({"route_id": ["R"] * 3, "trip_id": ["t1#0", "t1#1", "t2"], "original_trip_id": ["t1", "t1", "t2"]})
    st = pd.DataFrame([{"trip_id": t, "stop_id": s, "stop_sequence": str(i + 1), "departure_time": f"08:0{i}:00"}
                       for t in trips.trip_id for i, s in enumerate("ab")])
    km = costs.feed_km({"bus": {"stops": stops, "trips": trips, "stop_times": st,
                                "routes": pd.DataFrame({"route_id": ["R"], "route_type": ["3"]})}})
    assert km["3"] == pytest.approx(2 * 6.998, abs=0.02)          # two journeys of 0.1° of longitude at 51°N


def test_the_real_library_loads_and_rebases_every_value():
    lib = costs.Library.load(Path(__file__).parents[1] / "params" / "costs.yaml")
    assert lib.base == 2025
    assert lib.factor(2011) == pytest.approx(1.622, abs=0.002)    # HS2's 2Q2011 prices to 2025
    n = 0
    def walk(node, path):
        nonlocal n
        if isinstance(node, dict) and "value" in node:
            if str(node.get("unit", "")).startswith("GBP"):
                u = lib.unit(path)
                assert node["value"] is None or u["low"] <= u["central"] <= u["high"], path
                assert node["value"] is None or node["tag"] == "PLACEHOLDER" or node["value"] == 0 or "price_year" in node, path
                n += 1
        elif isinstance(node, dict):
            for k, v in node.items():
                if not k.startswith("_"):
                    walk(v, f"{path}.{k}" if path else k)
    walk(lib.doc, "")
    assert n > 30
    assert lib.unit("capex_per_route_km.tram.at_grade_street")["central"] == pytest.approx(42.1e+6, rel=0.01)
    assert lib.unit("opex.bus.per_vehicle_km")["central"] == pytest.approx(3.29, abs=0.01)
