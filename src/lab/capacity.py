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


def section_run_times(tp: pd.DataFrame) -> dict[tuple[str, str], float]:
    """Median minutes between consecutive timing points over today's trains that use track:
    departure from one point to arrival at the next, so dwell at the first is not in it."""
    t = tp[~tp.journey_cancelled & ~tp.exclusion.fillna("").str.startswith(("road", "water"))]
    t = t.sort_values(["rid", "seq"])
    g = t.groupby("rid")
    x = t.assign(to=g.tpl.shift(-1), to_arr=g.arr_s.shift(-1)).dropna(subset=["to", "dep_s", "to_arr"])
    x = x[x.tpl != x.to]
    run = ((x.to_arr - x.dep_s) / 60).groupby([x.tpl, x.to]).median()
    return {k: float(v) for k, v in run.items()}


class CapacityError(ValueError):
    pass


def _key(a: str, b: str) -> str:
    return f"{a}-{b}"


def block_limits(cap: dict, runs: dict[tuple[str, str], float]) -> dict[str, dict]:
    """Single-track limits by block (plans/heavy-rail-programme-log.md G2). A block is a run of
    consecutive timing points between passing places (``blocks.<name>.points``): trains
    in opposite directions cannot pass inside it, so its limit is one train pair per
    round trip over the whole block,

        limit = floor(60 ÷ (2 × (run + dwell at the stations inside) + 2 × margin)) per direction,

    from the summed ``runs`` (minutes; Darwin medians, replaced by a scenario leg's own run time
    where it has one) of the block's sections. Every section of the block, in both directions,
    takes the block's limit. A missing run time is an error, not a guess."""
    d = cap["defaults"]
    margin = d["single_track_margin_min"]
    dwell = d.get("block_station_dwell_min", 0.0)
    out = {}
    for name, b in (cap.get("blocks") or {}).items():
        pts = b["points"]
        fwd = [(a, c) for a, c in zip(pts, pts[1:])]
        run = 0.0
        for a, c in fwd:
            r = [runs[k] for k in ((a, c), (c, a)) if k in runs]
            if not r:
                raise CapacityError(f"block {name}: no run time known for the section {a}–{c}")
            run += sum(r) / len(r)
        inner = dwell * (len(pts) - 2)
        lim = math.floor(60 / (2 * (run + inner) + 2 * margin) + 1e-9)
        how = (f"single-track block {name} ({pts[0]} to {pts[-1]}): 60 ÷ (2 × ({run:.1f} + {inner:.1f} dwell) "
               f"+ 2 × {margin:g}) min")
        for a, c in fwd:
            for k in (_key(a, c), _key(c, a)):
                out[k] = {"limit": lim, "how": how, "block": name}
    return out


def apply_works(cap: dict, works: list[dict]) -> dict:
    """The capacity data a scenario sees: ``cap`` with its chain's ``works`` applied, in order.

    ``works`` items (from the generator's reports) carry ``track`` (groups of sections and a
    new track count), ``loops`` (a passing loop at a timing point) — the rest (``electrify``,
    ``junction``, ``platforms``) costs but does not change capacity here. Redoubling a section
    takes it out of its single-track block and the rest of the block is split where it
    was; a loop splits a block at its point. Nothing may reduce a track count."""
    import copy
    new = copy.deepcopy(cap)
    d = new["defaults"]
    secs = new.setdefault("sections", {})
    blocks = new.setdefault("blocks", {})

    def block_keys() -> set[str]:
        return {k for b in blocks.values() for x, y in zip(b["points"], b["points"][1:]) for k in (_key(x, y), _key(y, x))}

    def current(a: str, c: str) -> int:
        k = _key(a, c)
        if k in block_keys() and "tracks" not in secs.get(k, {}):
            return 1                                    # inside a single-track block
        return secs.get(k, {}).get("tracks", d["tracks"])

    for w in works:
        for grp in w.get("track") or []:
            for key in grp["sections"]:
                a, c = key.split("-")
                if grp["tracks"] < current(a, c):
                    raise CapacityError(f"works {w.get('name')}: {key} has {current(a, c)} tracks; works may not reduce that to {grp['tracks']}")
                for k in (key, _key(c, a)):
                    secs.setdefault(k, {})["tracks"] = grp["tracks"]
        for lp in w.get("loops") or []:
            hit = [(n, b) for n, b in blocks.items() if lp["at"] in b["points"][1:-1]]
            if len(hit) != 1:
                raise CapacityError(f"works {w.get('name')}: a passing loop at {lp['at']} must lie inside exactly one "
                                    f"single-track block; it lies inside {len(hit)}")
            n, b = hit[0]
            i = b["points"].index(lp["at"])
            del blocks[n]
            blocks[f"{n}/a"], blocks[f"{n}/b"] = {"points": b["points"][:i + 1]}, {"points": b["points"][i:]}
        # redoubled sections leave their blocks; the rest of a block stays single track between the gaps
        for n, b in list(blocks.items()):
            pts, runs_, cur = b["points"], [], []
            for a, c in zip(pts, pts[1:]):
                if secs.get(_key(a, c), {}).get("tracks", 1) >= 2:
                    if cur:
                        runs_.append(cur)
                    cur = []
                else:
                    cur = cur + [a] if not cur else cur
                    cur.append(c)
            if cur:
                runs_.append(cur)
            if runs_ != [pts]:
                del blocks[n]
                for j, r in enumerate(runs_):
                    blocks[f"{n}/{j + 1}"] = {"points": r}
    return new


