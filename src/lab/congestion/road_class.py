"""
Road class by DfT count point, propagated along the road (plans/P2.md §8 "Road class";
SPEC §3).

For each numbered road (``ref`` A…/M…), segments form an undirected graph through shared
OSM nodes. Every 2025 AADF count point on that road seeds the nearest segment with its
DfT category (TM/TA → ``srn``, PM/PA → ``local_a``). A multi-source Dijkstra over
segment lengths gives every other segment of the road the class of the nearest seed *by
distance along the road*, so a road that changes hands (Newport's A4042) splits at the
point between the two count points, and the class never jumps across a gap in the road.

Basis per segment:
* ``count_point`` — within half the count point's DfT link length of its seed;
* ``propagated`` — reached along the road but further than that;
* ``osm_fallback`` — no count point reachable on that road (class from OSM tags).

Boundaries (adjacent segments of one road with different classes) are listed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

DFT = {"TM": "srn", "TA": "srn", "PM": "local_a", "PA": "local_a"}


def propagate(seg: pd.DataFrame, cps: pd.DataFrame, default_half_link_m: float = 500.0) \
        -> tuple[pd.DataFrame, list[dict]]:
    """seg: way_id, seq, forward, u, v, ref, length_m, lon_u, lat_u, lon_v, lat_v.
    cps: cp_id, road_ref, cp_category, lon, lat, link_length_km.
    Returns per-segment (way_id, seq, forward, dft_class, basis, cp_id, along_m) and
    the boundaries."""
    out, bounds = [], []
    seg = seg[seg["ref"].notna() & seg["ref"].str.match(r"^[AM]\d")].copy()
    for ref, s in seg.groupby("ref"):
        c = cps[(cps["road_ref"] == ref) & cps["cp_category"].isin(DFT)]
        s = s.reset_index(drop=True)
        if c.empty:
            out.append(s[["way_id", "seq", "forward"]].assign(
                dft_class=None, basis="osm_fallback", cp_id=None, along_m=np.nan))
            continue
        nodes = pd.unique(np.concatenate([s["u"].to_numpy(), s["v"].to_numpy()]))
        idx = {n: i for i, n in enumerate(nodes)}
        a = s["u"].map(idx).to_numpy()
        b = s["v"].map(idx).to_numpy()
        n = len(nodes)
        # one undirected edge per node pair (a two-way road has a row per direction)
        e = pd.DataFrame({"lo": np.minimum(a, b), "hi": np.maximum(a, b),
                          "L": s["length_m"].to_numpy()}).groupby(["lo", "hi"])["L"].min()
        lo = e.index.get_level_values(0).to_numpy()
        hi = e.index.get_level_values(1).to_numpy()
        g = coo_matrix((np.r_[e.to_numpy(), e.to_numpy()], (np.r_[lo, hi], np.r_[hi, lo])),
                       shape=(n, n)).tocsr()
        # seed: the road node nearest each count point
        nx = np.zeros(n)
        ny = np.zeros(n)
        nx[a], ny[a] = s["lon_u"].to_numpy(), s["lat_u"].to_numpy()
        nx[b], ny[b] = s["lon_v"].to_numpy(), s["lat_v"].to_numpy()
        seeds, meta = [], []
        for _, cp in c.iterrows():
            d = np.hypot((nx - cp["lon"]) * 0.62, ny - cp["lat"]) * 111320
            k = int(d.argmin())
            if d[k] > 1000 or k in seeds:        # not on this stretch, or a duplicate
                continue
            seeds.append(k)
            half = (cp["link_length_km"] * 500) if pd.notna(cp["link_length_km"]) \
                else default_half_link_m
            meta.append((cp["cp_id"], DFT[cp["cp_category"]], half))
        if not seeds:
            out.append(s[["way_id", "seq", "forward"]].assign(
                dft_class=None, basis="osm_fallback", cp_id=None, along_m=np.nan))
            continue
        dist, _, src = dijkstra(g, directed=False, indices=seeds, min_only=True,
                                return_predecessors=True)
        seed_of = {sd: i for i, sd in enumerate(seeds)}
        # distance of a segment = min over its two nodes
        du, dv = dist[a], dist[b]
        # the nearer end decides; ties go to the lower node id, so both directions agree
        nearer_u = (du < dv) | ((du == dv) & (nodes[a] < nodes[b]))
        node = np.where(nearer_u, a, b)
        dd = np.where(nearer_u, du, dv)
        which = np.array([seed_of.get(src[x], -1) if np.isfinite(dd[i]) else -1
                          for i, x in enumerate(node)])
        cls = np.array([meta[w][1] if w >= 0 else None for w in which], dtype=object)
        half = np.array([meta[w][2] if w >= 0 else np.nan for w in which])
        basis = np.where(which < 0, "osm_fallback",
                         np.where(dd <= half, "count_point", "propagated"))
        cpid = np.array([meta[w][0] if w >= 0 else None for w in which], dtype=object)
        r = s[["way_id", "seq", "forward"]].assign(dft_class=cls, basis=basis, cp_id=cpid,
                                                   along_m=np.where(which >= 0, dd, np.nan))
        out.append(r)
        # boundaries: a node shared by segments of different classes
        node_cls: dict[int, set] = {}
        for x, y, k in zip(a, b, cls):
            if k is None:
                continue
            node_cls.setdefault(x, set()).add(k)
            node_cls.setdefault(y, set()).add(k)
        for x, ks in node_cls.items():
            if len(ks) > 1:
                bounds.append({"ref": ref, "node": int(nodes[x]), "classes": sorted(ks)})
    return pd.concat(out, ignore_index=True), bounds
