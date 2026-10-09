"""
D7 measurement (plans/P3.md P3a-8, decided at approval): how should a generated,
headway-based service be written so that its skims are comparable with the
timetable-based baseline?

One existing route is rewritten by hand, two ways, and skimmed against itself:

* ``frequencies`` — a template trip per stop pattern plus ``frequencies.txt`` rows (one
  per hour of service, headway = 60 ÷ that hour's departures), which R5 routes by
  drawing random schedules;
* ``even`` — explicit trips at that same even headway from a fixed offset.

The route is chosen by rule, not by name: a bus route with no restriction copies, one
dominant stop pattern per direction, frequent in the skim hour, with the most regular
headways.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd


def _mins(hms: pd.Series) -> pd.Series:
    p = hms.str.split(":", expand=True).astype(int)
    return p[0] * 60 + p[1] + p[2] / 60


def _hms(m: float) -> str:
    s = int(round(m * 60))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def read(path: Path) -> dict[str, pd.DataFrame]:
    with zipfile.ZipFile(path) as z:
        return {n: pd.read_csv(z.open(n), dtype=str) for n in z.namelist()}


def write(feed: dict[str, pd.DataFrame], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, df in feed.items():
            b = io.StringIO()
            df.to_csv(b, index=False)
            z.writestr(n, b.getvalue())


def trip_table(feed: dict[str, pd.DataFrame], route_ids=None) -> pd.DataFrame:
    """One row per trip: route, direction, first departure (min), stop pattern, duration."""
    st = feed["stop_times.txt"].assign(seq=lambda x: x.stop_sequence.astype(int)).sort_values(["trip_id", "seq"])
    tr = feed["trips.txt"]
    if route_ids is not None:
        tr = tr[tr.route_id.isin(route_ids)]
        st = st[st.trip_id.isin(tr.trip_id)]
    st = st.assign(t=_mins(st.departure_time))
    g = st.groupby("trip_id")
    t = pd.DataFrame({"start": g.t.first(), "dur": g.t.last() - g.t.first(),
                      "pattern": g.stop_id.agg("|".join)}).reset_index()
    return t.merge(tr, on="trip_id")


def pick_route(feed: dict[str, pd.DataFrame], hour: tuple[int, int], span: tuple[int, int],
               min_per_hour: int, dominant_share: float = 0.9) -> dict:
    tr = feed["trips.txt"]
    plain = tr.groupby("route_id").apply(
        lambda g: bool((g.get("original_trip_id", g.trip_id).fillna(g.trip_id) == g.trip_id).all()),
        include_groups=False)
    bus = set(feed["routes.txt"].route_id[feed["routes.txt"].route_type == "3"]) & set(plain.index[plain])
    t = trip_table(feed, bus)
    t = t[(t.start >= span[0]) & (t.start < span[1])]
    best = None
    for r, g in t.groupby("route_id"):
        if g.direction_id.nunique() != 2:
            continue
        cvs, ok = [], True
        for _, d in g.groupby("direction_id"):
            share = d.pattern.value_counts(normalize=True).iloc[0]
            main = d[d.pattern == d.pattern.value_counts().index[0]].sort_values("start")
            in_hour = ((main.start >= hour[0]) & (main.start < hour[1])).sum()
            gaps = np.diff(main.start)
            if share < dominant_share or in_hour < min_per_hour or len(gaps) < 3:
                ok = False
                break
            cvs.append(gaps.std() / gaps.mean())
        if ok and (best is None or max(cvs) < best["headway_cv"]):
            best = {"route_id": r, "headway_cv": float(max(cvs)), "trips_in_span": int(len(g))}
    if best is None:
        raise RuntimeError("no route meets the D7 selection rule")
    return best


def _bands(main: pd.DataFrame) -> list[tuple[int, int, int]]:
    """(start_min, end_min, departures) per clock hour with at least one departure."""
    h = (main.start // 60).astype(int)
    return [(int(k) * 60, int(k) * 60 + 60, int(n)) for k, n in h.value_counts().sort_index().items()]


def rewrite(feed: dict[str, pd.DataFrame], route_id: str, how: str, offset_min: float = 0.0) -> tuple[dict, dict]:
    """The feed with ``route_id``'s dominant-pattern trips replaced. ``how`` is
    "frequencies" or "even". Trips on minority patterns stay as timetabled."""
    t = trip_table(feed, [route_id])
    st = feed["stop_times.txt"]
    new_trips, new_st, freq, removed = [], [], [], set()
    info = {"route_id": route_id, "how": how, "bands": {}, "trips_removed": 0, "trips_added": 0}
    for d, g in t.groupby("direction_id"):
        pat = g.pattern.value_counts().index[0]
        main = g[g.pattern == pat].sort_values("start")
        removed |= set(main.trip_id)
        tmpl_id = main.iloc[(main.dur - main.dur.median()).abs().argsort().iloc[0]].trip_id
        tmpl = st[st.trip_id == tmpl_id].assign(seq=lambda x: x.stop_sequence.astype(int)).sort_values("seq")
        dep, arr = _mins(tmpl.departure_time), _mins(tmpl.arrival_time)
        t0 = dep.iloc[0]
        base = feed["trips.txt"][feed["trips.txt"].trip_id == tmpl_id].iloc[0].to_dict()
        bands = _bands(main)
        info["bands"][str(d)] = [(a // 60, n) for a, _, n in bands]

        def add(tid: str, start: float):
            row = dict(base, trip_id=tid)
            if "original_trip_id" in row:
                row["original_trip_id"] = tid
            new_trips.append(row)
            new_st.append(tmpl.drop(columns="seq").assign(
                trip_id=tid, arrival_time=[_hms(start + a - t0) for a in arr],
                departure_time=[_hms(start + x - t0) for x in dep]))

        if how == "frequencies":
            tid = f"d7:{route_id}:{d}"
            add(tid, bands[0][0])
            freq += [{"trip_id": tid, "start_time": _hms(a), "end_time": _hms(b),
                      "headway_secs": str(int(round(3600 / n))), "exact_times": "0"} for a, b, n in bands]
        elif how == "even":
            k = 0
            for a, b, n in bands:
                hw = 60 / n
                for i in range(n):
                    add(f"d7:{route_id}:{d}:{k}", a + (offset_min % hw) + i * hw)
                    k += 1
        else:
            raise ValueError(how)
    out = dict(feed)
    out["trips.txt"] = pd.concat([feed["trips.txt"][~feed["trips.txt"].trip_id.isin(removed)],
                                  pd.DataFrame(new_trips)], ignore_index=True)
    out["stop_times.txt"] = pd.concat([st[~st.trip_id.isin(removed)], *new_st], ignore_index=True)
    if freq:
        out["frequencies.txt"] = pd.DataFrame(freq)
    info["trips_removed"], info["trips_added"] = len(removed), len(new_trips)
    return out, info


def compare(a: pd.DataFrame, v: pd.DataFrame, min_route_share: float) -> dict:
    """Variant against the timetable, per pair: total-time p50 and mean, and mean wait
    over ride minutes. Reported for pairs that use the route in the timetable for at
    least ``min_route_share`` of their minutes, and for all pairs."""
    j = a.merge(v, on=["from_id", "to_id"], suffixes=("_tt", "_v"))
    out = {}
    for name, g in (("pairs_using_the_route", j[j.route_share_tt >= min_route_share]), ("all_pairs", j)):
        dp = g.p50_v - g.p50_tt
        dw = g.wait_mean_v - g.wait_mean_tt
        dm = g.total_mean_v - g.total_mean_tt
        out[name] = {
            "pairs": int(len(g)),
            "p50_diff_min": {"mean": round(float(dp.mean()), 2), "mean_abs": round(float(dp.abs().mean()), 2),
                             "p5": float(dp.quantile(.05)), "p95": float(dp.quantile(.95))},
            "share_p50_within_1_min": round(float((dp.abs() <= 1).mean()), 4),
            "share_p50_within_2_min": round(float((dp.abs() <= 2).mean()), 4),
            "mean_total_diff_min": round(float(dm.mean()), 2),
            "mean_wait_diff_min": round(float(dw.mean()), 2),
            "mean_abs_wait_diff_min": round(float(dw.abs().mean()), 2),
            "route_share_tt_vs_variant": [round(float(g.route_share_tt.mean()), 3),
                                          round(float(g.route_share_v.mean()), 3)]}
    return out
