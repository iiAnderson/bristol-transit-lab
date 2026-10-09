"""
Traffic-signal delays and the ANPR constraint (plans/P2.md §8 "Central bias").

* Signal nodes: OSM nodes tagged ``highway=traffic_signals`` in the clip. The delay
  sits on each segment whose end node (``v``) is a signal node — the approach — and is
  written into the segment-speed file as a lower speed on that segment
  (L / (L / v + d)), so only OSRM's customise step changes.
* ANPR links are routed once on free-flow speeds for their node paths; a link's modelled
  time in a period is Σ L / v over its segments plus the signal delays on the path.
* The links are split into spatial blocks, balanced within each area type; the ANPR
  layer — a speed multiplier k[area, period] and a delay per signalised approach
  d[area], for centre and urban non-SRN segments — is fitted on the calibration half
  (robust least squares on log modelled ÷ observed time); the other half validates.
* The ANPR hourly stamp is taken to mark the **end** of its hour (adopted 2026-10-09 on
  the profile evidence in sources.md; ``anpr.hour_stamp`` in config).
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


def anpr_obs_by_label(counts: list[Path], tz: str) -> pd.DataFrame:
    """Observed link times (plate-match-weighted mean s) by ANPR hour label, Tue–Thu,
    excluding August and the Christmas / late-July–early-September windows (approximate
    neutral days; the 2023/24 and 2024/25 calendars were not checked). Periods:
    IP = local labels 11–15 (inside 10:00–16:00 under either hour convention);
    AMPH_if_start = label 08 (stamp = hour start); AMPH_if_end = label 09 (stamp = hour end)."""
    x = pd.concat([pd.read_parquet(f) for f in counts], ignore_index=True)
    t = pd.to_datetime(x["t_ms"], unit="ms", utc=True).dt.tz_convert(tz)
    keep = (t.dt.weekday.isin([1, 2, 3]) & (t.dt.month != 8)
            & ~((t.dt.month == 12) & (t.dt.day >= 18)) & ~((t.dt.month == 1) & (t.dt.day <= 4))
            & ~((t.dt.month == 7) & (t.dt.day >= 22)) & ~((t.dt.month == 9) & (t.dt.day <= 3))
            & (x["matches"] > 0) & (x["journey_s"] > 0))
    x = x[keep.to_numpy()].assign(h=t[keep].dt.hour.to_numpy())
    lab = {**{h: "IP" for h in range(11, 16)}, 8: "AMPH_if_start", 9: "AMPH_if_end"}
    x["period"] = x["h"].map(lab)
    x = x.dropna(subset=["period"])
    g = x.groupby(["link_id", "period"])
    return pd.DataFrame({"obs_s": g.apply(lambda d: np.average(d["journey_s"], weights=d["matches"])),
                         "hours": g.size()}).reset_index()


PERIODS_ALL = ["AM", "AMPH", "IP", "PM", "PMPH", "OP", "WE"]


def anpr_obs(counts: list[Path], tz: str, periods: dict[str, tuple[str, str]],
             stamp: str = "end") -> pd.DataFrame:
    """Observed link times (plate-match-weighted mean s) per model period. ``stamp`` says
    whether the hourly timestamp marks the hour's "start" or "end". Neutral days as
    before (Tue–Thu for weekday periods; Sat–Sun for WE; holiday windows excluded,
    approximate). AMPH = 08:00–09:00, PMPH = 17:00–18:00 (diagnostic)."""
    x = pd.concat([pd.read_parquet(f) for f in counts], ignore_index=True)
    t = pd.to_datetime(x["t_ms"], unit="ms", utc=True)
    if stamp == "end":
        t = t - pd.Timedelta(hours=1)
    elif stamp != "start":
        raise ValueError(f"anpr hour stamp must be 'start' or 'end', not {stamp!r}")
    t = t.dt.tz_convert(tz)
    hol = ((t.dt.month == 8) | ((t.dt.month == 12) & (t.dt.day >= 18))
           | ((t.dt.month == 1) & (t.dt.day <= 4)) | ((t.dt.month == 7) & (t.dt.day >= 22))
           | ((t.dt.month == 9) & (t.dt.day <= 3)))
    keep = (~hol & (x["matches"] > 0) & (x["journey_s"] > 0)).to_numpy()
    x = x[keep].assign(h=t[keep].dt.hour.to_numpy(), wd=t[keep].dt.weekday.to_numpy())
    hr = {k: (int(a[:2]), int(b[:2])) for k, (a, b) in periods.items()}
    in_p = lambda k: x["h"].between(hr[k][0], hr[k][1] - 1)  # noqa: E731
    x["period"] = np.select(
        [x["wd"] >= 5, ~x["wd"].isin([1, 2, 3]), in_p("AM"), in_p("IP"), in_p("PM")],
        ["WE", "drop", "AM", "IP", "PM"], "OP")
    x = x[x["period"] != "drop"]
    x = pd.concat([x, x[(x["period"] == "AM") & (x["h"] == 8)].assign(period="AMPH"),
                   x[(x["period"] == "PM") & (x["h"] == 17)].assign(period="PMPH")])
    g = x.groupby(["link_id", "period"])
    return pd.DataFrame({"obs_s": g.apply(lambda d: np.average(d["journey_s"], weights=d["matches"])),
                         "hours": g.size()}).reset_index()


def link_area(paths: pd.DataFrame, seg_area: pd.DataFrame) -> pd.Series:
    """link_id -> the area type carrying most of the link's length."""
    p = paths.merge(seg_area, on=["u", "v"], how="left")
    p["area"] = p["area"].fillna("rural")
    return p.groupby(["link_id", "area"])["length_m"].sum().unstack(fill_value=0).idxmax(axis=1)


