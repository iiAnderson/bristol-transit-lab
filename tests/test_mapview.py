import json

import geopandas as gpd
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


def test_oa_layer_skips_unchanged_areas():
    g = gpd.GeoDataFrame({"OA21CD": ["a", "b"], "residents": [10, 20], "x": [0, 5]},
                         geometry=[Point(-2.5, 51.4), Point(-2.6, 51.5)], crs=4326)
    fg = mapview._oa_layer(g, "x", "x", "jobs", True, True)
    assert len(fg._children) == 1


def test_panel_states_the_disclaimer_and_the_run():
    pe = {"mean": 5.0, "min": 4, "max": 6}
    c = {"scenario": "S", "against": "P", "modelled_date": "2026-09-23", "inherited": ["fail one"],
         "caveats": {"S": ["a caveat"]}, "coverage": {"difference": {"frequent_15": {"residents": 0}}},
         "people": {"AM": {"offsets": 3, "to_the_centre": {"residents_faster_by_threshold": pe,
                                                          "residents_slower_by_threshold": pe},
                           "to_jobs_weighted_mean_time": {"residents_faster_by_threshold": pe,
                                                          "residents_slower_by_threshold": pe}}},
         "cost": {}}
    html = mapview.panel(c, "run1", "abc", {"lines": 1, "of": 2, "commuters_shown": 1.0,
                                           "commuters_all": 2.0}, "gap1")
    assert "sketch-planning" in html and "run1" in html and "fail one" in html and "n/a" in html
