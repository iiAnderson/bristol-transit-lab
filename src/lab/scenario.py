"""
Scenario files: schema, validation and ``spec_hash`` (SPEC §5; plans/P3.md P3b-1, D5).

A scenario is a parent plus an ordered list of operations. This module only reads and
checks: it builds nothing. Every rule fails loudly with a ``ScenarioError`` that names
the rule, so a broken scenario never reaches the generator.

What the validator needs to know about the parent's network — which routes exist, which
stops each serves, where the stops are, which timing-point sections exist — comes in as
a ``Catalogue``, so the rules can be tested without a GTFS feed. Ops are checked in
order against a running copy of it: a route removed by one op is gone for the next.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MODES = {"light_rail", "tram", "brt", "metro", "bus", "heavy_rail"}
ALIGNMENT_TYPES = {"at_grade_street", "at_grade_segregated", "elevated", "tunnel", "existing_rail"}
FARE_TYPES = {"flat"}
OPS = {"add_line", "modify_route", "replace_route", "reroute", "remove_route", "add_stop",
       "road_speed_factor", "fare_change", "landuse_delta", "works"}
COMPLEXITY = {"simple", "complex"}                        # RIA electrification rates (params/costs.yaml rail_works)
#: ops that are validated and recorded but change nothing yet, and the phase that will act on them
NOT_YET_MODELLED = {"fare_change": "P5"}
TOP_KEYS = {"id", "parent", "description", "landuse", "ops", "offsets", "sources", "caveats"}
ID_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(-[a-z0-9]+)*$")
SPAN_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


class ScenarioError(ValueError):
    """A scenario broke a rule. ``rule`` is the rule's short name."""

    def __init__(self, rule: str, message: str):
        super().__init__(f"[{rule}] {message}")
        self.rule = rule


@dataclass
class Catalogue:
    """The parent's network as the validator sees it."""
    routes: dict[str, list[str]]                       # route_id → stop ids served (any order)
    stops: dict[str, tuple[float, float]]              # stop_id → (lon, lat)
    sections: set[tuple[str, str]] = field(default_factory=set)   # known timing-point pairs
    periods: set[str] = field(default_factory=lambda: {"AM", "IP", "PM", "OP"})
    extent: tuple[float, float, float, float] | None = None
    accelerations: tuple = ()          # accelerations a speed profile may use (those with a fitted allowance)

    def copy(self) -> "Catalogue":
        return Catalogue({k: list(v) for k, v in self.routes.items()}, dict(self.stops),
                         set(self.sections), set(self.periods), self.extent, self.accelerations)


@dataclass
class Scenario:
    id: str
    parent: str | None
    description: str
    landuse: object
    ops: list[tuple[str, dict]]
    offsets: list[float]
    path: Path
    spec_hash: str
    files: list[Path]
    not_yet_modelled: list[str]
    caveats: list[str] = field(default_factory=list)       # carried onto every output, and by every child


def _req(d: dict, keys: list[str], where: str, rule: str = "required-field") -> None:
    for k in keys:
        if k not in d or d[k] is None:
            raise ScenarioError(rule, f"{where}: `{k}` is required")


