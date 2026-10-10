"""network_version: the identity of a routable network (SPEC §4 as amended at P3)."""
from lab import network


def test_version_depends_on_every_input_and_on_the_slope_function():
    h = {"osm_clip": "a", "bus_gtfs": "b", "rail_gtfs": "c"}
    v = network.version(h)
    assert v.startswith("flat-") and v == network.version(dict(reversed(list(h.items()))))
    assert network.version(h | {"bus_gtfs": "x"}) != v
    t = network.version(h | {"dem_clip": "d"}, "TOBLER")
    assert t.startswith("tobler-") and t != network.version(h | {"dem_clip": "d"}, "MINETTI")
    assert t != network.version(h | {"dem_clip": "e"}, "TOBLER")


def test_skim_paths_follow_the_spec_layout(tmp_path):
    nw = {"skims": tmp_path / "B" / "v", "car_skims": tmp_path / "B" / "car"}
    assert network.skim(nw, "pt", "AM") == tmp_path / "B" / "v" / "pt" / "AM.parquet"
    assert network.skim(nw, "walk") == tmp_path / "B" / "v" / "walk" / "DAY.parquet"
    assert network.skim(nw, "car", "IP") == tmp_path / "B" / "car" / "IP.parquet"
