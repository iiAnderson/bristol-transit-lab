"""Scenario schema and validator (plans/P3.md P3b-1): one good scenario that uses every
op, and one deliberately broken scenario per rule."""
import copy
import json

import pytest
import yaml

from lab import scenario as sc

EXTENT = (-3.0, 51.0, -2.0, 52.0)


def cat():
    return sc.Catalogue(routes={"R1": ["s1", "s2", "s3"], "R2": ["s2", "s4"]},
                        stops={"s1": (-2.60, 51.45), "s2": (-2.59, 51.45), "s3": (-2.58, 51.45),
                               "s4": (-2.59, 51.46)},
                        sections={("TPA", "TPB"), ("TPB", "TPC")}, extent=EXTENT)


def line(coords, **props):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props}


def point(lon, lat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def fc(*feats):
    return json.dumps({"type": "FeatureCollection", "features": list(feats)})


GOOD_OPS = [
    {"add_line": {"route_id": "L1", "name": "New line", "mode": "light_rail", "alignment": "lines/l1.geojson",
                  "stops": "stops/l1.geojson", "speed_profile": {"max_kmh": 70, "accel_ms2": 1.0, "dwell_s": 30},
                  "headways_min": {"AM": 7.5, "IP": 10}, "span": "05:30-00:00",
                  "vehicle": {"capacity": 250}, "fare": {"type": "flat", "gbp": 2.0}}},
    {"modify_route": {"route_id": "R1", "headways_min": {"IP": 20},
                      "stopping_pattern": {"remove": ["s3"], "add": ["s4"]}, "truncate_at": "s2"}},
    {"modify_route": {"route_id": "R2", "extend_to": {"from_stop": "s4", "alignment": "lines/ext.geojson",
                                                      "stops": "stops/ext.geojson"}}},
    {"reroute": {"route_id": "R1", "from_stop": "s1", "to_stop": "s2", "alignment": "lines/rr.geojson",
                 "speed_profile": {"max_kmh": 80, "accel_ms2": 0.8, "dwell_s": 45}}},
    {"replace_route": {"route_id": "R2", "new_route_id": "R2x", "mode": "heavy_rail",
                       "alignment": "lines/ext.geojson", "stops": "stops/ext2.geojson",
                       "headways_min": {"AM": 30}, "span": "06:00-23:00", "periods": ["AM"]}},
    {"add_stop": {"route_id": "R1", "after_stop": "s1", "stop": {"stop_id": "n9", "name": "New", "lon": -2.595, "lat": 51.45}}},
    {"road_speed_factor": {"links": "lines/rr.geojson", "applies_to": ["bus"], "factor": 1.25}},
    {"fare_change": {"modes": ["bus"], "type": "flat", "gbp": 2.0}},
    {"landuse_delta": {"file": "deltas/site.yaml"}},
    {"remove_route": {"route_id": "L1"}},
]


@pytest.fixture
def dirs(tmp_path):
    for d in ("lines", "stops", "deltas"):
        (tmp_path / d).mkdir()
    (tmp_path / "lines/l1.geojson").write_text(fc(
        line([[-2.60, 51.44], [-2.59, 51.44]], alignment_type="at_grade_street"),
        line([[-2.59, 51.44], [-2.58, 51.44]], alignment_type="tunnel")))
    (tmp_path / "stops/l1.geojson").write_text(fc(point(-2.60, 51.44, stop_id="a"), point(-2.58, 51.44, stop_id="b")))
    (tmp_path / "lines/ext.geojson").write_text(fc(
        line([[-2.59, 51.46], [-2.59, 51.47]], alignment_type="existing_rail", timing_points=["TPA", "TPB", "TPC"])))
    (tmp_path / "stops/ext.geojson").write_text(fc(point(-2.59, 51.46, stop_id="s4", existing=True),
                                                    point(-2.59, 51.47, stop_id="e1")))
    (tmp_path / "stops/ext2.geojson").write_text(fc(point(-2.59, 51.46, stop_id="s4", existing=True),
                                                     point(-2.59, 51.47, stop_id="e1", existing=True)))
    (tmp_path / "lines/rr.geojson").write_text(fc(line([[-2.60, 51.45], [-2.595, 51.452], [-2.59, 51.45]],
                                                       alignment_type="tunnel")))
    (tmp_path / "deltas/site.yaml").write_text("residents: 100\n")
    (tmp_path / "B0.yaml").write_text(yaml.safe_dump({"id": "B0", "parent": None, "description": "root"}))
    return tmp_path


def write(d, ops=None, **top):
    doc = {"id": "S001-test", "parent": "B0", "description": "test", "ops": GOOD_OPS if ops is None else ops} | top
    p = d / f"{doc['id'] if isinstance(doc.get('id'), str) else 'S001-test'}.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def load(p):
    return sc.load(p, cat(), stop_tolerance_m=50, offset_count=3)


def test_good_scenario_passes_and_reports_what_is_not_modelled(dirs):
    s = load(write(dirs))
    assert [k for k, _ in s.ops] == [next(iter(o)) for o in GOOD_OPS]
    assert s.offsets == [0, 1 / 3, 2 / 3]
    assert len(s.not_yet_modelled) == 1 and "fare_change" in s.not_yet_modelled[0]
    assert len(s.files) == 7 and len(s.spec_hash) == 64
    assert load(dirs / "B0.yaml").parent is None


def test_spec_hash_ignores_formatting_but_not_content_or_referenced_files(dirs):
    p = write(dirs)
    h = load(p).spec_hash
    p.write_text("# a comment\n" + p.read_text())
    assert load(p).spec_hash == h
    (dirs / "stops/l1.geojson").write_text(fc(point(-2.60, 51.44, stop_id="a"), point(-2.5801, 51.44, stop_id="b")))
    h2 = load(p).spec_hash
    assert h2 != h
    doc = yaml.safe_load(p.read_text())
    doc["ops"][0]["add_line"]["headways_min"]["AM"] = 6
    p.write_text(yaml.safe_dump(doc))
    assert load(p).spec_hash != h2


def broken(i, path, value, delete=False):
    """GOOD_OPS with op ``i`` changed at ``path``."""
    ops = copy.deepcopy(GOOD_OPS)
    node = ops[i][next(iter(ops[i]))]
    for k in path[:-1]:
        node = node[k]
    if delete:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return ops


CASES = [
    ("unknown-route", lambda d: write(d, broken(1, ["route_id"], "nope"))),
    ("unknown-route", lambda d: write(d, GOOD_OPS + [{"modify_route": {"route_id": "L1", "headways_min": {"AM": 5}}}])),
    ("duplicate-route", lambda d: write(d, broken(0, ["route_id"], "R1"))),
    ("headway", lambda d: write(d, broken(0, ["headways_min", "AM"], 0))),
    ("headway", lambda d: write(d, broken(0, ["headways_min"], {"XX": 10}))),
    ("span", lambda d: write(d, broken(0, ["span"], "5am-midnight"))),
    ("mode", lambda d: write(d, broken(0, ["mode"], "monorail"))),
    ("mode", lambda d: write(d, broken(6, ["applies_to"], ["car"]))),
    ("required-field", lambda d: write(d, broken(0, ["speed_profile"], None, delete=True))),
    ("speed-profile", lambda d: write(d, broken(0, ["speed_profile", "accel_ms2"], -1))),
    ("vehicle", lambda d: write(d, broken(0, ["vehicle", "capacity"], 0))),
    ("fare", lambda d: write(d, broken(7, ["gbp"], -1))),
    ("file-missing", lambda d: write(d, broken(0, ["alignment"], "lines/none.geojson"))),
    ("file-missing", lambda d: write(d, broken(8, ["file"], "deltas/none.yaml"))),
    ("stop-not-on-route", lambda d: write(d, broken(1, ["truncate_at"], "s3"))),       # removed by the same op
    ("stop-not-on-route", lambda d: write(d, broken(3, ["to_stop"], "s3"))),      # removed by op 2
    ("stop-id", lambda d: write(d, broken(1, ["stopping_pattern", "add"], ["zz"]))),
    ("stop-id", lambda d: write(d, broken(5, ["stop", "stop_id"], "s1"))),
    ("empty-op", lambda d: write(d, [{"modify_route": {"route_id": "R1"}}])),
    ("factor", lambda d: write(d, broken(6, ["factor"], 0))),
    ("unknown-key", lambda d: write(d, broken(0, ["colour"], "red"))),
    ("unknown-key", lambda d: write(d, surprise=1)),
    ("op", lambda d: write(d, [{"teleport": {}}])),
    ("op", lambda d: write(d, [{"remove_route": {"route_id": "R1"}, "add_stop": {}}])),
    ("parent", lambda d: write(d, parent="B9")),
    ("id", lambda d: write(d, id="bad id")),
    ("offsets", lambda d: write(d, offsets=[0, 1.2])),
    ("offsets", lambda d: write(d, offsets=[0.5, 0.5])),
]


@pytest.mark.parametrize("rule,make", CASES, ids=[f"{r}-{i}" for i, (r, _) in enumerate(CASES)])
def test_each_rule_rejects_a_broken_scenario(dirs, rule, make):
    with pytest.raises(sc.ScenarioError) as e:
        load(make(dirs))
    assert e.value.rule == rule, str(e.value)


def test_geometry_rules(dirs):
    def expect(rule):
        with pytest.raises(sc.ScenarioError) as e:
            load(write(dirs))
        assert e.value.rule == rule, str(e.value)
    good = {f: (dirs / f).read_text() for f in ("lines/l1.geojson", "stops/l1.geojson", "lines/ext.geojson",
                                                "lines/rr.geojson")}

    def reset():
        for f, t in good.items():
            (dirs / f).write_text(t)

    (dirs / "stops/l1.geojson").write_text(fc(point(-2.60, 51.44, stop_id="a"), point(-2.58, 51.4410, stop_id="b")))
    expect("stop-off-alignment")                                   # 111 m off the line
    reset()
    (dirs / "lines/l1.geojson").write_text(fc(line([[-2.60, 51.44], [-2.58, 51.44]], alignment_type="viaduct")))
    expect("alignment-type")
    reset()
    (dirs / "lines/l1.geojson").write_text(fc(line([[-2.60, 51.44], [-1.90, 51.44]], alignment_type="tunnel")))
    expect("outside-extent")
    reset()
    (dirs / "lines/ext.geojson").write_text(fc(line([[-2.59, 51.46], [-2.59, 51.47]], alignment_type="existing_rail",
                                                    timing_points=["TPA", "TPZ"])))
    expect("existing-rail-section")
    reset()
    (dirs / "lines/rr.geojson").write_text(fc(line([[-2.60, 51.45], [-2.585, 51.45]], alignment_type="tunnel")))
    expect("reroute-ends")                                          # stops 350 m short of s2
    reset()
    (dirs / "stops/l1.geojson").write_text(fc(point(-2.60, 51.44, stop_id="s1"), point(-2.58, 51.44, stop_id="b")))
    expect("stop-id")                                               # reuses an existing id without saying so
    reset()
    load(write(dirs))


def test_a_root_baseline_has_no_ops(dirs):
    (dirs / "B1.yaml").write_text(yaml.safe_dump({"id": "B1", "parent": None, "description": "x",
                                                  "ops": [{"remove_route": {"route_id": "R1"}}]}))
    with pytest.raises(sc.ScenarioError) as e:
        load(dirs / "B1.yaml")
    assert e.value.rule == "parent"