def _known(d: dict, allowed: set[str], where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise ScenarioError("unknown-key", f"{where}: unexpected {sorted(extra)}")


def _file(base: Path, rel, where: str) -> Path:
    if not isinstance(rel, str):
        raise ScenarioError("file-missing", f"{where}: expected a file path, got {rel!r}")
    p = (base / rel).resolve()
    if not p.is_file():
        raise ScenarioError("file-missing", f"{where}: {rel} does not exist under {base}")
    return p


def _geojson(p: Path, where: str) -> list[dict]:
    doc = json.loads(p.read_text())
    feats = doc.get("features") if isinstance(doc, dict) else None
    if not feats:
        raise ScenarioError("geometry", f"{where}: {p.name} has no features")
    return feats


def _headways(h, periods: set[str], where: str) -> None:
    if not isinstance(h, dict) or not h:
        raise ScenarioError("headway", f"{where}: headways_min must map period → minutes")
    for per, v in h.items():
        if per not in periods:
            raise ScenarioError("headway", f"{where}: unknown period {per!r} (known: {sorted(periods)})")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ScenarioError("headway", f"{where}: headway for {per} must be positive, got {v!r}")


def _span(s, where: str) -> None:
    m = SPAN_RE.match(s) if isinstance(s, str) else None
    if not m:
        raise ScenarioError("span", f"{where}: span must be \"HH:MM-HH:MM\", got {s!r}")
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    if int(m[2]) > 59 or int(m[4]) > 59 or int(m[1]) > 23:
        raise ScenarioError("span", f"{where}: span {s!r} is not a time of day")
    if b <= a:
        b += 1440                                   # runs past midnight
    if b - a > 1440:
        raise ScenarioError("span", f"{where}: span {s!r} is longer than a day")


def _metres():
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform


def _alignment(p: Path, cat: Catalogue, where: str):
    """The alignment as projected LineStrings with their types; checks types, the
    extent, and that ``existing_rail`` segments name known timing-point sections."""
    from shapely.geometry import shape
    from shapely.ops import transform
    to_m = _metres()
    segs = []
    for i, f in enumerate(_geojson(p, where)):
        g = shape(f["geometry"])
        if g.geom_type != "LineString":
            raise ScenarioError("geometry", f"{where}: feature {i} of {p.name} is a {g.geom_type}, not a LineString")
        t = (f.get("properties") or {}).get("alignment_type")
        if t not in ALIGNMENT_TYPES:
            raise ScenarioError("alignment-type", f"{where}: feature {i} of {p.name} has alignment_type {t!r}; "
                                                  f"one of {sorted(ALIGNMENT_TYPES)}")
        if cat.extent is not None:
            x0, y0, x1, y1 = cat.extent
            bx0, by0, bx1, by1 = g.bounds
            if bx0 < x0 or by0 < y0 or bx1 > x1 or by1 > y1:
                raise ScenarioError("outside-extent", f"{where}: feature {i} of {p.name} leaves the extent")
        if (f.get("properties") or {}).get("speed_profile") is not None:
            _profile(f["properties"]["speed_profile"], cat, f"{where}: feature {i} of {p.name}")
        tp = (f.get("properties") or {}).get("timing_points")
        if t == "existing_rail" and not (isinstance(tp, list) and len(tp) >= 2):
            raise ScenarioError("existing-rail-section",
                                f"{where}: feature {i} of {p.name} is existing_rail and needs "
                                "`timing_points` (two or more, in order)")
        if tp is not None:
            # any alignment type may name timing points (a tunnel or new line the capacity check
            # should see); each consecutive pair must be a known section, so a typo fails here
            if not (isinstance(tp, list) and len(tp) >= 2):
                raise ScenarioError("existing-rail-section", f"{where}: feature {i} of {p.name}: `timing_points` "
                                                             "needs two or more points, in order")
            for a, b in zip(tp, tp[1:]):
                if (a, b) not in cat.sections and (b, a) not in cat.sections:
                    raise ScenarioError("existing-rail-section",
                                        f"{where}: no known section between timing points {a} and {b}")
        segs.append((transform(to_m, g), t))
    return segs


def _stops(p: Path, segs, tol_m: float, where: str) -> list[dict]:
    """New stops from a GeoJSON of Points, each within ``tol_m`` of the alignment."""
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union
    to_m = _metres()
    line = unary_union([g for g, _ in segs])
    out, seen = [], set()
    for i, f in enumerate(_geojson(p, where)):
        g = shape(f["geometry"])
        pr = f.get("properties") or {}
        if g.geom_type != "Point":
            raise ScenarioError("geometry", f"{where}: feature {i} of {p.name} is a {g.geom_type}, not a Point")
        sid = pr.get("stop_id")
        if not sid or sid in seen:
            raise ScenarioError("stop-id", f"{where}: feature {i} of {p.name} needs a unique `stop_id`")
        seen.add(sid)
        d = transform(to_m, g).distance(line)
        if d > tol_m:
            raise ScenarioError("stop-off-alignment",
                                f"{where}: stop {sid} is {d:.0f} m from the alignment (limit {tol_m:g} m)")
        out.append({"stop_id": sid, "lon": g.x, "lat": g.y, "existing": bool(pr.get("existing"))})
    if len(out) < 2:
        raise ScenarioError("stop-id", f"{where}: {p.name} needs at least two stops")
    return out


def _profile(sp_: dict, cat: Catalogue, where: str) -> None:
    _known(sp_, {"max_kmh", "accel_ms2", "dwell_s"}, f"{where}.speed_profile")
    _req(sp_, ["max_kmh", "accel_ms2", "dwell_s"], f"{where}.speed_profile")
    for k, v in sp_.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ScenarioError("speed-profile", f"{where}: speed_profile.{k} must be positive, got {v!r}")
    if cat.accelerations and not any(abs(sp_["accel_ms2"] - a) < 1e-9 for a in cat.accelerations):
        raise ScenarioError("acceleration", f"{where}: accel_ms2 {sp_['accel_ms2']} has no fitted timetable allowance; "
                                            f"use one of {sorted(cat.accelerations)} (params scenario.profile_allowance)")


def _route(cat: Catalogue, op: dict, where: str) -> str:
    _req(op, ["route_id"], where)
    rid = op["route_id"]
    if rid not in cat.routes:
        raise ScenarioError("unknown-route", f"{where}: route {rid!r} does not exist at this point in the ops")
    return rid


def _routes(cat: Catalogue, op: dict, where: str) -> list[str]:
    """The route or routes an op names: `route_id` or a list in `route_ids`, not both."""
    if "route_ids" not in op:
        return [_route(cat, op, where)]
    if "route_id" in op:
        raise ScenarioError("route-ids", f"{where}: give `route_id` or `route_ids`, not both")
    ids = op["route_ids"]
    if not (isinstance(ids, list) and ids and all(isinstance(r, str) for r in ids)) or len(set(ids)) != len(ids):
        raise ScenarioError("route-ids", f"{where}: route_ids must be a list of different route ids")
    for r in ids:
        if r not in cat.routes:
            raise ScenarioError("unknown-route", f"{where}: route {r!r} does not exist at this point in the ops")
    return ids


def _on_route(cat: Catalogue, rid: str, stop: str, where: str) -> None:
    if stop not in cat.routes[rid]:
        raise ScenarioError("stop-not-on-route", f"{where}: stop {stop!r} is not served by route {rid!r}")


def _new_service(op: dict, base: Path, cat: Catalogue, tol_m: float, where: str, files: list[Path],
                 need_line: bool = True) -> tuple[list, list[dict]]:
    """Fields shared by add_line and replace_route: alignment, stops, timing, vehicle."""
    _req(op, ["mode", "alignment", "stops", "headways_min", "span"] + (["name"] if need_line else []), where)
    if op["mode"] not in MODES:
        raise ScenarioError("mode", f"{where}: mode {op['mode']!r}; one of {sorted(MODES)}")
    ap, sp = _file(base, op["alignment"], where), _file(base, op["stops"], where)
    files += [ap, sp]
    segs = _alignment(ap, cat, where)
    stops = _stops(sp, segs, tol_m, where)
    for s in stops:
        if s["existing"] and s["stop_id"] not in cat.stops:
            raise ScenarioError("stop-id", f"{where}: stop {s['stop_id']} is marked existing but is not in the network")
        if not s["existing"] and s["stop_id"] in cat.stops:
            raise ScenarioError("stop-id", f"{where}: stop id {s['stop_id']} is already in the network; "
                                           "mark it `existing: true` to reuse it")
    _headways(op["headways_min"], cat.periods, where)
    _span(op["span"], where)
    needs_profile = any(t != "existing_rail" for _, t in segs)
    sp_ = op.get("speed_profile")
    if needs_profile:
        _req(op, ["speed_profile"], where)
    if sp_ is not None:
        _profile(sp_, cat, where)
    cap = (op.get("vehicle") or {}).get("capacity")
    if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0):
        raise ScenarioError("vehicle", f"{where}: vehicle.capacity must be a positive whole number, got {cap!r}")
    if "fare" in op:
        _fare(op["fare"], where)
    return segs, stops


