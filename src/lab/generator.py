"""
The scenario generator (SPEC §5; plans/P3.md P3b-2, D5, D7, D8): applies a validated
scenario's ops to its parent's GTFS and returns the new feeds, for one timetable offset.

Rules it follows:

* **Explicit trips, never ``frequencies.txt``** (D7). A generated or re-timed service
  runs at the stated headway from an anchor, shifted by ``offset × headway``: the same
  fraction of its own headway for every generated service of the scenario.
* **One vehicle journey = a source trip and all its restriction copies**
  (``original_trip_id``). Journeys are removed, replaced and re-timed whole.
* **Run times are not guessed** (D8). A leg on existing track between two stations that
  trains serve today takes the median working time of those trains; anything else takes
  the speed-profile rule on the drawn geometry. The rule used for each leg is recorded.
* **Fail loudly.** An op, or a case of one, that is not built raises ``GeneratorError``
  naming it; nothing is silently skipped.

Feeds are dicts of GTFS tables (data frames of strings) keyed by file stem. New routes go
into their own feed, ``generated``; the parent's feeds are changed only where an op
modifies or removes one of their routes.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROUTE_TYPE = {"heavy_rail": "2", "light_rail": "0", "tram": "0", "metro": "1", "brt": "3", "bus": "3"}
STREET_TYPES = {"at_grade_street"}


class GeneratorError(RuntimeError):
    pass


# ------------------------------------------------------------------ time helpers

def mins(hms: pd.Series) -> pd.Series:
    p = hms.str.split(":", expand=True).astype(int)
    return p[0] * 60 + p[1] + p[2] / 60


def hms(m: float) -> str:
    s = int(round(m * 60))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def leg_time_s(dist_m: float, max_kmh: float, accel_ms2: float) -> float:
    """Start-to-stop time over ``dist_m``: accelerate at ``accel_ms2`` to ``max_kmh``,
    cruise, brake at the same rate; a leg too short to reach top speed never does."""
    v = max_kmh / 3.6
    if dist_m >= v * v / accel_ms2:
        return dist_m / v + v / accel_ms2
    return 2 * math.sqrt(dist_m / accel_ms2)


def period_windows(periods: dict[str, tuple[float, float]], span: str) -> dict[str, list[tuple[float, float]]]:
    """Each period's minutes-of-day windows inside the service span. ``OP`` is every
    part of the span outside the named periods. The span may run past midnight."""
    a, b = span.split("-")
    lo = int(a[:2]) * 60 + int(a[3:])
    hi = int(b[:2]) * 60 + int(b[3:])
    if hi <= lo:
        hi += 1440
    out: dict[str, list[tuple[float, float]]] = {}
    named = sorted((v for k, v in periods.items() if k != "OP"))
    for name, (p0, p1) in periods.items():
        if name == "OP":
            continue
        for shift in (0, 1440):
            s, e = max(lo, p0 + shift), min(hi, p1 + shift)
            if e > s:
                out.setdefault(name, []).append((s, e))
    edges = sorted({lo, hi, *[x + sh for p in named for x in p for sh in (0, 1440) if lo < x + sh < hi]})
    for s, e in zip(edges, edges[1:]):
        mid = (s + e) / 2
        if not any(p0 + sh <= mid < p1 + sh for p0, p1 in named for sh in (0, 1440)):
            out.setdefault("OP", []).append((s, e))
    return out


def departures(windows: dict[str, list[tuple[float, float]]], headways: dict[str, float], offset: float,
               anchor: dict[str, float] | None = None) -> list[tuple[float, str]]:
    """Departure minutes for one direction: in each period with a headway, every
    ``headway`` minutes from the window's start (or the period's ``anchor``, the first
    departure of the service being replaced), shifted by ``offset × headway``."""
    out = []
    for per, hw in headways.items():
        for s, e in windows.get(per, []):
            first = (anchor or {}).get(per, s)
            first = first + offset * hw
            first -= math.floor((first - s) / hw) * hw          # the first slot at or after s
            t = first
            while t < e - 1e-9:
                out.append((round(t, 4), per))
                t += hw
    return sorted(out)


# ------------------------------------------------------------------ geometry

def _to_m():
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform


def read_alignment(path: Path):
    """The alignment as one projected line, with the stretch of it each feature covers
    (start chainage, end chainage, alignment_type, timing_points)."""
    from shapely.geometry import shape
    from shapely.ops import linemerge, transform, unary_union
    to_m = _to_m()
    feats = json.loads(Path(path).read_text())["features"]
    geoms = [transform(to_m, shape(f["geometry"])) for f in feats]
    line = geoms[0] if len(geoms) == 1 else linemerge(unary_union(geoms))
    if line.geom_type != "LineString":
        raise GeneratorError(f"{Path(path).name}: the alignment's segments do not join into one line")
    parts = []
    for f, g in zip(feats, geoms):
        a, b = sorted((line.project(g.interpolate(0)), line.project(g.interpolate(g.length))))
        pr = f.get("properties") or {}
        parts.append((a, b, pr["alignment_type"], pr.get("timing_points")))
    return line, parts


def read_stops(path: Path, line) -> pd.DataFrame:
    """Stops in order along the line: stop_id, name, lon, lat, chainage_m, existing, tiploc."""
    from shapely.geometry import shape
    from shapely.ops import transform
    to_m = _to_m()
    rows = []
    for f in json.loads(Path(path).read_text())["features"]:
        g = shape(f["geometry"])
        pr = f.get("properties") or {}
        rows.append({"stop_id": pr["stop_id"], "stop_name": pr.get("name", pr["stop_id"]), "lon": g.x, "lat": g.y,
                     "chainage_m": line.project(transform(to_m, g)), "existing": bool(pr.get("existing")),
                     "tiploc": pr.get("tiploc")})
    return pd.DataFrame(rows).sort_values("chainage_m").reset_index(drop=True)


def leg_types(parts, a: float, b: float) -> dict[str, float]:
    """Metres of each alignment type between two chainages."""
    out: dict[str, float] = {}
    for s, e, t, _ in parts:
        ov = min(b, e) - max(a, s)
        if ov > 0:
            out[t] = out.get(t, 0.0) + ov
    return out


# ------------------------------------------------------------------ run times (D8)

def working_times(tp: pd.DataFrame) -> tuple[dict, dict]:
    """From the Darwin timing-point table: the median working run time in seconds between
    consecutive public calls (from, to, operator) → (median, trains), and each operator's
    median dwell at intermediate calls. Journeys the passenger timetable excludes, and
    cancelled calls, are left out: only trains calling at both ends of a section count."""
    t = tp[tp.exclusion.isna() & ~tp.journey_cancelled & ~tp.cancelled & tp.public].sort_values(["rid", "seq"])
    nxt = t.groupby("rid").shift(-1)
    pair = t.assign(to=nxt.tpl, run=nxt.arr_s - t.dep_s).dropna(subset=["to", "run"])
    runs = {}
    for (a, b, toc), g in pair.groupby(["tpl", "to", "toc"]):
        runs[(a, b, toc)] = (float(g.run.median()), int(len(g)))
    for (a, b), g in pair.groupby(["tpl", "to"]):
        runs[(a, b, None)] = (float(g.run.median()), int(len(g)))
    mid = t[(t.kind == "IP") & t.arr_s.notna() & t.dep_s.notna()]
    dwell = {toc: float((g.dep_s - g.arr_s).median()) for toc, g in mid.groupby("toc")}
    dwell[None] = float((mid.dep_s - mid.arr_s).median()) if len(mid) else float("nan")
    return runs, dwell


def legs(stops: pd.DataFrame, parts, mode: str, profile: dict | None, ctx: dict, operator: str | None,
         where: str) -> list[dict]:
    """Run time, rule and dwell for each leg between consecutive stops, in both
    directions. ``ctx`` has ``runs`` and ``dwell`` from ``working_times`` and
    ``min_trains`` (the fewest trains a working-time median may rest on)."""
    out = []
    for i in range(len(stops) - 1):
        a, b = stops.iloc[i], stops.iloc[i + 1]
        dist = float(b.chainage_m - a.chainage_m)
        if dist <= 0:
            raise GeneratorError(f"{where}: stops {a.stop_id} and {b.stop_id} are at the same place on the line")
        types = leg_types(parts, a.chainage_m, b.chainage_m)
        if mode in ("bus", "brt") and any(t in STREET_TYPES for t in types):
            raise GeneratorError(f"{where}: an on-street {mode} leg needs `bus_speed_ratio`, which is not fitted "
                                 "yet (plans/P3.md P3b-7)")
        rec = {"from": a.stop_id, "to": b.stop_id, "dist_m": round(dist, 1),
               "types_m": {k: round(v, 1) for k, v in types.items()}}
        for d, (x, y) in (("fwd", (a, b)), ("rev", (b, a))):
            run = None
            if set(types) == {"existing_rail"} and x.tiploc and y.tiploc:
                hit = ctx["runs"].get((x.tiploc, y.tiploc, operator)) or ctx["runs"].get((x.tiploc, y.tiploc, None))
                if hit and hit[1] >= ctx["min_trains"]:
                    run = hit[0]
                    rec[f"{d}_rule"] = f"working times, median of {hit[1]} trains"
            if run is None:
                if not profile:
                    raise GeneratorError(f"{where}: no train is timed {x.stop_id} → {y.stop_id} today and the op "
                                         "has no speed_profile to fall back on")
                run = leg_time_s(dist, profile["max_kmh"], profile["accel_ms2"])
                rec[f"{d}_rule"] = "speed profile"
            rec[f"{d}_s"] = round(run, 1)
        out.append(rec)
    return out


def _dwell(profile: dict | None, ctx: dict, operator: str | None) -> float:
    if profile:
        return float(profile["dwell_s"])
    d = ctx["dwell"].get(operator, ctx["dwell"].get(None))
    if d is None or np.isnan(d):
        raise GeneratorError("no dwell time: give a speed_profile or an operator with trains today")
    return d


def stop_offsets(leg_list: list[dict], dwell_s: float, reverse: bool = False) -> list[tuple[str, float, float]]:
    """(stop_id, arrival, departure) in minutes from the first departure, along the legs
    in one direction; dwell at every intermediate stop."""
    seq = [(lg["to"], lg["from"], lg["rev_s"]) for lg in reversed(leg_list)] if reverse else \
        [(lg["from"], lg["to"], lg["fwd_s"]) for lg in leg_list]
    out, t = [(seq[0][0], 0.0, 0.0)], 0.0
    for i, (_, to, run) in enumerate(seq):
        t += run / 60
        arr = t
        if i < len(seq) - 1:
            t += dwell_s / 60
        out.append((to, arr, t))
    return out


# ------------------------------------------------------------------ feed helpers

def empty_feed(date: str, agency_name: str) -> dict[str, pd.DataFrame]:
    return {"agency": pd.DataFrame([{"agency_id": "GEN", "agency_name": agency_name,
                                     "agency_url": "https://example.invalid/", "agency_timezone": "Europe/London"}]),
            "stops": pd.DataFrame(columns=["stop_id", "stop_name", "stop_lat", "stop_lon"]),
            "routes": pd.DataFrame(columns=["route_id", "agency_id", "route_short_name", "route_long_name",
                                            "route_type", "route_desc"]),
            "trips": pd.DataFrame(columns=["route_id", "service_id", "trip_id", "direction_id", "original_trip_id"]),
            "stop_times": pd.DataFrame(columns=["trip_id", "arrival_time", "departure_time", "stop_id",
                                                "stop_sequence", "pickup_type", "drop_off_type"]),
            "calendar_dates": pd.DataFrame([{"service_id": "GEN", "date": date, "exception_type": "1"}])}


def journey_key(trips: pd.DataFrame) -> pd.Series:
    k = trips["original_trip_id"] if "original_trip_id" in trips else trips["trip_id"]
    return k.fillna(trips["trip_id"])


def find_feed(feeds: dict, route_id: str) -> str:
    for name, f in feeds.items():
        if route_id in set(f["routes"].route_id):
            return name
    raise GeneratorError(f"route {route_id} is in no feed")


def remove_journeys(feed: dict, trip_ids: set[str]) -> None:
    feed["trips"] = feed["trips"][~feed["trips"].trip_id.isin(trip_ids)].reset_index(drop=True)
    feed["stop_times"] = feed["stop_times"][~feed["stop_times"].trip_id.isin(trip_ids)].reset_index(drop=True)


def _add_stops(gen: dict, stops: pd.DataFrame) -> None:
    new = stops[~stops.stop_id.isin(set(gen["stops"].stop_id))]
    add = pd.DataFrame({"stop_id": new.stop_id, "stop_name": new.stop_name,
                        "stop_lat": new.lat.map(lambda v: f"{v:.6f}"), "stop_lon": new.lon.map(lambda v: f"{v:.6f}")})
    gen["stops"] = pd.concat([gen["stops"], add], ignore_index=True)


def _emit(gen: dict, route_id: str, direction: int, deps: list[tuple[float, str]],
          offs: list[tuple[str, float, float]], prefix: str) -> int:
    trips, st = [], []
    for n, (t0, _per) in enumerate(deps):
        tid = f"{prefix}:{direction}:{n}"
        trips.append({"route_id": route_id, "service_id": "GEN", "trip_id": tid, "direction_id": str(direction),
                      "original_trip_id": tid})
        for i, (sid, arr, dep) in enumerate(offs):
            st.append({"trip_id": tid, "arrival_time": hms(t0 + arr), "departure_time": hms(t0 + dep),
                       "stop_id": sid, "stop_sequence": str(i + 1), "pickup_type": "0", "drop_off_type": "0"})
    gen["trips"] = pd.concat([gen["trips"], pd.DataFrame(trips)], ignore_index=True)
    gen["stop_times"] = pd.concat([gen["stop_times"], pd.DataFrame(st)], ignore_index=True)
    return len(trips)


# ------------------------------------------------------------------ ops

def _service(gen: dict, route_id: str, op: dict, base: Path, ctx: dict, offset: float, where: str,
             desc: str = "") -> dict:
    line, parts = read_alignment(base / op["alignment"])
    stops = read_stops(base / op["stops"], line)
    operator = op.get("operator")
    lg = legs(stops, parts, op["mode"], op.get("speed_profile"), ctx, operator, where)
    dwell = _dwell(op.get("speed_profile"), ctx, operator)
    _add_stops(gen, stops)
    gen["routes"] = pd.concat([gen["routes"], pd.DataFrame([{
        "route_id": route_id, "agency_id": "GEN", "route_short_name": route_id,
        "route_long_name": op.get("name", route_id), "route_type": ROUTE_TYPE[op["mode"]],
        "route_desc": desc}])], ignore_index=True)
    win = period_windows(ctx["periods"], op["span"])
    deps = departures(win, op["headways_min"], offset)
    n = 0
    for direction, rev in ((0, False), (1, True)):
        n += _emit(gen, route_id, direction, deps, stop_offsets(lg, dwell, rev), f"gen:{route_id}")
    fwd = stop_offsets(lg, dwell)[-1][1]
    rev = stop_offsets(lg, dwell, True)[-1][1]
    return {"route_id": route_id, "stops": len(stops), "length_km": round(float(stops.chainage_m.iloc[-1]
            - stops.chainage_m.iloc[0]) / 1000, 2), "legs": lg, "dwell_s": dwell,
            "end_to_end_min": {"fwd": round(fwd, 1), "rev": round(rev, 1)}, "trips": n,
            "periods_without_service": sorted(set(win) - set(op["headways_min"])),
            "departures_per_period": pd.Series([p for _, p in deps]).value_counts().to_dict() if deps else {}}


def _journeys(feed: dict, route_id: str) -> pd.DataFrame:
    """One row per vehicle journey of a route: journey, direction, start (min), pattern,
    and the trip ids (the source trip and its copies) that make it up."""
    tr = feed["trips"][feed["trips"].route_id == route_id].copy()
    tr["journey"] = journey_key(tr)
    st = feed["stop_times"][feed["stop_times"].trip_id.isin(tr.trip_id)].copy()
    st["seq"] = st.stop_sequence.astype(int)
    st["t"] = mins(st.departure_time)
    st = st.sort_values(["trip_id", "seq"]).merge(tr[["trip_id", "journey"]], on="trip_id")
    # a journey's full pattern is the union of its copies' calls, in time order
    full = st.drop_duplicates(["journey", "stop_id", "t"]).sort_values(["journey", "t", "seq"])
    g = full.groupby("journey")
    j = pd.DataFrame({"start": g.t.first(), "pattern": g.stop_id.agg("|".join)}).reset_index()
    d = tr.drop_duplicates("journey").set_index("journey")
    j["direction"] = j.journey.map(d.direction_id if "direction_id" in d else pd.Series("0", index=d.index)).fillna("0")
    j["trip_ids"] = j.journey.map(tr.groupby("journey").trip_id.agg(list))
    return j


def _retime(feed: dict, route_id: str, headways: dict[str, float], ctx: dict, offset: float) -> dict:
    """Replace a route's journeys, period by period, with journeys at a new headway.

    Per direction and stop pattern (the dominant pattern only; journeys on other
    patterns stay as timetabled): the new departures are anchored on the first existing
    departure in the period, and each takes the stop-time profile of the existing
    journey nearest in time, copies and all. So the same headway on an evenly timed
    route reproduces its stop times exactly."""
    j = _journeys(feed, route_id)
    st_all = feed["stop_times"]
    report = {"route_id": route_id, "periods": {}, "journeys_removed": 0, "journeys_added": 0}
    new_trips, new_st, drop = [], [], set()
    base_trips = feed["trips"].set_index("trip_id")
    for direction, g in j.groupby("direction"):
        main = g[g.pattern == g.pattern.mode().iloc[0]].sort_values("start")
        for per, hw in headways.items():
            lo, hi = ctx["periods"][per] if per != "OP" else (None, None)
            if per == "OP":
                named = [v for k, v in ctx["periods"].items() if k != "OP"]
                old = main[~main.start.map(lambda t: any(a <= t < b for a, b in named))]
                if old.empty:
                    continue
                wins = [(old.start.min(), old.start.max() + 1e-6)]
            else:
                old = main[(main.start >= lo) & (main.start < hi)]
                wins = [(lo, hi)]
            if old.empty:
                raise GeneratorError(f"route {route_id} direction {direction} has no journey in {per} to re-time; "
                                     "use add_line or replace_route to add service where there is none")
            deps = departures({per: wins}, {per: hw}, offset, {per: float(old.start.iloc[0])})
            for n, (t0, _) in enumerate(deps):
                tmpl = old.iloc[(old.start - t0).abs().argsort().iloc[0]]
                shift = t0 - tmpl.start
                jid = f"gen:{route_id}:{direction}:{per}:{n}"
                for k, tid in enumerate(tmpl.trip_ids):
                    new_id = jid if len(tmpl.trip_ids) == 1 else f"{jid}#{k}"
                    row = base_trips.loc[tid].to_dict() | {"trip_id": new_id}
                    if "original_trip_id" in row:
                        row["original_trip_id"] = jid
                    new_trips.append(row)
                    s = st_all[st_all.trip_id == tid].copy()
                    s["trip_id"] = new_id
                    s["arrival_time"] = (mins(s.arrival_time) + shift).map(hms)
                    s["departure_time"] = (mins(s.departure_time) + shift).map(hms)
                    new_st.append(s)
            for ids in old.trip_ids:
                drop |= set(ids)
            report["periods"].setdefault(per, {})[str(direction)] = {"journeys_before": int(len(old)),
                                                                     "journeys_after": len(deps), "headway_min": hw}
            report["journeys_removed"] += int(len(old))
            report["journeys_added"] += len(deps)
    remove_journeys(feed, drop)
    feed["trips"] = pd.concat([feed["trips"], pd.DataFrame(new_trips)], ignore_index=True)
    feed["stop_times"] = pd.concat([feed["stop_times"], *new_st], ignore_index=True)
    return report


def _remove_calls(feed: dict, route_id: str, stops: list[str]) -> dict:
    """Stop calling at ``stops``: the calls go and every later time on the trip moves
    earlier by the dwell that was timetabled there. The time lost to braking and
    accelerating is not given back [a stated simplification]."""
    tr = set(feed["trips"].trip_id[feed["trips"].route_id == route_id])
    st = feed["stop_times"]
    mine = st[st.trip_id.isin(tr)].copy()
    mine["seq"] = mine.stop_sequence.astype(int)
    mine = mine.sort_values(["trip_id", "seq"])
    mine["arr"], mine["dep"] = mins(mine.arrival_time), mins(mine.departure_time)
    gone = mine.stop_id.isin(stops)
    ends = mine.groupby("trip_id").seq.transform("min").eq(mine.seq) | mine.groupby("trip_id").seq.transform("max").eq(mine.seq)
    if (gone & ends).any():
        raise GeneratorError(f"route {route_id}: removing a call at a trip's first or last stop is a truncation, "
                             "which `stopping_pattern` does not do")
    saved = ((mine.dep - mine.arr).where(gone, 0.0)).groupby(mine.trip_id).cumsum()
    mine["arr"], mine["dep"] = mine.arr - saved, mine.dep - saved
    keep = mine[~gone].copy()
    keep["arrival_time"], keep["departure_time"] = keep.arr.map(hms), keep.dep.map(hms)
    keep["stop_sequence"] = (keep.groupby("trip_id").cumcount() + 1).astype(str)
    feed["stop_times"] = pd.concat([st[~st.trip_id.isin(tr)], keep[st.columns]], ignore_index=True)
    return {"route_id": route_id, "calls_removed": int(gone.sum()), "stops": stops,
            "minutes_saved_per_trip_median": round(float(saved.groupby(mine.trip_id).max().median()), 2)}


def _extend(feeds: dict, route_id: str, ext: dict, base: Path, ctx: dict, where: str,
            mode: str, operator: str | None) -> dict:
    """Extend a route beyond ``from_stop``: trips that end there run on along the new
    stops; trips that start there start from the far end instead, earlier, and reach
    ``from_stop`` at their timetabled time less the dwell. Other trips are untouched."""
    name = find_feed(feeds, route_id)
    feed = feeds[name]
    line, parts = read_alignment(base / ext["alignment"])
    stops = read_stops(base / ext["stops"], line)
    if stops.stop_id.iloc[0] != ext["from_stop"]:
        if stops.stop_id.iloc[-1] != ext["from_stop"]:
            raise GeneratorError(f"{where}: from_stop {ext['from_stop']} is not at an end of the extension")
        stops = stops.iloc[::-1].assign(chainage_m=lambda d: d.chainage_m.max() - d.chainage_m).reset_index(drop=True)
        parts = [(stops.chainage_m.max() - b, stops.chainage_m.max() - a, t, tp) for a, b, t, tp in parts]
    lg = legs(stops, parts, mode, ext.get("speed_profile"), ctx, operator, where)
    dwell = _dwell(ext.get("speed_profile"), ctx, operator)
    out_offs, in_offs = stop_offsets(lg, dwell), stop_offsets(lg, dwell, True)
    new = stops.iloc[1:]
    have = set(feed["stops"].stop_id)
    add = new[~new.stop_id.isin(have)]
    cols = feed["stops"].columns
    rows = pd.DataFrame({"stop_id": add.stop_id, "stop_name": add.stop_name,
                         "stop_lat": add.lat.map(lambda v: f"{v:.6f}"), "stop_lon": add.lon.map(lambda v: f"{v:.6f}")})
    feed["stops"] = pd.concat([feed["stops"], rows.reindex(columns=cols).fillna("0" if "location_type" in cols else "")],
                              ignore_index=True)
    tr = set(feed["trips"].trip_id[feed["trips"].route_id == route_id])
    st = feed["stop_times"]
    mine = st[st.trip_id.isin(tr)].copy()
    mine["seq"] = mine.stop_sequence.astype(int)
    mine = mine.sort_values(["trip_id", "seq"])
    out_rows, n_end, n_start = [], 0, 0
    for tid, g in mine.groupby("trip_id", sort=False):
        g = g.copy()
        first, last = g.iloc[0], g.iloc[-1]
        extra = {c: "0" for c in ("pickup_type", "drop_off_type") if c in g}
        if last.stop_id == ext["from_stop"]:
            t0 = mins(pd.Series([last.arrival_time])).iloc[0] + dwell / 60
            g.loc[g.index[-1], "departure_time"] = hms(t0)
            for c in extra:
                g.loc[g.index[-1], c] = "0"                      # no longer the terminus
            tail = pd.DataFrame([{"trip_id": tid, "arrival_time": hms(t0 + a), "departure_time": hms(t0 + d),
                                  "stop_id": sid, **extra} for sid, a, d in out_offs[1:]])
            g = pd.concat([g, tail], ignore_index=True)
            n_end += 1
        elif first.stop_id == ext["from_stop"]:
            arr_at = mins(pd.Series([first.departure_time])).iloc[0] - dwell / 60
            t0 = arr_at - in_offs[-1][1]
            g.loc[g.index[0], "arrival_time"] = hms(arr_at)
            for c in extra:
                g.loc[g.index[0], c] = "0"
            head = pd.DataFrame([{"trip_id": tid, "arrival_time": hms(t0 + a), "departure_time": hms(t0 + d),
                                  "stop_id": sid, **extra} for sid, a, d in in_offs[:-1]])
            g = pd.concat([head, g], ignore_index=True)
            n_start += 1
        g["stop_sequence"] = (np.arange(len(g)) + 1).astype(str)
        out_rows.append(g)
    if n_end + n_start == 0:
        raise GeneratorError(f"{where}: no trip of route {route_id} starts or ends at {ext['from_stop']}")
    ext_st = pd.concat(out_rows, ignore_index=True).reindex(columns=st.columns)
    feed["stop_times"] = pd.concat([st[~st.trip_id.isin(tr)], ext_st], ignore_index=True)
    return {"route_id": route_id, "from_stop": ext["from_stop"], "new_stops": list(new.stop_id), "legs": lg,
            "dwell_s": dwell, "extension_min": {"out": round(out_offs[-1][1], 1), "back": round(in_offs[-1][1], 1)},
            "trips_extended_at_end": n_end, "trips_extended_at_start": n_start,
            "trips_untouched": len(tr) - n_end - n_start}


NOT_BUILT = {
    "reroute": "moving a route onto a new alignment between two of its stops",
    "add_stop": "inserting a stop into an existing route",
    "road_speed_factor": "re-timing buses on a set of links (needs bus_speed_ratio, plans/P3.md P3b-7)",
}


def apply(feeds: dict[str, dict], ops: list[tuple[str, dict]], base: Path, ctx: dict, offset: float,
          modes: dict[str, str] | None = None) -> tuple[dict[str, dict], list[dict]]:
    """Apply a scenario's ops in order. ``feeds`` (not mutated) maps a feed name to its
    tables; ``ctx`` has periods (name → (start, end) minutes), service_date (YYYYMMDD),
    runs, dwell, min_trains. Returns the new feeds — with ``generated`` added when an op
    creates a route — and one report per op."""
    feeds = {n: {k: v.copy() for k, v in f.items()} for n, f in feeds.items()}
    reports = []

    def gen() -> dict:
        if "generated" not in feeds:
            feeds["generated"] = empty_feed(ctx["service_date"], "Scenario services")
        return feeds["generated"]

    for i, (kind, op) in enumerate(ops):
        where = f"op {i + 1} ({kind})"
        if kind == "add_line":
            rep = _service(gen(), op["route_id"], op, base, ctx, offset, where)
        elif kind == "remove_route":
            name = find_feed(feeds, op["route_id"])
            ids = set(feeds[name]["trips"].trip_id[feeds[name]["trips"].route_id == op["route_id"]])
            remove_journeys(feeds[name], ids)
            feeds[name]["routes"] = feeds[name]["routes"][feeds[name]["routes"].route_id != op["route_id"]]
            rep = {"route_id": op["route_id"], "trips_removed": len(ids)}
        elif kind == "replace_route":
            name = find_feed(feeds, op["route_id"])
            j = _journeys(feeds[name], op["route_id"])
            pers = op.get("periods")
            if pers:
                keep = j.start.map(lambda t: not any(ctx["periods"][p][0] <= t < ctx["periods"][p][1] for p in pers))
                j = j[~keep]
            ids = {t for ids_ in j.trip_ids for t in ids_}
            remove_journeys(feeds[name], ids)
            if not pers:
                feeds[name]["routes"] = feeds[name]["routes"][feeds[name]["routes"].route_id != op["route_id"]]
            sub = dict(op)
            if pers:
                sub["headways_min"] = {p: h for p, h in op["headways_min"].items() if p in pers}
            rep = _service(gen(), op["new_route_id"], sub, base, ctx, offset, where,
                           desc=f"replaces {op['route_id']}" + (f" in {', '.join(pers)}" if pers else ""))
            rep |= {"replaces": op["route_id"], "journeys_removed": int(len(j)), "periods": pers or "all day"}
        elif kind == "modify_route":
            rid = op["route_id"]
            name = find_feed(feeds, rid)
            rep = {"route_id": rid}
            if "truncate_at" in op:
                raise GeneratorError(f"{where}: `truncate_at` is not built: which side of the stop is kept needs "
                                     "defining first")
            if "stopping_pattern" in op:
                pat = op["stopping_pattern"]
                if pat.get("add"):
                    raise GeneratorError(f"{where}: adding a call to an existing route is not built")
                if pat.get("remove"):
                    rep["stopping_pattern"] = _remove_calls(feeds[name], rid, pat["remove"])
            if "extend_to" in op:
                rt = feeds[name]["routes"].set_index("route_id").loc[rid]
                mode = (modes or {}).get(rid) or {"2": "heavy_rail", "0": "tram", "1": "metro"}.get(rt.route_type, "bus")
                rep["extend_to"] = _extend(feeds, rid, op["extend_to"], base, ctx, where, mode,
                                           rt.get("agency_id") if mode == "heavy_rail" else None)
            if "headways_min" in op:
                rep["headways"] = _retime(feeds[name], rid, op["headways_min"], ctx, offset)
        elif kind in ("fare_change", "landuse_delta"):
            rep = {"recorded": True, "effect_on_network": "none"}
        elif kind in NOT_BUILT:
            raise GeneratorError(f"{where}: not built yet — {NOT_BUILT[kind]}")
        else:
            raise GeneratorError(f"{where}: unknown op")
        reports.append({"op": i + 1, "type": kind, **rep})
    return feeds, reports


def write_feed(feed: dict, path: Path) -> None:
    import io
    import zipfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, df in feed.items():
            b = io.StringIO()
            df.to_csv(b, index=False)
            z.writestr(f"{name}.txt", b.getvalue())


def read_feed(path: Path) -> dict[str, pd.DataFrame]:
    import zipfile
    with zipfile.ZipFile(path) as z:
        return {n[:-4]: pd.read_csv(z.open(n), dtype=str) for n in z.namelist() if n.endswith(".txt")}
