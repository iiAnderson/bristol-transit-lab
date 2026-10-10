"""
Track-capacity check (plans/P3.md D6; SPEC §11 item 20).

It counts trains per hour over each section between Darwin timing points — today's
trains from the timing-point table, a ``[MODELLED]`` freight allowance where freight is
known to run, and the scenario's generated or extended services — and compares the
busiest hour of each period with a ``[MODELLED]`` limit. **It catches overloads; it does
not prove a timetable works.** Darwin holds no freight, so freight is an allowance, not a
count; junction conflicts are not assessed.
"""
from __future__ import annotations

import math

import pandas as pd


def base_use(tp: pd.DataFrame, removed_rids: set[str] | None = None) -> pd.DataFrame:
    """Trains over each directed section in each clock hour: every journey in the
    timing-point table that uses track (so empty stock and charters count; buses and
    ships do not; journeys cancelled as a whole do not), by ``from``, ``to``, ``hour``."""
    t = tp[~tp.journey_cancelled & ~tp.exclusion.fillna("").str.startswith(("road", "water"))]
    if removed_rids:
        t = t[~t.rid.isin(removed_rids)]
    t = t.sort_values(["rid", "seq"])
    nxt = t.groupby("rid").tpl.shift(-1)
    s = t.assign(to=nxt).dropna(subset=["to", "dep_s"])
    s = s[s.tpl != s.to]
    s["hour"] = (s.dep_s // 3600).astype(int) % 24
    return s.groupby(["tpl", "to", "hour"]).size().rename("trains").reset_index().rename(columns={"tpl": "from"})


def scenario_use(feeds: dict, op_reports: list[dict]) -> pd.DataFrame:
    """Trains over each directed section in each clock hour from the scenario's own
    services: each trip of a generated route, and each extended trip over its extension,
    takes the timing points of every leg it runs, timed at its departure into the leg."""
    from .generator import mins
    rows = []
    jobs = []
    for op in op_reports:
        if "legs" in op:                                           # add_line / replace_route
            jobs.append(("generated", op["route_id"], op["legs"]))
        if "extend_to" in op:
            jobs.append((None, op["route_id"], op["extend_to"]["legs"]))
    for feed_name, rid, legs in jobs:
        name = feed_name or next(n for n, f in feeds.items() if rid in set(f["routes"].route_id))
        f = feeds[name]
        trips = set(f["trips"].trip_id[f["trips"].route_id == rid])
        st = f["stop_times"][f["stop_times"].trip_id.isin(trips)].copy()
        st["seq"] = st.stop_sequence.astype(int)
        st = st.sort_values(["trip_id", "seq"])
        st["nxt"] = st.groupby("trip_id").stop_id.shift(-1)
        st["hour"] = (mins(st.departure_time) // 60).astype(int) % 24
        for lg in legs:
            tps = lg.get("timing_points") or []
            for (a, b), seq in (((lg["from"], lg["to"]), tps), ((lg["to"], lg["from"]), tps[::-1])):
                hit = st[(st.stop_id == a) & (st.nxt == b)]
                for x, y in zip(seq, seq[1:]):
                    for h, n in hit.hour.value_counts().items():
                        rows.append({"from": x, "to": y, "hour": int(h), "trains": int(n), "route_id": rid})
    if not rows:
        return pd.DataFrame(columns=["from", "to", "hour", "trains"])
    return pd.DataFrame(rows).groupby(["from", "to", "hour"]).trains.sum().reset_index()


def limits(sections: list[dict], cap: dict, base: pd.DataFrame, run_min: dict | None = None) -> pd.DataFrame:
    """The limit, freight allowance and track count of every section. ``cap`` is the
    capacity data file: ``defaults`` (tracks, double_track_tph, single_track_margin_min),
    ``sections`` (overrides keyed "FROM-TO": tracks, limit_tph, freight_tph, note) and
    ``freight`` (lists of sections sharing one allowance). A double-track section takes
    the default limit; a single-track one 60 ÷ (2 × run time + 2 × margin), from
    ``run_min`` (minutes over the section) where known; and no limit is set below the
    busiest hour observed today."""
    d = cap["defaults"]
    over = cap.get("sections") or {}
    freight = {}
    for grp in cap.get("freight") or []:
        for key in grp["sections"]:
            freight[key] = grp["tph"]
    busiest = base.groupby(["from", "to"]).trains.max()
    rows = []
    for s in sections:
        key = f"{s['from']}-{s['to']}"
        o = over.get(key, {})
        tracks = o.get("tracks", d["tracks"])
        if "limit_tph" in o:
            lim, how = o["limit_tph"], "set for this section"
        elif tracks == 1:
            rt = (run_min or {}).get((s["from"], s["to"]))
            if rt is None:
                lim, how = d["single_track_default_tph"], "single track, default (no run time known)"
            else:
                lim = math.floor(60 / (2 * rt + 2 * d["single_track_margin_min"]))
                how = f"single track: 60 ÷ (2 × {rt:.1f} + 2 × {d['single_track_margin_min']:g}) min"
        else:
            lim, how = d["double_track_tph"], "double track, default"
        seen = int(busiest.get((s["from"], s["to"]), 0))
        if seen > lim:
            lim, how = seen, f"raised to the busiest hour observed today ({how} gave less)"
        rows.append({"from": s["from"], "to": s["to"], "tracks": tracks, "limit_tph": lim, "limit_basis": how,
                     "freight_allowance_tph": freight.get(key, o.get("freight_tph", 0)),
                     "timed_today": bool(s.get("timed", True))})
    return pd.DataFrame(rows)


def check(lim: pd.DataFrame, base: pd.DataFrame, scen: pd.DataFrame, periods: dict[str, tuple[float, float]],
          only_used: bool = True) -> pd.DataFrame:
    """One row per section and period: the busiest hour's trains today, the freight
    allowance, the scenario's trains in its own busiest hour, their sum against the
    limit. The sum takes each part's busiest hour, so it errs towards flagging."""
    rows = []
    b = base.set_index(["from", "to", "hour"]).trains
    s = scen.set_index(["from", "to", "hour"]).trains if len(scen) else pd.Series(dtype=float)
    used = set(zip(scen["from"], scen["to"])) if len(scen) else set()
    for r in lim.itertuples():
        if only_used and (r[1], r.to) not in used:
            continue
        for per, (lo, hi) in periods.items():
            hours = range(int(lo // 60), int(math.ceil(hi / 60)))
            bb = max((int(b.get((r[1], r.to, h), 0)) for h in hours), default=0)
            ss = max((int(s.get((r[1], r.to, h), 0)) for h in hours), default=0)
            tot = bb + ss + r.freight_allowance_tph
            rows.append({"from": r[1], "to": r.to, "period": per, "tracks": r.tracks, "base_tph": bb,
                         "freight_allowance_tph": r.freight_allowance_tph, "scenario_tph": ss, "total_tph": tot,
                         "limit_tph": r.limit_tph, "over": bool(tot > r.limit_tph), "limit_basis": r.limit_basis,
                         "timed_today": r.timed_today})
    return pd.DataFrame(rows)
