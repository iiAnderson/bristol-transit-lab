"""
Frequent-service coverage (plans/P3.md P3a; SPEC §8.2 as amended at P3).

Four steps, each a plain function over data frames so it can be tested on synthetic
inputs:

1. ``departures`` / ``stop_service`` — what leaves each stop, per period and direction,
   counted in vehicle journeys (``original_trip_id``) at calls where boarding is allowed;
2. ``clusters`` — stops grouped into stop clusters (the two sides of a road, the stands
   of an interchange), never across a barrier;
3. ``decay`` — the share of people prepared to walk a given distance to each kind of
   service, fitted to an observed mean and 85th percentile;
4. ``score`` — per OA: a service-quality class, a continuous score, and whether a
   frequent service is within walking reach.

Nothing here knows a place, a route or a station: mode classes come from GTFS route
types and a list of route ids passed in from config.
"""
from __future__ import annotations

import math
import zipfile

import numpy as np
import pandas as pd

OCTANTS = 8
CLASS_ORDER = ["A", "B", "C", "D", "none"]


# ---------------------------------------------------------------- 1. stop service

def read_gtfs(path) -> dict[str, pd.DataFrame]:
    with zipfile.ZipFile(path) as z:
        return {n[:-4]: pd.read_csv(z.open(n), dtype=str) for n in z.namelist()
                if n in ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt")}


def mode_class(route_type: str, route_id: str, brt_route_ids: set[str]) -> str:
    """GTFS route type (basic or extended) → the lab's mode class."""
    if route_id in brt_route_ids:
        return "brt"
    t = int(route_type)
    if t in (0, 5) or 900 <= t < 1000:
        return "tram"
    if t == 1 or 400 <= t < 500:
        return "metro"
    if t == 2 or 100 <= t < 200:
        return "rail"
    if t == 4 or 1000 <= t < 1100 or t == 1200:
        return "ferry"
    if 200 <= t < 300:
        return "coach"
    if t in (3, 11) or 700 <= t < 800:
        return "bus"
    raise ValueError(f"route {route_id}: GTFS route_type {route_type} has no mode class")


def _minutes(hms: pd.Series) -> pd.Series:
    p = hms.str.split(":", expand=True).astype(int)
    return p[0] * 60 + p[1] + p[2] / 60


def _octant(lon0, lat0, lon1, lat1) -> np.ndarray:
    """Compass octant (0 = north, clockwise) of the bearing from point 0 to point 1."""
    dx = (np.asarray(lon1) - np.asarray(lon0)) * np.cos(np.radians(np.asarray(lat0)))
    dy = np.asarray(lat1) - np.asarray(lat0)
    b = (np.degrees(np.arctan2(dx, dy)) + 360) % 360
    return (((b + 22.5) // 45) % OCTANTS).astype(int)


def departures(feed: dict[str, pd.DataFrame], brt_route_ids: set[str]) -> pd.DataFrame:
    """One row per vehicle journey leaving a stop: stop_id, mode_class, route_id,
    journey, dep_min, direction (octant towards the next call).

    A departure is a call that is not the journey's last and where boarding is allowed.
    Restriction copies of one source trip (``original_trip_id``) are one journey: a
    departure they share is counted once.
    """
    st = feed["stop_times"].copy()
    st["seq"] = st["stop_sequence"].astype(int)
    st = st.sort_values(["trip_id", "seq"])
    st["next_stop"] = st.groupby("trip_id")["stop_id"].shift(-1)
    st = st[st["next_stop"].notna()]
    if "pickup_type" in st:
        st = st[st["pickup_type"].fillna("0") != "1"]
    trips = feed["trips"]
    key = trips["original_trip_id"] if "original_trip_id" in trips else trips["trip_id"]
    trips = trips.assign(journey=key.fillna(trips["trip_id"]))
    routes = feed["routes"].assign(mode_class=[mode_class(t, r, brt_route_ids) for t, r in
                                               zip(feed["routes"].route_type, feed["routes"].route_id)])
    st = st.merge(trips[["trip_id", "route_id", "journey"]], on="trip_id") \
           .merge(routes[["route_id", "mode_class"]], on="route_id")
    st["dep_min"] = _minutes(st["departure_time"])
    xy = feed["stops"].set_index("stop_id")[["stop_lon", "stop_lat"]].astype(float)
    a, b = xy.loc[st["stop_id"]].to_numpy(), xy.loc[st["next_stop"]].to_numpy()
    st["direction"] = _octant(a[:, 0], a[:, 1], b[:, 0], b[:, 1])
    return st.drop_duplicates(["journey", "stop_id", "dep_min"])[
        ["stop_id", "mode_class", "route_id", "journey", "dep_min", "direction"]].reset_index(drop=True)


def parse_periods(periods: dict[str, str]) -> dict[str, tuple[float, float]]:
    out = {}
    for name, span in periods.items():
        a, b = span.split("-")
        out[name] = tuple(int(x[:2]) * 60 + int(x[3:]) for x in (a, b))
    return out


def _service(dep: pd.DataFrame, keys: list[str], periods: dict[str, tuple[float, float]]) -> pd.DataFrame:
    rows = []
    for name, (lo, hi) in periods.items():
        d = dep[(dep.dep_min >= lo) & (dep.dep_min < hi)].sort_values("dep_min")
        hours = (hi - lo) / 60
        for k, g in d.groupby(keys, sort=False):
            t = np.concatenate([[lo], g.dep_min.to_numpy(), [hi]])
            n = len(g)
            rows.append((*((k,) if not isinstance(k, tuple) else k), name, n / hours,
                         (hi - lo) / n, float(np.diff(t).max())))
    return pd.DataFrame(rows, columns=[*keys, "period", "departures_per_hour",
                                       "mean_headway_min", "max_headway_min"])


def stop_service(dep: pd.DataFrame, cluster_of: pd.Series,
                 periods: dict[str, tuple[float, float]]) -> pd.DataFrame:
    """Departures per hour by stop, mode class, period and direction. ``max_headway_min``
    is the longest gap in the period, counting from its start and to its end."""
    out = _service(dep, ["stop_id", "mode_class", "direction"], periods)
    out.insert(0, "cluster_id", out["stop_id"].map(cluster_of))
    return out.rename(columns={"direction": "direction_group"})


def cluster_service(dep: pd.DataFrame, cluster_of: pd.Series,
                    periods: dict[str, tuple[float, float]], min_share: float) -> pd.DataFrame:
    """Service level per cluster, mode class and period.

    ``sides`` is 2 where the cluster's departures go in opposed directions (two octants,
    each with at least ``min_share`` of them, 90° or more apart), else 1 — a terminus or
    a one-way stop. ``dph_one_way`` = departures per hour ÷ sides (the ARE convention:
    departures on all lines, halved for one direction, corrected for termini).
    ``directions`` counts the octants with at least ``min_share``.
    """
    d = dep.assign(cluster_id=dep["stop_id"].map(cluster_of))
    tot = _service(d, ["cluster_id", "mode_class"], periods)
    rows = []
    for name, (lo, hi) in periods.items():
        w = d[(d.dep_min >= lo) & (d.dep_min < hi)]
        for (c, m), g in w.groupby(["cluster_id", "mode_class"], sort=False):
            sh = g["direction"].value_counts(normalize=True)
            octs = sorted(sh[sh >= min_share].index)
            sep = max((min(abs(a - b), OCTANTS - abs(a - b)) for a in octs for b in octs), default=0)
            rows.append((c, m, name, len(octs), 2 if sep * 45 >= 90 else 1))
    sides = pd.DataFrame(rows, columns=["cluster_id", "mode_class", "period", "directions", "sides"])
    out = tot.merge(sides, on=["cluster_id", "mode_class", "period"])
    out["dph_one_way"] = out["departures_per_hour"] / out["sides"]
    out["headway_min"] = 60 / out["dph_one_way"]
    return out


def headline(cs: pd.DataFrame, periods: list[str]) -> pd.DataFrame:
    """The worse of the headline periods per cluster and mode class; a cluster with no
    departures in one of them has a service level of zero."""
    p = cs[cs.period.isin(periods)].pivot_table(index=["cluster_id", "mode_class"], columns="period",
                                                values=["dph_one_way", "directions"], fill_value=0)
    for per in periods:
        if ("dph_one_way", per) not in p:
            p[("dph_one_way", per)] = 0.0
            p[("directions", per)] = 0
    out = pd.DataFrame({"dph_one_way": p["dph_one_way"][periods].min(axis=1),
                        "directions": p["directions"][periods].max(axis=1)}).reset_index()
    out["period"] = "HEADLINE"
    return out


# ---------------------------------------------------------------- 2. stop clusters

def _bng(lon, lat):
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform(
        np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))


def candidate_pairs(stops: pd.DataFrame, same_name_m: float, any_name_m: float) -> pd.DataFrame:
    """Pairs of stops (a < b by position) that could be one cluster: within
    ``any_name_m``, or within ``same_name_m`` with the same name. ``stops`` has stop_id,
    stop_name, lon, lat and is restricted by the caller to street stops."""
    from scipy.spatial import cKDTree
    x, y = _bng(stops.lon, stops.lat)
    tree = cKDTree(np.c_[x, y])
    pairs = tree.query_pairs(max(same_name_m, any_name_m), output_type="ndarray")
    if not len(pairs):
        return pd.DataFrame(columns=["a", "b", "dist_m"])
    d = np.hypot(x[pairs[:, 0]] - x[pairs[:, 1]], y[pairs[:, 0]] - y[pairs[:, 1]])
    nm = stops.stop_name.str.strip().str.lower().to_numpy()
    keep = (d <= any_name_m) | ((nm[pairs[:, 0]] == nm[pairs[:, 1]]) & (d <= same_name_m))
    ids = stops.stop_id.to_numpy()
    return pd.DataFrame({"a": ids[pairs[keep, 0]], "b": ids[pairs[keep, 1]], "dist_m": d[keep]})


def clusters(stop_ids: list[str], pairs: pd.DataFrame, walk_min: pd.Series | None = None,
             max_walk_min: float | None = None) -> tuple[pd.Series, pd.DataFrame]:
    """Connected components of the candidate pairs. With ``walk_min`` (indexed by
    (a, b), minutes on the network, missing = unreachable within the matrix cap), pairs
    further apart on foot than ``max_walk_min`` are cut first: the barrier test.
    Returns the stop → cluster mapping and the pairs that were cut."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    cut = pairs.iloc[0:0]
    if walk_min is not None and len(pairs):
        w = pd.Series(list(zip(pairs.a, pairs.b))).map(walk_min).to_numpy()
        wb = pd.Series(list(zip(pairs.b, pairs.a))).map(walk_min).to_numpy()
        w = np.fmax(w, wb)                      # the worse direction; NaN only if both missing
        bad = np.isnan(w) | (w > max_walk_min)
        cut = pairs[bad].assign(walk_min=w[bad])
        pairs = pairs[~bad]
    idx = {s: i for i, s in enumerate(stop_ids)}
    n = len(stop_ids)
    g = coo_matrix((np.ones(len(pairs)), (pairs.a.map(idx), pairs.b.map(idx))), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    s = pd.Series(lab, index=stop_ids)
    first = pd.Series(stop_ids, index=stop_ids).groupby(s).min()
    return "c:" + s.map(first), cut


# ---------------------------------------------------------------- 3. distance decay

def _logistic(d, mu, s):
    """Share prepared to walk at least d: a logistic survival curve, 1 at d = 0."""
    d = np.asarray(d, dtype=float)
    return (1 + math.exp(-mu / s)) / (1 + np.exp((d - mu) / s))


def fit_logistic(mean_m: float, p85_m: float) -> dict:
    """(mu, s) such that walk distances distributed with survival ``_logistic`` have the
    given mean and 85th percentile. Mean of a non-negative variable = ∫ survival."""
    from scipy.optimize import fsolve

    def mean_of(mu, s):
        return s * math.log1p(math.exp(mu / s)) * (1 + math.exp(-mu / s))

    def eqs(v):
        mu, s = v
        s = abs(s) + 1e-9
        return [mean_of(mu, s) - mean_m, float(_logistic(p85_m, mu, s)) - 0.15]

    mu, s = fsolve(eqs, [mean_m, (p85_m - mean_m) / 1.7])
    s = abs(s)
    return {"form": "logistic", "mu_m": float(mu), "s_m": float(s),
            "fit_mean_m": mean_of(mu, s),
            "fit_p85_m": float(mu + s * math.log((1 + math.exp(-mu / s)) / 0.15 - 1))}


def fit_exponential(mean_m: float, p85_m: float) -> dict:
    """One parameter, so it can match the mean only; the implied 85th percentile shows
    the misfit."""
    return {"form": "exponential", "scale_m": mean_m, "fit_mean_m": mean_m,
            "fit_p85_m": -mean_m * math.log(0.15)}


def decay_curves(obs: dict[str, dict[str, float]], between: dict[str, float]) -> dict[str, dict]:
    """Logistic curves per mode class. ``obs`` has mean_m and p85_m for bus and rail;
    ``between`` places other classes between bus (0) and rail (1). Metro takes the rail
    curve and ferry the bus curve."""
    fit = {m: fit_logistic(obs[m]["mean_m"], obs[m]["p85_m"]) | obs[m] for m in ("bus", "rail")}
    for m, w in between.items():
        o = {k: (1 - w) * obs["bus"][k] + w * obs["rail"][k] for k in ("mean_m", "p85_m")}
        fit[m] = fit_logistic(o["mean_m"], o["p85_m"]) | o
    fit["metro"], fit["ferry"] = fit["rail"], fit["bus"]
    return fit


def weight(walk_min, curve: dict, walk_kmh: float, scale: float = 1.0):
    """Decay weight at a walking time. ``scale`` < 1 shrinks every distance the curve is
    defined on (the reduced-mobility variant)."""
    d = np.asarray(walk_min, dtype=float) * walk_kmh * 1000 / 60
    return _logistic(d / scale, curve["mu_m"], curve["s_m"])


def half_weight_m(curve: dict) -> float:
    mu, s = curve["mu_m"], curve["s_m"]
    return mu + s * math.log(2 * (1 + math.exp(-mu / s)) - 1)


def rounding_effect(curve: dict, walk_kmh: float, threshold_m: float, max_m: float = 3000) -> dict:
    """What whole-minute walking times do (plans/P3.md Q14), on a synthetic field of
    origins spread evenly over the plane around one stop (so the number at distance d
    grows with d): the decay weight and the share inside ``threshold_m``, with the time
    exact, truncated to the minute, and rounded to the nearest minute."""
    d = np.linspace(0.5, max_m, 6000)
    area = d / d.sum()                                   # ring area ∝ distance
    t = d / (walk_kmh * 1000 / 60)
    thr = threshold_m / (walk_kmh * 1000 / 60)
    out = {}
    for name, tt in (("exact", t), ("truncated", np.floor(t)), ("rounded", np.round(t))):
        w = weight(tt, curve, walk_kmh)
        out[name] = {"weighted_area": float((w * area).sum()),
                     "share_within_threshold": float(area[tt <= thr].sum())}
    for name in ("truncated", "rounded"):
        for k in ("weighted_area", "share_within_threshold"):
            out[name][k + "_vs_exact_pct"] = round(100 * (out[name][k] / out["exact"][k] - 1), 1)
    return out


# ---------------------------------------------------------------- 4. score

def stop_category(mode: str, directions: float, headway_min: float, bands: list[float],
                  table: dict[str, list[int]], node_min_directions: int) -> int | None:
    """ARE stop category (1 best … 5) from the kind of service and the interval between
    departures in one direction; None beyond the last band or for coaches."""
    if mode == "coach" or not np.isfinite(headway_min) or headway_min > bands[-1]:
        return None
    group = ("rail_node" if directions >= node_min_directions else "rail_line") \
        if mode in ("rail", "metro") else "street"
    return table[group][min(int(np.searchsorted(bands, headway_min, side="right")), len(bands) - 1)]


def are_class(category: int | None, walk_min: float, walk_kmh: float, dist_bands_m: list[float],
              table: dict[int, list[str]]) -> str:
    if category is None or not np.isfinite(walk_min):
        return "none"
    d = walk_min * walk_kmh * 1000 / 60
    if d > dist_bands_m[-1]:
        return "none"
    return table[category][int(np.searchsorted(dist_bands_m, d, side="left"))]


def score(walk: pd.DataFrame, level: pd.DataFrame, curves: dict[str, dict], cfg: dict) -> pd.DataFrame:
    """Per OA, for one period's service levels.

    ``walk``: OA21CD, cluster_id, walk_min (the nearest stop of the cluster).
    ``level``: cluster_id, mode_class, dph_one_way, directions.
    ``cfg``: walk_kmh, frequent_headway_min (list), score_cap_dph, the ARE tables,
    rail_node_min_directions, and scale (1, or < 1 for reduced mobility).

    Columns: ``are_class`` (best over clusters), ``score`` (Σ decay × capped departures
    per hour), and for each headway threshold h: ``frequent_h`` (a cluster with a
    headway of h or better within the 85th-percentile walk for its mode),
    ``frequent_h_weight`` (the largest decay weight among clusters that frequent) and
    ``frequent_h_walk_min`` (the walk to the nearest of them).
    """
    scale = cfg.get("scale", 1.0)
    lv = level[(level.mode_class != "coach") & (level.dph_one_way > 0)]
    j = walk.merge(lv, on="cluster_id")
    out = pd.DataFrame(index=pd.Index(sorted(walk.OA21CD.unique()), name="OA21CD"))
    if j.empty:
        j = j.assign(w=[], headway=[], cls=[])
    else:
        j["headway"] = 60 / j.dph_one_way
        j["w"] = np.nan
        j["p85_min"] = np.nan
        for m, g in j.groupby("mode_class"):
            c = curves[m]
            j.loc[g.index, "w"] = weight(g.walk_min, c, cfg["walk_kmh"], scale)
            j.loc[g.index, "p85_min"] = c["p85_m"] * scale / (cfg["walk_kmh"] * 1000 / 60)
        cat = [stop_category(m, d, h, cfg["are_interval_bands_min"], cfg["are_stop_category"],
                             cfg["rail_node_min_directions"])
               for m, d, h in zip(j.mode_class, j.directions, j.headway)]
        j["cls"] = [are_class(c, t / scale, cfg["walk_kmh"], cfg["are_distance_bands_m"], cfg["are_class"])
                    for c, t in zip(cat, j.walk_min)]
    rank = {c: i for i, c in enumerate(CLASS_ORDER)}
    best = j.assign(r=j.cls.map(rank)).groupby("OA21CD").r.min() if len(j) else pd.Series(dtype=float)
    out["are_class"] = best.reindex(out.index).map(lambda r: CLASS_ORDER[int(r)] if pd.notna(r) else "none")
    sc = (j.w * j.dph_one_way.clip(upper=cfg["score_cap_dph"])).groupby(j.OA21CD).sum() if len(j) else pd.Series(dtype=float)
    out["score"] = sc.reindex(out.index).fillna(0.0)
    for h in cfg["frequent_headway_min"]:
        f = j[(j.headway <= h + 1e-9) & (j.walk_min <= j.p85_min)] if len(j) else j
        g = f.groupby("OA21CD")
        out[f"frequent_{h}"] = out.index.isin(f.OA21CD) if len(f) else False
        out[f"frequent_{h}_weight"] = (g.w.max() if len(f) else pd.Series(dtype=float)).reindex(out.index).fillna(0.0)
        out[f"frequent_{h}_walk_min"] = (g.walk_min.min() if len(f) else pd.Series(dtype=float)).reindex(out.index)
    return out.reset_index()


def oa_jobs(lsoa_jobs: pd.DataFrame, oa: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Jobs at OA: each LSOA's jobs split by its OAs' workplace counts (plans/P3.md Q13).
    ``oa`` has OA21CD, LSOA21CD, workers. An LSOA with no workplace count is split
    equally across its OAs, and counted."""
    o = oa.merge(lsoa_jobs, on="LSOA21CD", how="left")
    tot = o.groupby("LSOA21CD").workers.transform("sum")
    n = o.groupby("LSOA21CD").OA21CD.transform("count")
    share = np.where(tot > 0, o.workers / tot.where(tot > 0, 1), 1 / n)
    o["jobs"] = o["jobs"].fillna(0) * share
    info = {"lsoas": int(o.LSOA21CD.nunique()),
            "lsoas_split_equally": int(o.loc[tot == 0, "LSOA21CD"].nunique()),
            "jobs_in_equal_split_lsoas": float(o.loc[tot == 0, "jobs"].sum()),
            "jobs_total": float(o.jobs.sum())}
    return o[["OA21CD", "jobs"]], info


def summarise(oa: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """Population and jobs by service-quality class and by frequent-service flag.
    ``oa`` has residents, jobs, are_class and the frequent_* columns; ``by`` adds a
    grouping column (a deprivation decile)."""
    keys = [by] if by else []
    flags = [c for c in oa.columns if c.startswith("frequent_") and oa[c].dtype == bool]
    rows = []
    for k, g in (oa.groupby(by) if by else [(None, oa)]):
        r = {by: k} if by else {}
        r |= {"oas": len(g), "residents": g.residents.sum(), "jobs": g.jobs.sum()}
        for c in CLASS_ORDER:
            r[f"residents_class_{c}"] = g.residents[g.are_class == c].sum()
        for f in flags:
            r[f"residents_{f}"] = g.residents[g[f]].sum()
            r[f"residents_not_{f}"] = g.residents[~g[f]].sum()
            r[f"jobs_{f}"] = g.jobs[g[f]].sum()
            r[f"share_residents_{f}"] = g.residents[g[f]].sum() / g.residents.sum()
            r[f"weighted_residents_{f}"] = (g.residents * g[f + "_weight"]).sum()
        r["mean_score_resident_weighted"] = (g.score * g.residents).sum() / g.residents.sum()
        rows.append(r)
    return pd.DataFrame(rows).sort_values(keys) if keys else pd.DataFrame(rows)
