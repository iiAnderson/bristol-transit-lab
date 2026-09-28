"""Tests on the built PT skims (D7). Skipped until the AM skims exist.

* GC from stored mean components = mean of per-minute GC (GC is linear), on the 1%
  sample's full per-minute output.
* r5r vs r5py regression on the 1% sample: the same reachable pairs ± 0.1%, and ≥ 99% of
  p50 within 1 minute (slow: builds an r5py network; ``-m slow``).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab import params, skims
from lab.config import LabConfig

CFG = LabConfig.load()
WORK = CFG.root / "data" / "interim" / "skims" / "pt_AM"
PS = {p.path: p.value for p in params.load(CFG.root / "params" / "base.yaml")}
W = {k: PS[f"generalised_cost.{k}"] for k in ("w_walk", "w_wait", "p_interchange")}
have = (WORK / "chunks").is_dir() and any((WORK / "chunks").glob("minutes_*.parquet"))
pytestmark = pytest.mark.skipif(not have, reason="AM PT skims not built")


def _sample():
    minutes = pd.concat([pd.read_parquet(f) for f in (WORK / "chunks").glob("minutes_*.parquet")])
    summary = skims.combine(WORK / "chunks")
    ids = set(minutes["from_id"])
    return minutes, summary[summary["from_id"].isin(ids)]


def test_gc_from_mean_components_equals_mean_of_per_minute_gc():
    minutes, summary = _sample()
    m = minutes.rename(columns={"access_time": "access_min", "wait_time": "wait_min",
                                "ride_time": "ride_min", "transfer_time": "transfer_min",
                                "egress_time": "egress_min"})
    m["n_transfers"] = (m["n_rides"] - 1).clip(lower=0)
    m["gc"] = skims.gc_from_components(m, W)
    per_minute = m.groupby(["from_id", "to_id"])["gc"].mean()
    from_means = skims.gc_from_components(summary, W).set_axis(
        pd.MultiIndex.from_frame(summary[["from_id", "to_id"]]))
    j = pd.concat([per_minute.rename("a"), from_means.rename("b")], axis=1, join="inner")
    assert len(j) > 10000
    assert np.allclose(j["a"], j["b"], atol=1e-6)


def test_walk_only_minutes_count_as_walk():
    minutes, _ = _sample()
    w = minutes[minutes["n_rides"] == 0]
    assert len(w) > 0
    assert np.allclose(w["access_time"], w["total_time"])
    assert (w[["wait_time", "ride_time", "transfer_time", "egress_time"]] == 0).all().all()


@pytest.mark.slow
def test_r5r_matches_r5py_on_the_one_percent_sample(monkeypatch):
    import datetime as dt
    import sys
    monkeypatch.setattr(sys, "argv", ["pytest"])     # r5py parses argv (-m = --max-memory)

    import geopandas as gpd
    from r5py import TransportMode, TransportNetwork, TravelTimeMatrix
    from shapely.geometry import Point
    _, summary = _sample()
    o = pd.read_csv(WORK / "origins.csv")
    o = o[o["id"].isin(summary["from_id"].unique())]
    d = pd.read_csv(WORK / "destinations.csv")
    g = lambda df: gpd.GeoDataFrame({"id": df["id"]}, crs="EPSG:4326",  # noqa: E731
                                    geometry=[Point(x, y) for x, y in zip(df.lon, df.lat)])
    net = TransportNetwork(str(CFG.root / "data/interim/osm/b2026_clip.osm.pbf"),
                           [str(CFG.root / "data/interim/gtfs/bus_bods.zip"),
                            str(CFG.root / "data/interim/gtfs/rail_darwin.zip")])
    dep = dt.datetime.combine(dt.date(2026, 9, 23), dt.time.fromisoformat(PS["skims.window_start.AM"]))
    t = TravelTimeMatrix(net, origins=g(o), destinations=g(d), departure=dep,
                         departure_time_window=dt.timedelta(minutes=PS["skims.departure_window_min"]),
                         percentiles=[50], transport_modes=[TransportMode.TRANSIT, TransportMode.WALK],
                         max_time=dt.timedelta(minutes=PS["routing.max_trip_min"]),
                         speed_walking=PS["routing.walk_speed_kmh"],
                         max_public_transport_rides=PS["routing.max_rides"])
    r5py_ok = t.dropna(subset=["travel_time"])
    r5r_ok = summary.dropna(subset=["p50"])
    assert abs(len(r5r_ok) - len(r5py_ok)) <= 0.001 * max(len(r5py_ok), 1)
    j = r5r_ok.merge(t, on=["from_id", "to_id"])
    assert (np.abs(j["p50"] - j["travel_time"]) <= 1).mean() >= 0.99