def _fare(f: dict, where: str) -> None:
    _req(f, ["type", "gbp"], where)
    if f["type"] not in FARE_TYPES:
        raise ScenarioError("fare", f"{where}: fare type {f['type']!r}; one of {sorted(FARE_TYPES)}")
    if not isinstance(f["gbp"], (int, float)) or isinstance(f["gbp"], bool) or f["gbp"] < 0:
        raise ScenarioError("fare", f"{where}: fare must be zero or more, got {f['gbp']!r}")


SERVICE_KEYS = {"route_id", "name", "mode", "alignment", "stops", "speed_profile", "headways_min", "span",
                "vehicle", "fare", "traction", "operator"}


def _points(cat: Catalogue) -> set[str]:
    return {p for pair in cat.sections for p in pair}


def _works_sections(grp: dict, cat: Catalogue, where: str) -> list[str]:
    secs = grp.get("sections")
    if not (isinstance(secs, list) and secs and all(isinstance(x, str) for x in secs)):
        raise ScenarioError("works-section", f"{where}: `sections` must be a list of FROM-TO timing-point pairs")
    for key in secs:
        a, _, b = key.partition("-")
        if not b or ((a, b) not in cat.sections and (b, a) not in cat.sections):
            raise ScenarioError("works-section", f"{where}: {key!r} is not a known section between timing points")
    seen = {frozenset(k.split("-")) for k in secs}
    if len(seen) != len(secs):
        raise ScenarioError("works-section", f"{where}: a section is named twice (a section and its reverse are one track)")
    return secs


