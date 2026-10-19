"""The `works` op, `route_ids` and timing-point validation (plans/heavy-rail-programme-log.md G1, G4, G9)."""
import copy
import json

import pytest
import yaml

from lab import scenario as sc


def cat():
    return sc.Catalogue(routes={"R1": ["s1", "s2"], "R2": ["s2", "s3"], "R3": ["s1", "s3"]},
                        stops={"s1": (-2.60, 51.45), "s2": (-2.59, 51.45), "s3": (-2.58, 51.45)},
                        sections={("TPA", "TPB"), ("TPB", "TPC"), ("TPC", "TPD")},
                        extent=(-3.0, 51.0, -2.0, 52.0))


def fc(*feats):
    return json.dumps({"type": "FeatureCollection", "features": list(feats)})


def write(d, ops):
    (d / "B0.yaml").write_text(yaml.safe_dump({"id": "B0", "parent": None, "description": "root"}))
    p = d / "S001-t.yaml"
    p.write_text(yaml.safe_dump({"id": "S001-t", "parent": "B0", "description": "t", "ops": ops}))
    return p


def load(p):
    return sc.load(p, cat(), stop_tolerance_m=50, offset_count=3)


GOOD = {"works": {"name": "W", "track": [{"sections": ["TPA-TPB", "TPB-TPC"], "was": 1, "tracks": 2, "length_km": 3.2}],
                  "electrify": [{"sections": ["TPA-TPB"], "tracks": 2, "length_km": 1.5, "complexity": "complex"}],
                  "loops": [{"at": "TPB"}], "junctions": [{"at": "TPC", "type": "grade_separated"}],
                  "platforms": [{"at": "s3", "kind": "bay", "count": 2}]}}


def test_a_good_works_op_passes(tmp_path):
    s = load(write(tmp_path, [GOOD]))
    assert [k for k, _ in s.ops] == ["works"]


def broke(path, value, delete=False):
    ops = [copy.deepcopy(GOOD)]
    node = ops[0]["works"]
    for k in path[:-1]:
        node = node[k]
    if delete:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return ops


CASES = [
    ("works-section", broke(["track", 0, "sections"], ["TPA-TPZ"])),
    ("works-section", broke(["track", 0, "sections"], ["TPA-TPB", "TPB-TPA"])),          # a section and its reverse
    ("works-section", broke(["electrify", 0, "sections"], [])),
    ("works-tracks", broke(["track", 0, "tracks"], 1)),                                    # works add track
    ("works-tracks", broke(["track", 0, "was"], 0)),
    ("works-tracks", broke(["electrify", 0, "tracks"], 1.5)),
    ("works-length", broke(["track", 0, "length_km"], 0)),
    ("works-length", broke(["electrify", 0, "length_km"], None, delete=True)),
    ("works-complexity", broke(["electrify", 0, "complexity"], None, delete=True)),         # no silent default
    ("works-complexity", broke(["electrify", 0, "complexity"], "cheap")),
    ("works-point", broke(["loops", 0, "at"], "NOWHERE")),
    ("works-point", broke(["junctions", 0, "at"], "NOWHERE")),
    ("works-point", broke(["platforms", 0, "at"], "NOWHERE")),
    ("works-kind", broke(["junctions", 0, "type"], "flat")),
    ("works-kind", broke(["platforms", 0, "kind"], "through")),
    ("required-field", broke(["name"], None, delete=True)),
]


@pytest.mark.parametrize("rule,ops", CASES, ids=[f"{r}-{i}" for i, (r, _) in enumerate(CASES)])
def test_each_works_rule_rejects_a_broken_op(tmp_path, rule, ops):
    with pytest.raises(sc.ScenarioError) as e:
        load(write(tmp_path, ops))
    assert e.value.rule == rule


def test_empty_works_is_refused(tmp_path):
    with pytest.raises(sc.ScenarioError) as e:
        load(write(tmp_path, [{"works": {"name": "nothing"}}]))
    assert e.value.rule == "empty-op"


def test_unknown_keys_in_works_are_refused(tmp_path):
    ops = [copy.deepcopy(GOOD)]
    ops[0]["works"]["track"][0]["speed"] = 100
    with pytest.raises(sc.ScenarioError):
        load(write(tmp_path, ops))


def test_modify_route_over_route_ids_becomes_one_op_per_route(tmp_path):
    s = load(write(tmp_path, [{"modify_route": {"route_ids": ["R1", "R2"], "headways_min": {"AM": 30}}}]))
    assert [(k, o["route_id"]) for k, o in s.ops] == [("modify_route", "R1"), ("modify_route", "R2")]
    assert all("route_ids" not in o and o["headways_min"] == {"AM": 30} for _, o in s.ops)


