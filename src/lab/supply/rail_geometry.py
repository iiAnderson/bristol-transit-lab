"""Rail alignments from OSM: the track between given points, routed along railway ways,
for drawing `existing_rail` segments of a scenario (plans/P3.md P3b-4). An authoring aid:
its output is a GeoJSON file under ``scenarios/``, which is what a scenario reads."""
from __future__ import annotations

import math


def rail_graph(pbf: str, kinds: set[str]):
    """Undirected graph of railway ways whose ``railway`` tag is in ``kinds`` (service
    sidings and yards left out), nodes keyed by OSM id with lon/lat, edges in metres."""
    import networkx as nx
    import osmium

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.g = nx.Graph()

        def way(self, w):
            t = w.tags
            if t.get("railway") not in kinds or t.get("service") in ("siding", "yard", "spur"):
                return
            try:
                pts = [(n.ref, n.lon, n.lat) for n in w.nodes]
            except Exception:
                return
            for (a, x0, y0), (b, x1, y1) in zip(pts, pts[1:]):
                d = math.hypot((x1 - x0) * 111_320 * math.cos(math.radians(y0)), (y1 - y0) * 111_320)
                self.g.add_node(a, lon=x0, lat=y0)
                self.g.add_node(b, lon=x1, lat=y1)
                self.g.add_edge(a, b, m=d, way=w.id, kind=t.get("railway"))

    h = H()
    h.apply_file(pbf, locations=True)
    return h.g


def near_nodes(g, lon: float, lat: float, within_m: float) -> list[tuple[int, float]]:
    """Track nodes within ``within_m`` of a point, nearest first (always at least the nearest)."""
    out = []
    for n, d in g.nodes(data=True):
        m = math.hypot((d["lon"] - lon) * 111_320 * math.cos(math.radians(lat)), (d["lat"] - lat) * 111_320)
        out.append((m, n))
    out.sort()
    keep = [(n, m) for m, n in out if m <= within_m] or [(out[0][1], out[0][0])]
    return keep


def path(g, waypoints: list[tuple[float, float]], max_snap_m: float, choice_m: float = 60.0) -> dict:
    """The shortest track path through the waypoints in order. A station sits beside
    several parallel tracks, so each waypoint may be met at any track node within
    ``choice_m`` of it (or its nearest node, which must be within ``max_snap_m``); the
    path takes whichever gives the shortest run. Returns coordinates, length, how far
    each waypoint was from the node used, and the metres of each ``railway`` kind."""
    import networkx as nx
    cands = []
    for lon, lat in waypoints:
        c = near_nodes(g, lon, lat, choice_m)
        if c[0][1] > max_snap_m:
            raise ValueError(f"waypoint {lon},{lat} is {c[0][1]:.0f} m from the nearest track (limit {max_snap_m:g} m)")
        cands.append(dict(c))
    # dynamic programme over the candidate nodes of successive waypoints
    best = {n: (0.0, [n]) for n in cands[0]}
    for nxt in cands[1:]:
        new = {}
        for a, (da, pa) in best.items():
            dist, paths = nx.single_source_dijkstra(g, a, weight="m")
            for b in nxt:
                if b in dist and (b not in new or da + dist[b] < new[b][0]):
                    new[b] = (da + dist[b], pa + paths[b][1:])
        if not new:
            raise ValueError("no track path between two of the waypoints")
        best = new
    _, seq = min(best.values(), key=lambda v: v[0])
    snaps = []
    for c in cands:
        hit = [n for n in seq if n in c]
        snaps.append(round(min(c[n] for n in hit), 1))
    kinds: dict[str, float] = {}
    for a, b in zip(seq, seq[1:]):
        e = g.edges[a, b]
        kinds[e["kind"]] = kinds.get(e["kind"], 0.0) + e["m"]
    return {"coords": [[round(g.nodes[n]["lon"], 6), round(g.nodes[n]["lat"], 6)] for n in seq],
            "length_m": round(sum(kinds.values()), 1), "snap_m": snaps,
            "kinds_m": {k: round(v, 1) for k, v in kinds.items()},
            "ways": sorted({g.edges[a, b]["way"] for a, b in zip(seq, seq[1:])})}