def _positive(grp: dict, key: str, where: str, rule: str, whole: bool = False) -> None:
    v = grp.get(key)
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 and (not whole or v == int(v))
    if not ok:
        raise ScenarioError(rule, f"{where}: `{key}` must be a positive {'whole ' if whole else ''}number, got {v!r}")


def _check_works(op: dict, cat: Catalogue, where: str) -> None:
    """Works to existing railway (plans/heavy-rail-programme-log.md G1). Each item names timing-point
    sections that exist in the infrastructure files; lengths are given in km (from
    `lab scenario draw-rail`) because junction timing points have no coordinates."""
    _known(op, {"name", "track", "electrify", "loops", "junctions", "platforms"}, where)
    _req(op, ["name"], where)
    if not any(k in op for k in ("track", "electrify", "loops", "junctions", "platforms")):
        raise ScenarioError("empty-op", f"{where}: works changes nothing")
    pts = _points(cat)
    for g in op.get("track") or []:
        _known(g, {"sections", "was", "tracks", "length_km", "note"}, f"{where}.track")
        _works_sections(g, cat, where)
        _positive(g, "was", where, "works-tracks", whole=True)
        _positive(g, "tracks", where, "works-tracks", whole=True)
        if g["tracks"] <= g["was"]:
            raise ScenarioError("works-tracks", f"{where}: tracks ({g['tracks']}) must exceed was ({g['was']}); "
                                                "works add track, they never remove it")
        _positive(g, "length_km", where, "works-length")
    for g in op.get("electrify") or []:
        _known(g, {"sections", "tracks", "length_km", "complexity", "note"}, f"{where}.electrify")
        _works_sections(g, cat, where)
        _positive(g, "tracks", where, "works-tracks", whole=True)
        _positive(g, "length_km", where, "works-length")
        if g.get("complexity") not in COMPLEXITY:
            raise ScenarioError("works-complexity", f"{where}: electrify needs complexity, one of {sorted(COMPLEXITY)} "
                                                    "(it picks the RIA rate; there is no default)")
    for g in op.get("loops") or []:
        _known(g, {"at", "note"}, f"{where}.loops")
        if g.get("at") not in pts:
            raise ScenarioError("works-point", f"{where}: a passing loop at {g.get('at')!r}: not a known timing point")
    for g in op.get("junctions") or []:
        _known(g, {"at", "type", "note"}, f"{where}.junctions")
        if g.get("at") not in pts:
            raise ScenarioError("works-point", f"{where}: a junction at {g.get('at')!r}: not a known timing point")
        if g.get("type") != "grade_separated":
            raise ScenarioError("works-kind", f"{where}: junction type {g.get('type')!r}; only grade_separated is costed")
    for g in op.get("platforms") or []:
        _known(g, {"at", "kind", "count", "note"}, f"{where}.platforms")
        if g.get("at") not in pts and g.get("at") not in cat.stops:
            raise ScenarioError("works-point", f"{where}: platforms at {g.get('at')!r}: not a known timing point or stop")
        if g.get("kind") != "bay":
            raise ScenarioError("works-kind", f"{where}: platform kind {g.get('kind')!r}; only bay is costed")
        _positive(g, "count", where, "works-tracks", whole=True)


