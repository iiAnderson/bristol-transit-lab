"""The P3b round-trip test on the real baseline feed (plans/P3.md P3b-6): a bus route with
restriction copies goes from its timetabled headway to another and back, and its stop times
come back exactly, every other route untouched. Skipped where the feed is not built."""
from pathlib import Path

import pytest
import yaml

from lab import generator as gen
from lab.config import LabConfig

CFG = LabConfig.load()
RAW = yaml.safe_load((CFG.root / "config" / "lab.yaml").read_text())
BUS = CFG.root / RAW["bus"]["out"]
SCN = CFG.root / RAW["baseline"]["scenarios_dir"]
pytestmark = pytest.mark.skipif(not BUS.is_file(), reason="bus GTFS not built")


def test_frequency_change_and_revert_reproduces_the_parent():
    there = yaml.safe_load((SCN / "T001-bus-frequency.yaml").read_text())["ops"]
    back = yaml.safe_load((SCN / "T001r-bus-frequency-reverted.yaml").read_text())["ops"]
    ops = [next(iter(o.items())) for o in there + back]
    rid = ops[0][1]["route_id"]
    base = {"bus": gen.read_feed(BUS)}
    ctx = {"periods": {"AM": (420, 600), "IP": (600, 960), "PM": (960, 1140)}, "service_date": "20260923",
           "runs": {}, "dwell": {None: 60.0}, "min_trains": 5}
    mid, _ = gen.apply(base, ops[:1], SCN, ctx, 0.0)
    out, _ = gen.apply(base, ops, SCN, ctx, 0.0)

    def route(feed):
        s = feed["stop_times"].merge(feed["trips"][["trip_id", "route_id", "direction_id"]], on="trip_id")
        s = s[s.route_id == rid]
        return sorted(zip(s.direction_id, s.stop_id, s.arrival_time, s.departure_time, s.pickup_type, s.drop_off_type))

    def others(feed):
        mine = feed["trips"].trip_id[feed["trips"].route_id == rid]
        return feed["stop_times"][~feed["stop_times"].trip_id.isin(mine)].reset_index(drop=True)

    def journeys(feed):
        return gen.journey_key(feed["trips"][feed["trips"].route_id == rid]).nunique()

    assert route(mid["bus"]) != route(base["bus"]) and journeys(mid["bus"]) > journeys(base["bus"])
    assert route(out["bus"]) == route(base["bus"])
    assert journeys(out["bus"]) == journeys(base["bus"])
    assert others(out["bus"]).equals(others(base["bus"]))
    assert not out["bus"]["trips"].trip_id.duplicated().any()