def split_links_balanced(paths: pd.DataFrame, area: pd.Series, block_km: float,
                         seed: int = 0) -> pd.Series:
    """link_id -> 'calibration' | 'validation'. Whole spatial blocks go to one half; within
    each area type the blocks are dealt in a seeded random order to whichever half has
    fewer links so far, so both halves hold about half of each area type's links."""
    m = paths.groupby("link_id")[["mlon", "mlat"]].first()
    bx = np.floor(m["mlon"] * 111.32 * 0.623 / block_km).astype(int)
    by = np.floor(m["mlat"] * 111.32 / block_km).astype(int)
    key = area.reindex(m.index).fillna("rural") + "_" + bx.astype(str) + "_" + by.astype(str)
    rng = np.random.default_rng(seed)
    out: dict[str, str] = {}
    for a in sorted(area.reindex(m.index).fillna("rural").unique()):
        sizes = key[key.str.startswith(a + "_")].value_counts()
        n = {"calibration": 0, "validation": 0}
        for b in rng.permutation(sorted(sizes.index)):
            h = "calibration" if n["calibration"] <= n["validation"] else "validation"
            out[b] = h
            n[h] += int(sizes[b])
    return key.map(out)


def link_components(paths: pd.DataFrame, speeds: pd.DataFrame, seg_info: pd.DataFrame,
                    signals: set[int], areas: list[str]) -> pd.DataFrame:
    """Per link: seconds on adjustable segments of each area (T_<area>), signalised
    approaches there (S_<area>) and seconds elsewhere (T_other). Adjustable = not SRN.
    speeds: u, v, speed_kmh, length_m; seg_info: u, v, area, srn (bool)."""
    p = paths.merge(speeds, on=["u", "v"], how="left").merge(seg_info, on=["u", "v"], how="left")
    p["t"] = p["length_m"] / (p["speed_kmh"] / 3.6)
    adj = p["area"].isin(areas) & ~p["srn"].fillna(False).astype(bool)
    p["sig"] = p["v"].isin(signals) & adj
    out = pd.DataFrame(index=sorted(p["link_id"].unique()))
    for a in areas:
        m = adj & (p["area"] == a)
        out[f"T_{a}"] = p[m].groupby("link_id")["t"].sum()
        out[f"S_{a}"] = p[m].groupby("link_id")["sig"].sum()
    out["T_other"] = p[~adj].groupby("link_id")["t"].sum()
    miss = p.groupby("link_id")["t"].apply(lambda x: x.isna().mean())
    return out.fillna(0.0)[miss.reindex(out.index) < 0.05]


def layer_times(comp: pd.DataFrame, k: dict[str, float], d: dict[str, float]) -> pd.Series:
    t = comp["T_other"].copy()
    for a in k:
        t = t + comp[f"T_{a}"] / k[a] + comp[f"S_{a}"] * d.get(a, 0.0)
    return t


def fit_layer(comp: dict[str, pd.DataFrame], obs: pd.DataFrame, areas: list[str],
              periods: list[str]) -> dict:
    """comp: period -> link components; obs: link_id, period, obs_s (calibration half).
    Minimises a soft-L1 loss on log(modelled / observed) over link × period rows.
    Returns {"k": {(area, period): k}, "d": {area: seconds}} with k in [0.3, 1.5], d ≥ 0."""
    rows = []
    for per in periods:
        o = obs[obs["period"] == per].set_index("link_id")["obs_s"]
        j = comp[per].join(o.rename("o"), how="inner")
        rows.append(j.assign(pi=periods.index(per)))
    J = pd.concat(rows)
    na, npd = len(areas), len(periods)
    T = np.stack([J[f"T_{a}"].to_numpy() for a in areas])
    S = np.stack([J[f"S_{a}"].to_numpy() for a in areas])
    pi = J["pi"].to_numpy()

    def resid(x):
        k = x[: na * npd].reshape(na, npd)
        d = x[na * npd:]
        t = J["T_other"].to_numpy() + sum(T[i] / k[i, pi] + S[i] * d[i] for i in range(na))
        return np.log(t / J["o"].to_numpy())
    x0 = np.r_[np.ones(na * npd), np.full(na, 5.0)]
    lo = np.r_[np.full(na * npd, 0.3), np.zeros(na)]
    hi = np.r_[np.full(na * npd, 1.5), np.full(na, 120.0)]
    sol = least_squares(resid, x0, bounds=(lo, hi), loss="soft_l1", f_scale=0.3)
    k = sol.x[: na * npd].reshape(na, npd)
    return {"k": {(a, p): float(k[i, q]) for i, a in enumerate(areas)
                  for q, p in enumerate(periods)},
            "d": {a: float(sol.x[na * npd + i]) for i, a in enumerate(areas)},
            "n_rows": int(len(J)), "n_links": int(J.index.nunique())}