def _check_op(kind: str, op: dict, base: Path, cat: Catalogue, tol_m: float, where: str,
              files: list[Path]) -> None:
    if kind == "add_line":
        _known(op, SERVICE_KEYS, where)
        _req(op, ["route_id"], where)
        if op["route_id"] in cat.routes:
            raise ScenarioError("duplicate-route", f"{where}: route {op['route_id']!r} already exists")
        _, stops = _new_service(op, base, cat, tol_m, where, files)
        cat.routes[op["route_id"]] = [s["stop_id"] for s in stops]
        cat.stops.update({s["stop_id"]: (s["lon"], s["lat"]) for s in stops})
    elif kind == "replace_route":
        _known(op, (SERVICE_KEYS - {"route_id"}) | {"route_id", "route_ids", "new_route_id", "periods"}, where)
        rids = _routes(cat, op, where)
        _req(op, ["new_route_id"], where)
        if op["new_route_id"] in cat.routes:
            raise ScenarioError("duplicate-route", f"{where}: route {op['new_route_id']!r} already exists")
        for per in op.get("periods", []):
            if per not in cat.periods:
                raise ScenarioError("headway", f"{where}: unknown period {per!r}")
        _, stops = _new_service(op, base, cat, tol_m, where, files, need_line=False)
        cat.routes[op["new_route_id"]] = [s["stop_id"] for s in stops]
        cat.stops.update({s["stop_id"]: (s["lon"], s["lat"]) for s in stops})
        if not op.get("periods"):
            for rid in rids:
                del cat.routes[rid]                # replaced all day
    elif kind == "modify_route":
        _known(op, {"route_id", "headways_min", "stopping_pattern", "extend_to", "truncate_at", "fill_span"}, where)
        rid = _route(cat, op, where)
        if not any(k in op for k in ("headways_min", "stopping_pattern", "extend_to", "truncate_at", "fill_span")):
            raise ScenarioError("empty-op", f"{where}: modify_route changes nothing")
        if "fill_span" in op:
            _span(op["fill_span"], where)
        if "headways_min" in op:
            _headways(op["headways_min"], cat.periods, where)
        if "stopping_pattern" in op:
            pat = op["stopping_pattern"]
            _known(pat, {"add", "remove"}, f"{where}.stopping_pattern")
            for s in pat.get("remove", []):
                _on_route(cat, rid, s, where)
            for s in pat.get("add", []):
                if s not in cat.stops:
                    raise ScenarioError("stop-id", f"{where}: stop {s!r} to add is not in the network")
                if s in cat.routes[rid]:
                    raise ScenarioError("stop-id", f"{where}: route {rid!r} already calls at {s!r}")
            cat.routes[rid] = [s for s in cat.routes[rid] if s not in pat.get("remove", [])] + pat.get("add", [])
        if "truncate_at" in op:
            tr = op["truncate_at"]
            if not isinstance(tr, dict):
                raise ScenarioError("truncate", f"{where}: truncate_at is a mapping: stop, tiploc, keep (before or after)")
            _known(tr, {"stop", "tiploc", "keep"}, f"{where}.truncate_at")
            _req(tr, ["stop", "tiploc", "keep"], f"{where}.truncate_at")
            _on_route(cat, rid, tr["stop"], where)
            if tr["keep"] not in ("before", "after"):
                raise ScenarioError("truncate", f"{where}: keep must be 'before' (the trip ends at the stop) or 'after' "
                                                f"(it starts there), got {tr['keep']!r}")
            if tr["tiploc"] not in _points(cat):
                raise ScenarioError("truncate", f"{where}: tiploc {tr['tiploc']!r} is not a known timing point")
        if "extend_to" in op:
            ext = op["extend_to"]
            _known(ext, {"from_stop", "alignment", "stops", "speed_profile", "stand_at_from_stop_s"}, f"{where}.extend_to")
            sd = ext.get("stand_at_from_stop_s")
            if sd is not None and (not isinstance(sd, (int, float)) or isinstance(sd, bool) or sd < 0):
                raise ScenarioError("speed-profile", f"{where}: stand_at_from_stop_s must be zero or more, got {sd!r}")
            if ext.get("speed_profile") is not None:
                _profile(ext["speed_profile"], cat, f"{where}.extend_to")
            _req(ext, ["from_stop", "alignment", "stops"], f"{where}.extend_to")
            _on_route(cat, rid, ext["from_stop"], where)
            ap, sp = _file(base, ext["alignment"], where), _file(base, ext["stops"], where)
            files += [ap, sp]
            segs = _alignment(ap, cat, where)
            stops = _stops(sp, segs, tol_m, where)
            if any(t != "existing_rail" for _, t in segs):
                _req(ext, ["speed_profile"], f"{where}.extend_to")
            if ext["from_stop"] not in {s["stop_id"] for s in stops}:
                raise ScenarioError("stop-off-alignment", f"{where}: the extension's stops must start at "
                                                          f"from_stop {ext['from_stop']!r}")
            cat.routes[rid] += [s["stop_id"] for s in stops if s["stop_id"] not in cat.routes[rid]]
            cat.stops.update({s["stop_id"]: (s["lon"], s["lat"]) for s in stops})
    elif kind == "works":
        _check_works(op, cat, where)
    elif kind == "reroute":
        _known(op, {"route_id", "from_stop", "to_stop", "alignment", "stops", "speed_profile"}, where)
        rid = _route(cat, op, where)
        _req(op, ["from_stop", "to_stop", "alignment"], where)
        for s in (op["from_stop"], op["to_stop"]):
            _on_route(cat, rid, s, where)
        ap = _file(base, op["alignment"], where)
        files.append(ap)
        segs = _alignment(ap, cat, where)
        from shapely.geometry import Point
        from shapely.ops import linemerge, unary_union
        to_m = _metres()
        line = unary_union([g for g, _ in segs])
        line = line if line.geom_type == "LineString" else linemerge(line)
        if line.geom_type != "LineString":
            raise ScenarioError("geometry", f"{where}: the reroute alignment is not one continuous line")
        ends = [Point(line.coords[0]), Point(line.coords[-1])]
        for s in (op["from_stop"], op["to_stop"]):
            pt = Point(*to_m(*cat.stops[s]))
            if min(pt.distance(e) for e in ends) > tol_m:
                raise ScenarioError("reroute-ends", f"{where}: the alignment does not end at stop {s!r} "
                                                    f"(limit {tol_m:g} m)")
        if any(t != "existing_rail" for _, t in segs):
            _req(op, ["speed_profile"], where)
        if op.get("speed_profile") is not None:
            _profile(op["speed_profile"], cat, where)
        if "stops" in op:
            sp = _file(base, op["stops"], where)
            files.append(sp)
            _stops(sp, segs, tol_m, where)
    elif kind == "remove_route":
        _known(op, {"route_id"}, where)
        del cat.routes[_route(cat, op, where)]
    elif kind == "add_stop":
        _known(op, {"route_id", "after_stop", "stop"}, where)
        rid = _route(cat, op, where)
        _req(op, ["after_stop", "stop"], where)
        _on_route(cat, rid, op["after_stop"], where)
        st = op["stop"]
        _req(st, ["stop_id", "name", "lon", "lat"], f"{where}.stop")
        if st["stop_id"] in cat.stops:
            raise ScenarioError("stop-id", f"{where}: stop id {st['stop_id']} is already in the network")
        if cat.extent is not None:
            x0, y0, x1, y1 = cat.extent
            if not (x0 <= st["lon"] <= x1 and y0 <= st["lat"] <= y1):
                raise ScenarioError("outside-extent", f"{where}: stop {st['stop_id']} is outside the extent")
        cat.stops[st["stop_id"]] = (st["lon"], st["lat"])
        cat.routes[rid].append(st["stop_id"])
    elif kind == "road_speed_factor":
        _known(op, {"links", "applies_to", "factor"}, where)
        _req(op, ["links", "applies_to", "factor"], where)
        files.append(_file(base, op["links"], where))
        bad = set(op["applies_to"]) - MODES
        if bad or not op["applies_to"]:
            raise ScenarioError("mode", f"{where}: applies_to {sorted(bad) or '[]'}; modes are {sorted(MODES)}")
        if not isinstance(op["factor"], (int, float)) or isinstance(op["factor"], bool) or op["factor"] <= 0:
            raise ScenarioError("factor", f"{where}: factor must be positive, got {op['factor']!r}")
    elif kind == "fare_change":
        _known(op, {"modes", "type", "gbp"}, where)
        _req(op, ["modes"], where)
        bad = set(op["modes"]) - MODES
        if bad or not op["modes"]:
            raise ScenarioError("mode", f"{where}: modes {sorted(bad) or '[]'}; modes are {sorted(MODES)}")
        _fare(op, where)
    elif kind == "landuse_delta":
        _known(op, {"file"}, where)
        _req(op, ["file"], where)
        files.append(_file(base, op["file"], where))


