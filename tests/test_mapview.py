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


def test_dots_draws_one_marker_per_row():
    g = gpd.GeoDataFrame({"OA21CD": ["a", "b"], "residents": [10, 20]},
                         geometry=[Point(-2.5, 51.4), Point(-2.6, 51.5)], crs=4326)
    fg = mapview._dots(g, "x", True, pd.Series([0.2, 1.0], index=g.index), lambda r: "#000000",
                       lambda r: r.OA21CD)
    assert len(fg._children) == 2


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