def copied_use(tp: pd.DataFrame, copies: list[dict]) -> pd.DataFrame:
    """Trains over each section and hour from journeys a scenario adds as copies of
    today's (``modify_route.fill_span``): each copy runs the copied journey's timing
    points, moved by its shift. A copy of a journey Darwin does not hold is an error."""
    if not copies:
        return pd.DataFrame(columns=["from", "to", "hour", "trains"])
    parts = []
    have = set(tp.rid)
    for i, c in enumerate(copies):
        rid = str(c["of"]).split("#")[0]
        if rid not in have:
            raise ValueError(f"added journey copies {c['of']}, which is not in the timing-point table")
        x = tp[tp.rid == rid].copy()
        if c.get("trim"):                       # a truncated journey: only the part that still runs
            x = x.sort_values("seq")
            hit = x[x.tpl == c["trim"]["tiploc"]]
            if hit.empty:
                raise ValueError(f"journey {rid} does not pass timing point {c['trim']['tiploc']}")
            cut = int(hit.seq.iloc[0])
            x = x[x.seq <= cut] if c["trim"]["keep"] == "before" else x[x.seq >= cut]
        x["rid"] = f"copy{i}"
        for col in ("arr_s", "dep_s"):
            x[col] = x[col] + c["shift_min"] * 60
        parts.append(x)
    return base_use(pd.concat(parts, ignore_index=True))


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
        if st.empty:
            continue                      # replaced or removed by a later op: it no longer runs
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


def limits(sections: list[dict], cap: dict, base: pd.DataFrame, run_min: dict | None = None,
           tp_runs: dict | None = None) -> pd.DataFrame:
    """The limit, freight allowance and track count of every section. ``cap`` is the
    capacity data file: ``defaults`` (tracks, double_track_tph, single_track_margin_min),
    ``sections`` (overrides keyed "FROM-TO": tracks, limit_tph, freight_tph, note) and
    ``freight`` (lists of sections sharing one allowance). A double-track section takes
    the default limit; a single-track one 60 ÷ (2 × run time + 2 × margin), from
    ``run_min`` (minutes over the section) where known; and no limit is set below what the
    section carries today — the busiest hour in ``base``, which must be today's trains
    before the scenario removes any, plus the freight allowance."""
    d = cap["defaults"]
    over = cap.get("sections") or {}
    blk = block_limits(cap, {**(tp_runs or {}), **(run_min or {})}) if cap.get("blocks") else {}
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
        if key in blk and "limit_tph" not in o:
            tracks = 1                                  # a block is single track by definition
        if "limit_tph" in o:
            lim, how = o["limit_tph"], "set for this section"
        elif key in blk and tracks == 1:
            lim, how = blk[key]["limit"], blk[key]["how"]
        elif tracks == 1:
            rt = (run_min or {}).get((s["from"], s["to"]))
            if rt is None:
                lim, how = d["single_track_default_tph"], "single track, default (no run time known)"
            else:
                lim = math.floor(60 / (2 * rt + 2 * d["single_track_margin_min"]))
                how = f"single track: 60 ÷ (2 × {rt:.1f} + 2 × {d['single_track_margin_min']:g}) min"
        else:
            lim, how = d["double_track_tph"], "double track, default"
        fr = freight.get(key, o.get("freight_tph", 0))
        seen = int(busiest.get((s["from"], s["to"]), 0)) + fr        # what the section carries today
        track_lim, limit_from = lim, "modelled track capacity"
        if seen > lim:
            lim, how = seen, f"raised to today's busiest hour plus the freight allowance ({how} gave less)"
            # the floor binds: the check on this section measures "busier than today", not capacity
            limit_from = "today's busiest hour (floor)"
        rows.append({"from": s["from"], "to": s["to"], "tracks": tracks, "limit_tph": lim, "limit_basis": how,
                     "limit_from": limit_from, "modelled_track_tph": track_lim, "busiest_today_tph": seen,
                     "freight_allowance_tph": fr,
                     "timed_today": bool(s.get("timed", True))})
    return pd.DataFrame(rows)


def check(lim: pd.DataFrame, base: pd.DataFrame, scen: pd.DataFrame, periods: dict[str, tuple[float, float]],
          only_used: bool = True) -> pd.DataFrame:
    """One row per section and period, for the period's busiest hour of the total: the
    trains still running from today's timetable in that hour, the freight allowance, the
    scenario's trains in that hour, and their sum against the limit."""
    rows = []
    b = base.set_index(["from", "to", "hour"]).trains
    s = scen.set_index(["from", "to", "hour"]).trains if len(scen) else pd.Series(dtype=float)
    used = set(zip(scen["from"], scen["to"])) if len(scen) else set()
    for r in lim.itertuples():
        if only_used and (r[1], r.to) not in used:
            continue
        for per, (lo, hi) in periods.items():
            hours = range(int(lo // 60), int(math.ceil(hi / 60)))
            h_max = max(hours, key=lambda h: int(b.get((r[1], r.to, h), 0)) + int(s.get((r[1], r.to, h), 0)))
            bb, ss = int(b.get((r[1], r.to, h_max), 0)), int(s.get((r[1], r.to, h_max), 0))
            tot = bb + ss + r.freight_allowance_tph
            rows.append({"from": r[1], "to": r.to, "period": per, "hour": h_max, "tracks": r.tracks, "base_tph": bb,
                         "freight_allowance_tph": r.freight_allowance_tph, "scenario_tph": ss, "total_tph": tot,
                         "limit_tph": r.limit_tph, "over": bool(tot > r.limit_tph), "limit_from": r.limit_from,
                         "modelled_track_tph": r.modelled_track_tph, "busiest_today_tph": r.busiest_today_tph,
                         "limit_basis": r.limit_basis,
                         "timed_today": r.timed_today})
    return pd.DataFrame(rows)
