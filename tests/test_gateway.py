"""External rail legs in a comparison (src/lab/gateway.py): synthetic skims."""
import pandas as pd
import pytest

from lab import gateway as gw

DG = pd.DataFrame([
    ("external_in", "G1", "L1", 60.0), ("external_in", "G1", "L2", 40.0),
    ("external_out", "G1", "L1", 30.0), ("external_in", "G2", "L1", 10.0),
], columns=["dir", "gateway", "zone", "trips"])


def skim(rows):
    return pd.DataFrame(rows, columns=["from_id", "to_id", "p50", "unreachable"])


PARENT = skim([("G1", "L1", 30.0, False), ("G1", "L2", 20.0, False), ("L1", "G1", 28.0, False),
               ("G2", "L1", 50.0, True)])
SCEN = skim([("G1", "L1", 20.0, False), ("G1", "L2", 21.0, False), ("L1", "G1", 22.0, False),
             ("G2", "L1", 45.0, False)])


def test_inbound_and_outbound_read_the_right_direction_of_the_skim():
    s, _ = gw.leg_summary(PARENT, SCEN, DG)
    # inbound over both reachable: 60 trips -10 min, 40 trips +1 min
    assert s["external_in"]["mean_change_min"] == pytest.approx((60 * -10 + 40 * 1) / 100)
    assert s["external_out"]["mean_change_min"] == pytest.approx(-6.0)


def test_thresholds_newly_reachable_and_person_hours():
    s, _ = gw.leg_summary(PARENT, SCEN, DG)
    a = s["all"]
    assert a["trips"] == 140
    assert a["trips_faster_by_threshold"] == 90          # 60 in, 30 out; the +1 min leg is not slower by 2
    assert a["trips_slower_by_threshold"] == 0
    assert a["trips_newly_reachable"] == 10               # G2 was unreachable in the parent
    assert a["person_hours_saved_per_weekday"] == pytest.approx(-(60 * -10 + 40 * 1 + 30 * -6) / 60)


def test_a_pair_missing_from_the_skim_is_unreachable_not_dropped():
    s, g = gw.leg_summary(PARENT, SCEN.iloc[:2], DG)       # the scenario skim lacks the outbound pair and G2
    assert s["all"]["trips"] == 140
    assert s["external_out"]["trips_newly_unreachable"] == 30
    assert s["all"]["trips_reachable_in_both"] == 100


def test_by_gateway_frame_and_offsets_are_averaged():
    other = skim([("G1", "L1", 25.0, False), ("G1", "L2", 21.0, False), ("L1", "G1", 22.0, False),
                  ("G2", "L1", 45.0, False)])
    s, g = gw.over_offsets([(PARENT, SCEN), (PARENT, other)], DG)
    assert s["offsets"] == 2
    assert s["external_in"]["mean_change_min"]["mean"] == pytest.approx(((60 * -10 + 40 * 1) / 100 + (60 * -5 + 40 * 1) / 100) / 2)
    row = g[(g.dir == "external_in") & (g.gateway == "G1")].iloc[0]
    assert row.trips == 100


def test_the_input_key_changes_with_the_points():
    g = pd.DataFrame({"id": ["G1"], "lat": [51.4], "lon": [-2.6]})
    z = pd.DataFrame({"id": ["L1"], "lat": [51.45], "lon": [-2.59]})
    assert gw.inputs_key(g, z) == gw.inputs_key(g.copy(), z.copy())
    assert gw.inputs_key(g, z) != gw.inputs_key(g.assign(lat=51.5), z)