@pytest.mark.parametrize("body", [
    {"route_ids": ["R1", "R1"], "headways_min": {"AM": 30}},                      # a route twice
    {"route_ids": [], "headways_min": {"AM": 30}},
    {"route_ids": ["R1", "NOPE"], "headways_min": {"AM": 30}},
    {"route_id": "R1", "route_ids": ["R2"], "headways_min": {"AM": 30}},          # both forms
    {"route_ids": ["R1", "R2"], "fill_span": "06:00-23:00"},                      # differs by route
])
def test_route_ids_rules(tmp_path, body):
    with pytest.raises(sc.ScenarioError):
        load(write(tmp_path, [{"modify_route": body}]))


def test_replace_route_over_several_routes_removes_them_all(tmp_path):
    for d in ("lines", "stops"):
        (tmp_path / d).mkdir()
    (tmp_path / "lines/n.geojson").write_text(fc({"type": "Feature", "geometry": {"type": "LineString",
        "coordinates": [[-2.60, 51.45], [-2.58, 51.45]]}, "properties": {"alignment_type": "existing_rail",
        "timing_points": ["TPA", "TPB"]}}))
    (tmp_path / "stops/n.geojson").write_text(fc(
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-2.60, 51.45]}, "properties": {"stop_id": "s1", "existing": True}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-2.58, 51.45]}, "properties": {"stop_id": "s3", "existing": True}}))
    op = {"replace_route": {"route_ids": ["R1", "R2"], "new_route_id": "RN", "mode": "heavy_rail",
                            "alignment": "lines/n.geojson", "stops": "stops/n.geojson",
                            "headways_min": {"AM": 30}, "span": "06:00-23:00"}}
    s = load(write(tmp_path, [op, {"modify_route": {"route_id": "R3", "headways_min": {"AM": 60}}}]))
    assert len(s.ops) == 2
    # R1 and R2 are gone for later ops
    with pytest.raises(sc.ScenarioError) as e:
        load(write(tmp_path, [op, {"modify_route": {"route_id": "R1", "headways_min": {"AM": 60}}}]))
    assert e.value.rule == "unknown-route"


def test_timing_points_on_a_tunnel_are_checked_like_existing_rail(tmp_path):
    (tmp_path / "lines").mkdir(); (tmp_path / "stops").mkdir()
    def feat(tp):
        return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-2.60, 51.45], [-2.58, 51.45]]},
                "properties": {"alignment_type": "tunnel", "timing_points": tp}}
    (tmp_path / "stops/n.geojson").write_text(fc(
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-2.60, 51.45]}, "properties": {"stop_id": "t1"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-2.58, 51.45]}, "properties": {"stop_id": "t2"}}))
    op = {"add_line": {"route_id": "T", "name": "T", "mode": "heavy_rail", "alignment": "lines/t.geojson",
                       "stops": "stops/n.geojson", "speed_profile": {"max_kmh": 80, "accel_ms2": 0.5, "dwell_s": 30},
                       "headways_min": {"AM": 30}, "span": "06:00-23:00"}}
    (tmp_path / "lines/t.geojson").write_text(fc(feat(["TPA", "TPB"])))
    load(write(tmp_path, [op]))                                   # a known section: fine
    (tmp_path / "lines/t.geojson").write_text(fc(feat(["TPA", "TPQ"])))
    with pytest.raises(sc.ScenarioError) as e:
        load(write(tmp_path, [op]))                               # a typo: now fails
    assert e.value.rule == "existing-rail-section"


@pytest.mark.parametrize("cut,rule", [
    ("s2", "truncate"),                                                              # must be a mapping
    ({"stop": "s2", "tiploc": "TPA"}, "required-field"),
    ({"stop": "s2", "tiploc": "TPA", "keep": "middle"}, "truncate"),
    ({"stop": "s2", "tiploc": "NOWHERE", "keep": "before"}, "truncate"),
    ({"stop": "s3", "tiploc": "TPA", "keep": "before"}, "stop-not-on-route"),        # s3 is not on R1
])
def test_truncate_at_rules(tmp_path, cut, rule):
    with pytest.raises(sc.ScenarioError) as e:
        load(write(tmp_path, [{"modify_route": {"route_id": "R1", "truncate_at": cut}}]))
    assert e.value.rule == rule


def test_a_good_truncate_at_passes(tmp_path):
    s = load(write(tmp_path, [{"modify_route": {"route_id": "R1", "truncate_at": {"stop": "s2", "tiploc": "TPA", "keep": "before"}}}]))
    assert s.ops[0][1]["truncate_at"]["keep"] == "before"
