"""The comparison report's layout (plans/P3.md P3c-4): it must show losers beside
winners, say what is not costed, and list the placeholders."""
from lab import compare as cmp_
from lab.params import Param


def test_params_behind_lists_tagged_values_in_the_namespaces_a_comparison_reads():
    ps = [Param("routing.walk_speed_kmh", 4.8, "MODELLED", None), Param("commute.x", 1, "PLACEHOLDER", None),
          Param("connectivity.decay_beta_per_min", 0.04, "PLACEHOLDER", None), Param("skims.window", 60, "SOURCED", None)]
    out = cmp_.params_behind(ps, {"PLACEHOLDER": ["fleet.spare_ratio"], "SOURCED": ["tunnel.systems"]})
    assert out == {"MODELLED": ["base.yaml: routing.walk_speed_kmh"],
                   "PLACEHOLDER": ["base.yaml: connectivity.decay_beta_per_min", "costs.yaml: fleet.spare_ratio"]}


def test_the_report_shows_losers_ranges_gaps_in_the_cost_and_placeholders():
    side = {"jobs_45min": {"resident_weighted_mean": 1000.0, "median_core_oa": 900.0}}
    ppl = {"centre_zone": "Z", "to_the_centre": {"residents_faster_by_threshold": 500, "residents_slower_by_threshold": 40},
           "to_jobs_weighted_mean_time": {"residents_faster_by_threshold": 300, "residents_slower_by_threshold": 20},
           "range_across_offsets": {"to_the_centre": {"residents_faster_by_threshold": {"min": 450, "max": 620},
                                                      "residents_slower_by_threshold": {"min": 10, "max": 90}}}}
    cost = {"against": "B", "price_base_year": 2025, "optimism_bias_stage": 1, "complete": False,
            "capex": {"raw": {"low": 1e6, "central": 2e6, "high": 3e6}, "with_optimism_bias": {"low": 1.5e6, "central": 3e6, "high": 4.5e6}},
            "opex_per_year": {"low": 1e5, "central": 2e5, "high": 3e5}, "not_costed": ["track in tunnel: no unit cost"],
            "notes": ["a note"], "caveat": "Sketch costs."}
    c = {"scenario": "S1", "against": "B", "modelled_date": "2026-09-23", "walk_speed_kmh": 4.8, "gradient": "TOBLER",
         "threshold_min": 2.0, "decay_beta": 0.04, "car_period": "AMPH", "caveats": {"S1": ["B: a caveat from the parent"], "B": []},
         "people": {"AM": ppl},
         "connectivity": {"AM": {"scenario": side, "against": side, "difference": {"jobs_45min": {"resident_weighted_mean": 0.0}}}},
         "coverage": {"scenario": {"frequent_15": {"residents": 10}, "residents_in_no_class": 5},
                      "against": {"frequent_15": {"residents": 8}, "residents_in_no_class": 6}},
         "cost": {"S1": cost, "B": None}, "cost_note": None, "inherited": ["P2 fail: car times."],
         "not_yet_modelled": {"S1": ["op 2 (fare_change): recorded, not yet modelled"], "B": []},
         "params_behind": {"MODELLED": ["a"], "PLACEHOLDER": ["costs.yaml: fleet.spare_ratio"]},
         "skims": {"scenario": "x", "against": "y"}}
    md = cmp_.markdown(c)
    assert "| AM | the centre (Z) | 500 | 40 | 450 to 620 | 10 to 90 |" in md
    assert "| AM | jobs (weighted mean time) | 300 | 20 | one network | one network |" in md
    assert "> **B:** a caveat from the parent" in md
    assert "| frequent 15 min, residents | 10 | 8 | +2 |" in md and "| residents in no class | 5 | 6 | -1 |" in md
    assert "**incomplete:** 1 items have no unit cost" in md and "track in tunnel" in md
    assert "£1.0m – £2.0m – £3.0m" in md and "£100k – £200k – £300k" in md
    assert "- costs.yaml: fleet.spare_ratio" in md and "P2 fail: car times." in md and "recorded, not yet modelled" in md
