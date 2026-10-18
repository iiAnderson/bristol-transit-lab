import json

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from lab import mapview


def test_colour_is_grey_at_zero_and_blue_for_gain_red_for_loss():
    assert mapview._colour(0, 10) == mapview.NEUTRAL
    assert mapview._colour(10, 10) == mapview.GAIN
    assert mapview._colour(-10, 10) == mapview.LOSS
    # for a measure where smaller is better, the sign flips
    assert mapview._colour(-10, 10, good_is_positive=False) == mapview.GAIN


def test_scheme_files_stop_at_the_comparison_scenario(tmp_path):
    (tmp_path / "lines").mkdir()
    for n in ("A", "B"):
        (tmp_path / "lines" / f"{n}.geojson").write_text("{}")
    (tmp_path / "P.yaml").write_text("id: P\nparent: null\nops: []\n")
    (tmp_path / "Q.yaml").write_text("id: Q\nparent: P\nops:\n- add_line: {alignment: lines/A.geojson}\n")
    (tmp_path / "R.yaml").write_text(
        "id: R\nparent: Q\nops:\n- modify_route: {extend_to: {alignment: lines/B.geojson}}\n")
    got = [p.name for p in mapview.scheme_files(tmp_path, "R", "Q")]
    assert got == ["B.geojson"]                      # Q's own line belongs to the parent
    assert sorted(p.name for p in mapview.scheme_files(tmp_path, "R", "P")) == ["A.geojson", "B.geojson"]


def test_scheme_routes_read_the_scenarios_own_ops(tmp_path):
    (tmp_path / "P.yaml").write_text("id: P\nparent: null\nops:\n- add_line: {route_id: OLD}\n")
    (tmp_path / "Q.yaml").write_text(
        "id: Q\nparent: P\nops:\n- add_line: {route_id: NEW}\n- modify_route: {route_id: 'GW:X'}\n")
    assert mapview.scheme_routes(tmp_path, "Q", "P") == ["NEW", "GW:X"]


def test_saved_bands_count_residents_and_the_unchanged():
    g = gpd.GeoDataFrame({"residents": [100, 200, 300, 400],
                          "jobs_time_change_min_AM": [-0.4, -1.5, -3.0, 2.0]},
                         geometry=[Point(0, 0)] * 4, crs=4326)
    bands, unchanged, slower = mapview.saved_bands(g)
    assert dict(bands) == {"1–2 min": 200, "2–5 min": 300, "5–10 min": 0, "over 10 min": 0}
    assert unchanged == 100 and slower == 400


def _project(tmp_path, with_f15=False):
    pe = {"mean": 5.0, "min": 4, "max": 6}
    c = {"scenario": "S", "against": "P", "modelled_date": "2026-09-23", "inherited": ["fail one"],
         "caveats": {"S": ["a caveat"]}, "not_yet_modelled": {"S": []}, "threshold_min": 2.0,
         "coverage": {"difference": {"frequent_15": {"residents": 0}}},
         "people": {"AM": {"offsets": 3, "to_the_centre": {"residents_faster_by_threshold": pe,
                                                          "residents_slower_by_threshold": pe},
                           "to_jobs_weighted_mean_time": {"residents_faster_by_threshold": pe,
                                                          "residents_slower_by_threshold": pe}}},
         "cost": {}}
    g = gpd.GeoDataFrame({"OA21CD": ["a", "b"], "residents": [100, 200],
                          "jobs_time_change_min_AM": [-3.0, 0.0], "centre_change_min_AM": [-2.0, 0.0],
                          "pt_jobs_45_AM_change": [500, 0], "frequent_15_change": [1 if with_f15 else 0, 0]},
                         geometry=[Point(-2.5, 51.4), Point(-2.6, 51.5)], crs=4326)
    attr = pd.DataFrame({"OA21CD": ["a", "b"], "jobs_faster": [1000, 0], "jobs_faster_via_scheme": [900, 0],
                         "jobs_new_within": [300, 0]})
    reach = pd.DataFrame({"oa": ["a"], "zone": ["Z1"], "t0": [50.0], "t1": [44.0]})
    return mapview.Project("S", "A readable title", "the baseline", "#123456", tmp_path / "run1", c, g, attr,
                           reach, [], "abc")


def test_card_names_the_project_states_the_run_and_the_caveats(tmp_path):
    html = mapview.card(_project(tmp_path))
    assert "A readable title" in html and "run1" in html and "fail one" in html and "a caveat" in html
    assert "n/a" in html                              # no cost: said, not hidden


def test_rows_keep_only_areas_a_layer_draws_and_link_to_attribution(tmp_path):
    rows, scales = mapview.project_rows(_project(tmp_path))
    assert [r[0] for r in rows] == ["a"]
    assert rows[0][4] == 3.0 and rows[0][5:8] == [1000, 900, 300]
    assert scales["saved"] == 3.0


def test_reach_is_keyed_by_area_and_only_for_known_zones(tmp_path):
    p = _project(tmp_path)
    rows, _ = mapview.project_rows(p)
    reach, oas = mapview.project_reach(p, {"Z1": 7}, rows)
    assert reach == {"a": [[7, 50.0, 44.0]]} and oas[0][0] == "a"
    assert mapview.project_reach(p, {}, rows)[0] == {}


def test_sidebar_lists_every_project_by_its_title(tmp_path):
    p = _project(tmp_path)
    side = mapview.sidebar([p], {"lines": 1, "of": 2, "commuters_shown": 1.0, "commuters_all": 2.0}, False)
    assert "A readable title" in side and 'data-project="S"' in side and 'value="f15"' not in side
