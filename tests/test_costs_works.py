"""Costs of `works` items (plans/heavy-rail-programme-log.md G1)."""
from pathlib import Path

import pytest

from lab import costs

LIB = Path(__file__).resolve().parents[1] / "params" / "costs.yaml"

W = {"name": "W", "electrify": [{"sections": ["A-B", "B-C"], "tracks": 2, "length_km": 3.0, "complexity": "complex"}],
     "track": [{"sections": ["A-B"], "was": 1, "tracks": 2, "length_km": 2.0}],
     "loops": [{"at": "X"}], "junctions": [{"at": "Y", "type": "grade_separated"}],
     "platforms": [{"at": "s", "kind": "bay", "count": 2}]}


@pytest.fixture(scope="module")
def lib():
    return costs.Library.load(LIB)


def test_electrification_is_priced_per_single_track_km_at_the_stated_complexity(lib):
    lines, _ = costs.works_capex([W], lib)
    e = next(x for x in lines if "electrify" in x["item"])
    assert e["quantity"] == 6.0 and e["unit_cost_path"] == "rail_works.electrification_complex"
    assert e["tag"] == "SOURCED" and e["cost"] == pytest.approx(6.0 * lib.unit("rail_works.electrification_complex")["central"])
    assert e["low"] < e["cost"] < e["high"] and e["cost_with_ob"] > e["cost"]


def test_items_without_a_unit_cost_are_lines_with_no_cost_not_zeros(lib):
    lines, notes = costs.works_capex([W], lib)
    rest = [x for x in lines if "electrify" not in x["item"]]
    assert len(rest) == 4 and all(x["cost"] is None and x["tag"] == "PLACEHOLDER" for x in rest)
    assert any("signalling" in n.lower() for n in notes)
    q = {x["unit_cost_path"]: x["quantity"] for x in rest}
    assert q["rail_works.track_doubling"] == 2.0                      # one track added over 2 km
    assert q["rail_works.passing_loop"] == 1 and q["rail_works.grade_separated_junction"] == 1
    assert q["stations.heavy_rail.bay_platform"] == 2


def test_no_works_no_lines_no_note(lib):
    assert costs.works_capex([], lib) == ([], [])


def test_the_result_is_marked_incomplete_while_a_rate_is_a_placeholder(lib):
    lines, notes = costs.works_capex([W], lib)
    out = costs.summarise(lines, [], notes, lib, None)
    assert out["complete"] is False and any("track_doubling" in n for n in out["not_costed"])


def test_only_diesel_units_are_leased_at_the_sourced_rate(lib):
    import pandas as pd
    svc = lambda t, n: {"mode": "heavy_rail", "traction": t, "km_by_type": {}, "tunnel_runs": [], "new_stops": [],
                        "fleet": {"in_service": n}, "name": t}
    lines, _ = costs.opex(pd.Series(dtype=float), pd.Series(dtype=float), [svc("diesel", 3), svc("electric", 5), svc("bi_mode", 1)], lib)
    by = {x["item"]: x for x in lines}
    assert by["heavy rail: units leased"]["quantity"] == 4 and by["heavy rail: units leased"]["cost"] > 0     # ceil(3 × 1.1)
    other = by["heavy rail: electric and bi-mode units leased"]
    assert other["quantity"] == 7 and other["cost"] is None and other["tag"] == "PLACEHOLDER"      # ceil(6 × 1.1)
    assert by["heavy rail: train crew"]["quantity"] == 9                                             # every unit in service has a crew
