"""
Traffic-signal delays and the ANPR constraint (plans/P2.md §8 "Central bias").

* Signal nodes: OSM nodes tagged ``highway=traffic_signals`` in the clip. The delay
  sits on each segment whose end node (``v``) is a signal node — the approach — and is
  written into the segment-speed file as a lower speed on that segment
  (L / (L / v + d)), so only OSRM's customise step changes.
* ANPR links are routed once on free-flow speeds for their node paths; a link's modelled
  time in a period is Σ L / v over its segments plus the signal delays on the path.
* The links are split into spatial blocks; delays d[area][period] are fitted on the
  calibration half (least squares on log time), the other half validates.
* IP uses hour labels 11–15 only, which lie inside 10:00–16:00 whether the ANPR stamp
  marks the start or the end of its hour (the convention is unconfirmed).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import osmium
import pandas as pd
import requests
from scipy.optimize import least_squares


class _Signals(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ids: list[int] = []

    def node(self, n):
        if n.tags.get("highway") == "traffic_signals":
            self.ids.append(n.id)


def signal_nodes(pbf: Path) -> set[int]:
    h = _Signals()
    h.apply_file(str(pbf))
    return set(h.ids)


def link_paths(links_geojson: Path, port: int) -> pd.DataFrame:
    """One row per (link, node pair) on the free-flow route start → end."""
    rows = []
    s = requests.Session()
    for f in json.loads(links_geojson.read_text())["features"]:
        c = f["geometry"]["coordinates"]
        (x0, y0), (x1, y1) = c[0][:2], c[-1][:2]
        j = s.get(f"http://127.0.0.1:{port}/route/v1/driving/{x0},{y0};{x1},{y1}",
                  params={"overview": "false", "annotations": "nodes"}, timeout=60).json()
        if j.get("code") != "Ok":
            continue
        rt = j["routes"][0]
        nodes = [n for leg in rt["legs"] for n in leg["annotation"]["nodes"]]
        lid = f["properties"]["JOURNEY_LINK_ID"]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        for u, v in zip(nodes, nodes[1:]):
            rows.append({"link_id": lid, "u": u, "v": v, "route_m": rt["distance"],
                         "link_m": f["properties"]["SHAPE.STLength()"], "mlon": mx, "mlat": my})
    return pd.DataFrame(rows)


def split_links(paths: pd.DataFrame, block_km: float, seed: int = 0) -> pd.Series:
    """link_id -> 'calibration' | 'validation', by spatial block of the link midpoint."""
    m = paths.groupby("link_id")[["mlon", "mlat"]].first()
    bx = np.floor(m["mlon"] * 111.32 * 0.623 / block_km).astype(int)
    by = np.floor(m["mlat"] * 111.32 / block_km).astype(int)
    key = bx.astype(str) + "_" + by.astype(str)
    blocks = sorted(key.unique())
    rng = np.random.default_rng(seed)
    cal = set(rng.permutation(blocks)[: len(blocks) // 2])
    return key.map(lambda k: "calibration" if k in cal else "validation")


def link_times(paths: pd.DataFrame, speeds: pd.DataFrame, seg_area: pd.DataFrame,
               signals: set[int], delays: dict[str, float]) -> pd.Series:
    """Modelled seconds per link: Σ L/v + Σ delay[area of approach] at signal nodes.
    speeds: u, v, speed_kmh, length_m (one period); seg_area: u, v, area."""
    p = paths.merge(speeds, on=["u", "v"], how="left").merge(seg_area, on=["u", "v"], how="left")
    p["t"] = p["length_m"] / (p["speed_kmh"] / 3.6)
    p["sig"] = p["v"].isin(signals)
    p["d"] = np.where(p["sig"], p["area"].map(delays).fillna(delays.get("urban", 0.0)), 0.0)
    g = p.groupby("link_id")
    miss = g["t"].apply(lambda x: x.isna().mean())
    out = g["t"].sum() + g["d"].sum()
    return out[miss < 0.05]


def fit_delays(paths, speeds, seg_area, signals, obs: pd.Series, areas: list[str]) -> dict:
    """Least squares on log(modelled / observed) over the given links; d ≥ 0."""
    def resid(x):
        d = dict(zip(areas, x))
        t = link_times(paths, speeds, seg_area, signals, d)
        j = pd.concat([t.rename("m"), obs.rename("o")], axis=1, join="inner")
        return np.log(j["m"] / j["o"]).to_numpy()
    sol = least_squares(resid, np.full(len(areas), 10.0), bounds=(0, 180))
    return dict(zip(areas, [float(v) for v in sol.x]))


def delay_arrays(seg: pd.DataFrame, signals: set[int], delays: dict[str, dict[str, float]],
                 periods: list[str]) -> dict[str, np.ndarray]:
    """Per-segment delay (s) per period: on approaches (v is a signal node)."""
    is_sig = seg["v"].isin(signals).to_numpy()
    area = seg["area_type"].replace({"buffer": "rural"}).to_numpy()
    out = {}
    for p in periods:
        dp = delays.get(p, delays.get("IP", {}))
        out[p] = np.where(is_sig, np.array([dp.get(a, dp.get("urban", 0.0)) for a in area]), 0.0)
    return out
