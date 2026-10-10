"""
Walk-network faults at paired stops (plans/P3.md Q15 and the P3a / P3b stop decisions).

For each stop pair the barrier test cut from one cluster: the OSM ways round the two
stops, a classification from their tags, a plot for review, and — for the pairs that
look like artefacts of separately mapped paths — a drafted link between the two stops'
nearest walkable ways. Nothing here changes the network: the draft is a file to approve.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROADS = {"primary", "trunk", "secondary", "tertiary", "unclassified", "residential",
         "primary_link", "trunk_link", "secondary_link", "living_street", "service"}
PATHS = {"footway", "cycleway", "pedestrian", "path", "steps"}
DIVIDING = {"primary", "trunk", "primary_link", "trunk_link", "secondary", "motorway"}


def ways_near(pbf: str, boxes: list[tuple[float, float, float, float]]) -> list[dict]:
    """Highway ways (id, version, timestamp, tags, lon/lat points) touching any box."""
    import osmium

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.out = []

        def way(self, w):
            if "highway" not in w.tags:
                return
            try:
                pts = [(n.lon, n.lat) for n in w.nodes]
            except Exception:           # a node outside the extract
                return
            if len(pts) < 2:
                return
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if any(max(xs) >= b[0] and min(xs) <= b[2] and max(ys) >= b[1] and min(ys) <= b[3] for b in boxes):
                self.out.append({"id": w.id, "version": w.version, "timestamp": str(w.timestamp)[:10],
                                 "tags": dict(w.tags), "pts": pts})

    h = H()
    h.apply_file(pbf, locations=True)
    return h.out


def walkable(tags: dict) -> bool:
    """A rough stand-in for what R5 lets a pedestrian use: no private or forbidden access."""
    return tags.get("foot") not in ("no", "private") and tags.get("access") not in ("no", "private") \
        or tags.get("foot") in ("yes", "designated", "permissive")


def classify(near: list[dict]) -> str:
    """``near``: ways within 20 m of either stop."""
    if any(w["tags"]["highway"] in DIVIDING and (w["tags"].get("oneway") == "yes" or w["tags"].get("foot") == "no")
           for w in near):
        return "divided road"
    if any(w["tags"]["highway"] in PATHS for w in near):
        return "separately mapped path"
    return "unclear"


def analyse(pairs, stops, pbf: str, pad_deg: float = 0.0012) -> list[dict]:
    """``pairs``: frame with a, b, dist_m, walk_min; ``stops``: frame indexed by stop id
    with stop_name, lon, lat. One record per pair, with a drafted link for artefacts."""
    from pyproj import Transformer
    from shapely.geometry import LineString, Point
    from shapely.ops import nearest_points
    fwd = Transformer.from_crs(4326, 27700, always_xy=True)
    back = Transformer.from_crs(27700, 4326, always_xy=True)
    boxes = []
    for r in pairs.itertuples():
        lo, hi = sorted((stops.lon[r.a], stops.lon[r.b]))
        la, lb = sorted((stops.lat[r.a], stops.lat[r.b]))
        boxes.append((lo - pad_deg, la - pad_deg * 0.65, hi + pad_deg, lb + pad_deg * 0.65))
    ways = ways_near(pbf, boxes)
    for w in ways:
        w["geom"] = LineString([fwd.transform(*p) for p in w["pts"]])
    out = []
    for r, box in zip(pairs.itertuples(), boxes):
        pa, pb = (Point(*fwd.transform(stops.lon[i], stops.lat[i])) for i in (r.a, r.b))
        near = [w for w in ways if min(w["geom"].distance(pa), w["geom"].distance(pb)) <= 20]
        kind = classify(near)
        rec = {"a": r.a, "b": r.b, "name": stops.stop_name[r.a], "dist_m": float(r.dist_m),
               "walk_min": None if np.isnan(r.walk_min) else float(r.walk_min), "kind": kind, "box": box,
               "ways": [{k: w[k] for k in ("id", "version", "timestamp", "tags", "pts")} for w in ways
                        if w["geom"].distance(pa) <= 120 or w["geom"].distance(pb) <= 120],
               "stop_xy": [(stops.lon[r.a], stops.lat[r.a]), (stops.lon[r.b], stops.lat[r.b])]}
        if kind == "separately mapped path":
            # At each stop, a short connector between the path it stands on and the
            # carriageway beside it: where the map lacks the link a person crossing uses.
            links = []
            for pt in (pa, pb):
                ok = [w for w in near if walkable(w["tags"]) and w["geom"].distance(pt) <= 25]
                paths = [w for w in ok if w["tags"]["highway"] in PATHS]
                roads = [w for w in ok if w["tags"]["highway"] not in PATHS]
                if not (paths and roads):
                    continue
                wp = min(paths, key=lambda w: w["geom"].distance(pt))
                wr = min(roads, key=lambda w: w["geom"].distance(pt))
                qp = nearest_points(wp["geom"], pt)[0]
                qr = nearest_points(wr["geom"], qp)[0]
                if qp.distance(qr) >= 0.5:
                    links.append({"path_way": wp["id"], "road_way": wr["id"],
                                  "length_m": round(qp.distance(qr), 1),
                                  "coords": [back.transform(qp.x, qp.y), back.transform(qr.x, qr.y)]})
            rec["links"] = links
            if not links:
                rec["note"] = "no path and road within 25 m of either stop; nothing drafted"
        out.append(rec)
    return out


def draft_patch(recs: list[dict], extract_date: str) -> dict:
    """GeoJSON of proposed footway links: a path-to-carriageway connector at each stop
    of an artefact pair."""
    feats = [{"type": "Feature",
              "geometry": {"type": "LineString", "coordinates": [list(c) for c in ln["coords"]]},
              "properties": {"status": "draft — not applied", "tags": {"highway": "footway", "footway": "link"},
                             "stops": [r["a"], r["b"]], "stop_name": r["name"],
                             "connects_ways": [ln["path_way"], ln["road_way"]],
                             "length_m": ln["length_m"], "stops_apart_m": round(r["dist_m"], 1),
                             "r5_walk_min_before": r["walk_min"],
                             "evidence": "two-way road with a separately mapped path beside it and no mapped "
                                         "link between path and carriageway at the stop; from OSM tags and "
                                         "the plot only, not checked on the ground"}}
             for r in recs for ln in r.get("links", [])]
    return {"type": "FeatureCollection", "name": "walk_links_draft",
            "osm_extract_date": extract_date, "features": feats}


def plot(recs: list[dict], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n = len(recs)
    cols = 5
    rows = -(-n // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(4.4 * cols, 4.4 * rows))
    style = {"road": ("#555555", 2.2), "path": ("#1f77b4", 1.6), "private": ("#bbbbbb", 1.0)}
    for ax, r in zip(np.ravel(axes), recs):
        for w in r["ways"]:
            hw = w["tags"]["highway"]
            k = "private" if not walkable(w["tags"]) else "path" if hw in PATHS else "road"
            xs, ys = zip(*w["pts"])
            ax.plot(xs, ys, color=style[k][0], lw=style[k][1], solid_capstyle="round",
                    ls=":" if k == "private" else "-", zorder=1 if k == "road" else 2)
        (xa, ya), (xb, yb) = r["stop_xy"]
        ax.scatter([xa, xb], [ya, yb], s=70, c="#d62728", zorder=5, edgecolors="white")
        for ln in r.get("links", []):
            (x0, y0), (x1, y1) = ln["coords"]
            ax.plot([x0, x1], [y0, y1], color="#2ca02c", lw=3.5, zorder=4)
        cx, cy = (xa + xb) / 2, (ya + yb) / 2
        half = max(abs(xa - xb), abs(ya - yb) * 1.6, 0.0006) * 1.6
        ax.set_xlim(cx - half, cx + half)
        ax.set_ylim(cy - half / 1.6, cy + half / 1.6)
        ax.set_aspect(1.6)
        ax.set_xticks([])
        ax.set_yticks([])
        walk = "no route" if r["walk_min"] is None else f"{r['walk_min']:.0f} min"
        ax.set_title(f"{r['name']}\n{r['dist_m']:.0f} m apart, R5 walk {walk}", fontsize=10)
        if r.get("links"):
            ax.set_xlabel("drafted: " + "; ".join(f"{ln['length_m']:.0f} m, path {ln['path_way']} to road {ln['road_way']}"
                                                  for ln in r["links"]), fontsize=6.5)
        elif r.get("note"):
            ax.set_xlabel(r["note"], fontsize=7.5)
    for ax in np.ravel(axes)[n:]:
        ax.set_axis_off()
    fig.suptitle("Stop pairs cut by the barrier test that look like artefacts of separately mapped paths. "
                 "Red: stops. Grey: roads. Blue: footways, cycleways, paths. Dotted: private or no foot access. "
                 "Green: drafted connector (not applied). © OpenStreetMap contributors", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out, dpi=120)
    plt.close(fig)


# ------------------------------------------------------------------ the whole class

def walk_graph(pbf: str):
    """Every walkable way in the extract as a graph (edges in metres, with way id and
    whether the way is a path or a carriageway), plus the edge geometries for snapping."""
    import math
    import networkx as nx
    import osmium

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.g = nx.Graph()
            self.edges = []                      # (lon0, lat0, lon1, lat1, u, v, is_path, way)

        def way(self, w):
            t = dict(w.tags)
            hw = t.get("highway")
            if hw is None or hw in ("motorway", "motorway_link", "construction", "proposed", "platform", "bus_stop",
                                    "raceway", "corridor", "elevator") or not walkable(t):
                return
            if t.get("area") == "yes":
                return
            try:
                pts = [(n.ref, n.lon, n.lat) for n in w.nodes]
            except Exception:
                return
            is_path = hw in PATHS
            for (a, x0, y0), (b, x1, y1) in zip(pts, pts[1:]):
                d = math.hypot((x1 - x0) * 111_320 * math.cos(math.radians(y0)), (y1 - y0) * 111_320)
                self.g.add_edge(a, b, m=d)
                if not is_path:
                    self.g.nodes[a]["road"] = self.g.nodes[b]["road"] = True
                self.edges.append((x0, y0, x1, y1, a, b, is_path, w.id))

    h = H()
    h.apply_file(pbf, locations=True)
    return h.g, h.edges


def snap_class(stops, g, edges, near_m: float, reach_m: float):
    """For each stop: the nearest walkable way (taken as where R5 links the stop), whether
    it is a separately mapped path, the straight-line distance to the nearest carriageway,
    and the walking distance on the network from the snap point to the nearest carriageway
    node (searched up to ``reach_m``). A stop is in the fault class when it snaps to a path,
    a walkable carriageway lies within ``near_m`` in a straight line, and the network needs
    more than ``near_m`` to reach one."""
    import networkx as nx
    import numpy as np
    import pandas as pd
    from pyproj import Transformer
    from shapely import STRtree
    from shapely.geometry import LineString, Point
    fwd = Transformer.from_crs(4326, 27700, always_xy=True).transform
    e = np.array([(x0, y0, x1, y1) for x0, y0, x1, y1, *_ in edges])
    ax, ay = fwd(e[:, 0], e[:, 1])
    bx, by = fwd(e[:, 2], e[:, 3])
    lines = [LineString([(ax[i], ay[i]), (bx[i], by[i])]) for i in range(len(edges))]
    is_path = np.array([x[6] for x in edges])
    tree_all = STRtree(lines)
    road_idx = np.flatnonzero(~is_path)
    tree_road = STRtree([lines[i] for i in road_idx])
    sx, sy = fwd(stops.lon.to_numpy(), stops.lat.to_numpy())
    rows = []
    for k, (x, y) in enumerate(zip(sx, sy)):
        p = Point(x, y)
        i = int(tree_all.nearest(p))
        d_snap = lines[i].distance(p)
        j = int(road_idx[tree_road.nearest(p)])
        d_road = lines[j].distance(p)
        net = 0.0
        if is_path[i] and d_road <= near_m:
            _, _, _, _, u, v, _, _ = edges[i]
            along = lines[i].project(p)
            start = {u: along, v: lines[i].length - along}
            best = float("inf")
            for n0, d0 in start.items():
                dist = nx.single_source_dijkstra_path_length(g, n0, cutoff=reach_m, weight="m")
                hit = [d0 + dd for n, dd in dist.items() if g.nodes[n].get("road")]
                if hit:
                    best = min(best, min(hit))
            net = best
        rows.append({"snap_way": edges[i][7], "snap_m": round(d_snap, 1), "snaps_to_path": bool(is_path[i]),
                     "road_way": edges[j][7], "road_straight_m": round(d_road, 1),
                     "road_network_m": None if not np.isfinite(net) else round(net, 1),
                     "road_x": float(lines[j].interpolate(lines[j].project(p)).x),
                     "road_y": float(lines[j].interpolate(lines[j].project(p)).y)})
    out = pd.concat([stops.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    far = out.road_network_m.isna() | (out.road_network_m > near_m)
    out["fault_class"] = out.snaps_to_path & (out.road_straight_m <= near_m) & far
    return out