def phase_fractions(offsets, default_count: int) -> list[float]:
    """Offsets as fractions of the headway, shared by every generated service of a
    scenario (plans/P3.md D5 as decided): offset k shifts each generated service by the
    same fraction of its own headway. Default: ``default_count`` evenly spread."""
    if offsets is None:
        return [k / default_count for k in range(default_count)]
    if not isinstance(offsets, list) or not offsets:
        raise ScenarioError("offsets", "offsets must be a non-empty list of fractions of the headway")
    for v in offsets:
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v < 1:
            raise ScenarioError("offsets", f"offset {v!r} is not a fraction in [0, 1)")
    if len(set(offsets)) != len(offsets):
        raise ScenarioError("offsets", "offsets repeat")
    return [float(v) for v in offsets]


def spec_hash(path: Path, files: list[Path], base: Path) -> str:
    """Hash of the scenario's YAML (parsed, so formatting and comments do not count)
    plus the bytes of every file it references, keyed by path relative to ``base``."""
    h = hashlib.sha256()
    h.update(json.dumps(yaml.safe_load(path.read_text()), sort_keys=True, default=str).encode())
    for f in sorted(set(files)):
        h.update(str(f.relative_to(base.resolve())).encode())
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def load(path: Path, cat: Catalogue, *, stop_tolerance_m: float, offset_count: int,
         known_scenarios: set[str] | None = None) -> Scenario:
    """Read and validate one scenario file against its parent's network. ``path`` sits in
    the scenarios directory, which is also the base for the files it references."""
    base = path.parent
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ScenarioError("required-field", f"{path.name} is not a mapping")
    _known(doc, TOP_KEYS, path.name)
    _req(doc, ["id", "description"], path.name)
    if "parent" not in doc:
        raise ScenarioError("required-field", f"{path.name}: `parent` is required (null for a root baseline)")
    sid = doc["id"]
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise ScenarioError("id", f"{path.name}: id {sid!r} must look like B2026 or S014-airport-lrt")
    if path.stem != sid:
        raise ScenarioError("id", f"{path.name}: id {sid!r} does not match the file name")
    parent = doc["parent"]
    if parent is not None:
        if parent == sid:
            raise ScenarioError("parent", f"{sid}: a scenario cannot be its own parent")
        exists = (parent in known_scenarios) if known_scenarios is not None else (base / f"{parent}.yaml").is_file()
        if not exists:
            raise ScenarioError("parent", f"{sid}: parent {parent!r} has no scenario file")
    ops_doc = doc.get("ops") or []
    if parent is None and ops_doc:
        raise ScenarioError("parent", f"{sid}: a root baseline has no ops")
    if not isinstance(ops_doc, list):
        raise ScenarioError("op", f"{sid}: ops must be a list")
    files: list[Path] = []
    work = cat.copy()
    ops, later = [], []
    for i, item in enumerate(ops_doc):
        if not isinstance(item, dict) or len(item) != 1:
            raise ScenarioError("op", f"{sid} op {i + 1}: each op is one `type: {{…}}` mapping")
        (kind, body), = item.items()
        if kind not in OPS:
            raise ScenarioError("op", f"{sid} op {i + 1}: unknown op {kind!r}; one of {sorted(OPS)}")
        if not isinstance(body, dict):
            raise ScenarioError("op", f"{sid} op {i + 1} ({kind}): the op's body must be a mapping")
        where = f"{sid} op {i + 1} ({kind})"
        if kind == "modify_route" and "route_ids" in body:
            # one modify_route over several routes is one op per route, so every later stage
            # (generator, capacity, cost) sees the same list of ops
            ids = _routes(work, body, where)
            bad = [k for k in ("extend_to", "fill_span", "truncate_at") if k in body]
            if bad:
                raise ScenarioError("route-ids", f"{where}: {bad} differ by route and cannot be given with route_ids")
            for rid in ids:
                one = {k: v for k, v in body.items() if k != "route_ids"} | {"route_id": rid}
                _check_op(kind, one, base, work, stop_tolerance_m, where, files)
                ops.append((kind, one))
            continue
        _check_op(kind, body, base, work, stop_tolerance_m, where, files)
        ops.append((kind, body))
        if kind in NOT_YET_MODELLED:
            later.append(f"op {i + 1} ({kind}): recorded, not yet modelled (arrives in {NOT_YET_MODELLED[kind]})")
    lu = doc.get("landuse")
    for item in (lu if isinstance(lu, list) else []):
        if isinstance(item, str) and item.endswith((".yaml", ".yml")):
            files.append(_file(base, item, f"{sid} landuse"))
    caveats = doc.get("caveats") or []
    if not isinstance(caveats, list) or not all(isinstance(c, str) and c.strip() for c in caveats):
        raise ScenarioError("caveats", f"{sid}: caveats must be a list of sentences")
    return Scenario(sid, parent, doc["description"], lu, ops, phase_fractions(doc.get("offsets"), offset_count),
                    path, spec_hash(path, files, base), sorted(set(files)), later, [" ".join(c.split()) for c in caveats])


def catalogue_from_gtfs(feeds: list[dict], sections: set[tuple[str, str]], periods: set[str],
                        extent, accelerations: tuple = ()) -> Catalogue:
    """``feeds``: GTFS tables as frames (stops, trips, stop_times), as ``coverage.read_gtfs`` gives."""
    routes: dict[str, list[str]] = {}
    stops: dict[str, tuple[float, float]] = {}
    for f in feeds:
        st = f["stop_times"].merge(f["trips"][["trip_id", "route_id"]], on="trip_id")
        for rid, g in st.groupby("route_id"):
            routes[rid] = sorted(set(g.stop_id))
        for r in f["stops"].itertuples():
            stops[r.stop_id] = (float(r.stop_lon), float(r.stop_lat))
    return Catalogue(routes, stops, sections, periods, extent, tuple(accelerations))
