"""``lab coverage …`` — frequent-service coverage (plans/P3.md P3a). Orchestration only:
the method is in ``coverage.py``."""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import click

from . import coverage as cov
from . import network, params, runrecord
from .config import LabConfig


def _ctx():
    import yaml
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    return cfg, raw, ps


def _net(cfg, elevation: str | None) -> tuple[str, dict]:
    from .supply import feeds
    names = ["osm_clip", "bus_gtfs", "rail_gtfs"] + (["dem_clip"] if elevation else [])
    h = {n: feeds.get(cfg, n)["sha256"] for n in names}
    return network.version(h, elevation), h


def _periods(ps: dict) -> dict:
    return cov.parse_periods({k.split(".", 1)[1]: v for k, v in ps.items() if k.startswith("periods.")})


def _walk_matrix(osm: str, tif: str | None, fn: str | None, o, d, ps: dict, max_min: float,
                 day: dt.date, chunk: int = 400):
    """r5py walk times, origins → destinations (frames with id, lon, lat), long format
    with unreachable pairs dropped."""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point
    from r5py import ElevationCostFunction, TransportMode, TransportNetwork, TravelTimeMatrix
    from .supply import dem
    g = lambda df: gpd.GeoDataFrame({"id": df["id"]}, crs="EPSG:4326",  # noqa: E731
                                    geometry=[Point(x, y) for x, y in zip(df.lon, df.lat)])
    net = (TransportNetwork(osm, []) if fn is None else
           TransportNetwork(osm, [], elevation_model=[dem.for_function(Path(tif), fn)],
                            elevation_cost_function=ElevationCostFunction(fn)))
    dep = dt.datetime.combine(day, dt.time.fromisoformat(ps["skims.window_start.AM"]))
    dd, out = g(d), []
    for i in range(0, len(o), chunk):
        t = TravelTimeMatrix(net, origins=g(o.iloc[i:i + chunk]), destinations=dd, departure=dep,
                             transport_modes=[TransportMode.WALK],
                             max_time=dt.timedelta(minutes=max_min),
                             speed_walking=ps["routing.walk_speed_kmh"])
        out.append(t[t["travel_time"].notna()])
    return pd.concat(out, ignore_index=True)


@click.group()
def coverage() -> None:
    """Frequent-service coverage: stop service, clusters, walking times, score."""


@coverage.command("stops")
@click.option("--elevation", type=click.Choice(["none", "TOBLER", "MINETTI"]), default="none",
              show_default=True, help="Slope cost for the barrier test's walking times.")
def coverage_stops(elevation: str) -> None:
    """Stop service table and stop clusters from the baseline bus and rail GTFS."""
    import duckdb
    import pandas as pd
    cfg, raw, ps = _ctx()
    fn = None if elevation == "none" else elevation
    nv, hashes = _net(cfg, fn)
    day = raw["modelled_date"]
    rec = runrecord.build(cfg, command="coverage-stops",
                          inputs=[{"name": k, "sha256": v} for k, v in hashes.items()])
    run_dir = runrecord.write(cfg, rec).parent
    try:
        brt = set(raw["metrobus"]["route_ids"])
        feeds_ = {k: cov.read_gtfs(cfg.root / raw[k]["out"]) for k in ("bus", "rail")}
        dep = pd.concat([cov.departures(f, brt) for f in feeds_.values()], ignore_index=True)
        # Independent reconciliation (rule 5): departures = stop_times that are not the
        # last call of their trip and allow boarding, one per source trip.
        indep = 0
        for f in feeds_.values():
            st = f["stop_times"].assign(seq=f["stop_times"].stop_sequence.astype(int))
            st = st[st.seq != st.groupby("trip_id").seq.transform("max")]
            if "pickup_type" in st:
                st = st[st.pickup_type.fillna("0") != "1"]
            tr = f["trips"]
            j = tr.get("original_trip_id", tr.trip_id).fillna(tr.trip_id)
            st = st.merge(tr.assign(journey=j)[["trip_id", "journey"]], on="trip_id")
            indep += len(st.drop_duplicates(["journey", "stop_id", "departure_time"]))
        if indep != len(dep):
            raise click.ClickException(f"departures do not reconcile: {len(dep)} vs {indep}")
        stops = pd.concat([f["stops"].assign(feed=k) for k, f in feeds_.items()], ignore_index=True)
        stops = stops.rename(columns={"stop_lon": "lon", "stop_lat": "lat"})
        stops[["lon", "lat"]] = stops[["lon", "lat"]].astype(float)
        stops = stops[stops.stop_id.isin(set(dep.stop_id))].reset_index(drop=True)
        rail_like = set(dep.stop_id[dep.mode_class.isin(["rail", "metro"])])
        street = stops[~stops.stop_id.isin(rail_like)]
        pairs = cov.candidate_pairs(street, ps["coverage.cluster_same_name_m"],
                                    ps["coverage.cluster_any_name_m"])
        t0 = time.time()
        inv = street[street.stop_id.isin(set(pairs.a) | set(pairs.b))][["stop_id", "lon", "lat"]] \
            .rename(columns={"stop_id": "id"})
        m = _walk_matrix(str(cfg.root / raw["osm"]["clip"]), str(cfg.root / raw["dem"]["out"]), fn,
                         inv, inv, ps, 3 * ps["coverage.cluster_max_walk_min"],
                         day if isinstance(day, dt.date) else dt.date.fromisoformat(day))
        walk = m.set_index(["from_id", "to_id"])["travel_time"].astype(float)
        cl, cut = cov.clusters(list(stops.stop_id), pairs, walk, ps["coverage.cluster_max_walk_min"])
        cut.to_csv(run_dir / "cluster_pairs_cut_by_barrier_test.csv", index=False)
        periods = _periods(ps)
        ss = cov.stop_service(dep, cl, periods)
        cs = cov.cluster_service(dep, cl, periods, ps["coverage.direction_min_share"])
        cs = pd.concat([cs, cov.headline(cs, ps["coverage.headline_periods"])], ignore_index=True)
        sc = stops.assign(cluster_id=stops.stop_id.map(cl))
        sc["n_stops"] = sc.groupby("cluster_id").stop_id.transform("count")
        for df in (ss, cs, sc):
            df.insert(0, "network_version", nv)
        with duckdb.connect(str(cfg.lab_db)) as con:
            for name, df in (("stop_cluster", sc), ("stop_service", ss), ("cluster_service", cs)):
                con.register("df_", df)
                con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df_")
                con.unregister("df_")
        size = sc.groupby("cluster_id").size()
        day_tot = dep.groupby("stop_id").size()
        res = {
            "network_version": nv, "departures": int(len(dep)), "reconciled_with_independent_count": True,
            "journeys": int(dep.journey.nunique()),
            # rail: departures = calls − one last call per trip − set-down-only calls that are not last
            "rail_departures": int(dep.mode_class.eq("rail").sum()),
            "rail_stop_times_minus_trips": int(len(feeds_["rail"]["stop_times"]) - len(feeds_["rail"]["trips"])),
            "rail_set_down_only_calls": int((feeds_["rail"]["stop_times"].get("pickup_type") == "1").sum()),
            "stops_with_departures": int(len(stops)),
            "departures_by_mode_class": dep.mode_class.value_counts().to_dict(),
            "routes_by_mode_class": dep.groupby("mode_class").route_id.nunique().to_dict(),
            "candidate_pairs": int(len(pairs)), "pairs_cut_by_barrier_test": int(len(cut)),
            "barrier_walk_s": round(time.time() - t0, 1),
            "clusters": int(cl.nunique()), "cluster_size_counts": size.value_counts().sort_index().to_dict(),
            "largest_clusters": [{"cluster_id": c, "stops": int(n),
                                  "names": sorted(set(sc.stop_name[sc.cluster_id == c]))[:6]}
                                 for c, n in size.sort_values(ascending=False).head(8).items()],
            "busiest_rail_stops_departures_all_day": {
                stops.set_index("stop_id").stop_name[s]: int(n)
                for s, n in day_tot[day_tot.index.isin(rail_like)].sort_values(ascending=False).head(5).items()},
        }
        hl = cs[cs.period == "HEADLINE"]
        for h in ps["coverage.frequent_headway_min"]:
            f = hl[hl.dph_one_way >= 60 / h - 1e-9]
            res[f"clusters_frequent_{h}_headline"] = f.groupby("mode_class").cluster_id.nunique().to_dict()
        _cluster_plot(sc, cut, run_dir / "clusters_review.png")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    click.echo(json.dumps(res, indent=1, default=str))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


def _cluster_plot(sc, cut, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(15, 7))
    multi = sc[sc.n_stops > 1]
    ax[0].scatter(sc.lon, sc.lat, s=1, c="#bbbbbb", linewidths=0)
    for _, g in multi.groupby("cluster_id"):
        ax[0].plot(g.lon, g.lat, lw=0.6, c="#1f77b4")
    xy = sc.set_index("stop_id")[["lon", "lat"]]
    for a, b in zip(cut.a, cut.b):
        ax[0].plot([xy.lon[a], xy.lon[b]], [xy.lat[a], xy.lat[b]], lw=1.2, c="#d62728")
    ax[0].set_title(f"Stop clusters: {sc.cluster_id.nunique():,} from {len(sc):,} stops "
                    f"(blue: joined; red: {len(cut)} pairs cut by the barrier test)")
    ax[0].set_aspect(1.6)
    n = sc.groupby("cluster_id").size().value_counts().sort_index()
    ax[1].bar(n.index.astype(str), n.values, color="#1f77b4")
    ax[1].set_yscale("log")
    ax[1].set_xlabel("stops per cluster")
    ax[1].set_ylabel("clusters (log scale)")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


@coverage.command("walk")
@click.option("--elevation", type=click.Choice(["none", "TOBLER", "MINETTI"]), default="none",
              show_default=True)
def coverage_walk(elevation: str) -> None:
    """Walking times on the network from every OA centroid to every stop with service."""
    import duckdb
    import numpy as np
    cfg, raw, ps = _ctx()
    fn = None if elevation == "none" else elevation
    nv, hashes = _net(cfg, fn)
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        o = con.execute("SELECT OA21CD id, lon, lat FROM int_oa_pwc ORDER BY 1").df()
        s = con.execute("SELECT stop_id id, lon, lat, cluster_id, stop_name FROM stop_cluster ORDER BY 1").df()
    rec = runrecord.build(cfg, command="coverage-walk",
                          inputs=[{"name": k, "sha256": v} for k, v in hashes.items()])
    run_dir = runrecord.write(cfg, rec).parent
    try:
        t0 = time.time()
        cap = ps["coverage.max_walk_min"]
        m = _walk_matrix(str(cfg.root / raw["osm"]["clip"]), str(cfg.root / raw["dem"]["out"]), fn,
                         o, s, ps, cap, day)
        m = m.rename(columns={"from_id": "OA21CD", "to_id": "stop_id", "travel_time": "walk_min"})
        m["walk_min"] = m.walk_min.astype(float)
        out = cfg.root / raw["coverage"]["dir"] / nv
        out.mkdir(parents=True, exist_ok=True)
        m.to_parquet(out / "oa_stop_walk.parquet")
        # Q15: where the network walk is far longer than the straight line.
        ox, oy = cov._bng(o.lon, o.lat)
        sx, sy = cov._bng(s.lon, s.lat)
        oi = {k: i for i, k in enumerate(o.id)}
        si = {k: i for i, k in enumerate(s.id)}
        a, b = m.OA21CD.map(oi).to_numpy(), m.stop_id.map(si).to_numpy()
        m["straight_m"] = np.hypot(ox[a] - sx[b], oy[a] - sy[b])
        m["network_m"] = m.walk_min * ps["routing.walk_speed_kmh"] * 1000 / 60
        near = m[m.straight_m >= 100].assign(ratio=lambda x: x.network_m / x.straight_m)
        worst = near.sort_values("ratio", ascending=False).head(400) \
            .merge(s[["id", "stop_name", "cluster_id"]], left_on="stop_id", right_on="id").drop(columns="id")
        worst.to_csv(run_dir / "largest_network_to_straight_line_ratios.csv", index=False)
        # origins whose nearest stop in a straight line is not their nearest on foot by a wide margin,
        # or that reach no stop at all within the cap
        from scipy.spatial import cKDTree
        dist, j = cKDTree(np.c_[sx, sy]).query(np.c_[ox, oy])
        nearest = o.assign(nearest_stop=s.id.to_numpy()[j], nearest_straight_m=dist)
        got = m.groupby("OA21CD").walk_min.min()
        to_nearest = m.set_index(["OA21CD", "stop_id"]).walk_min
        nearest["walk_to_it_min"] = [to_nearest.get((a_, b_), np.nan) for a_, b_ in zip(nearest.id, nearest.nearest_stop)]
        nearest["best_walk_min"] = nearest.id.map(got)
        odd = nearest[(nearest.nearest_straight_m <= 400) &
                      (nearest.walk_to_it_min.isna() | (nearest.walk_to_it_min * 80 > 3 * nearest.nearest_straight_m + 240))]
        odd.sort_values("nearest_straight_m").to_csv(run_dir / "origins_far_from_their_nearest_stop.csv", index=False)
        r = near.ratio
        res = {"network_version": nv, "elevation": elevation, "origins": int(len(o)), "stops": int(len(s)),
               "pairs_within_cap": int(len(m)), "cap_min": cap, "runtime_s": round(time.time() - t0, 1),
               "origins_reaching_no_stop": int((~o.id.isin(m.OA21CD)).sum()),
               "median_walk_to_nearest_stop_min": float(got.median()),
               "ratio_network_to_straight_line": {"pairs_100m_plus": int(len(near)), "median": float(r.median()),
                                                  "p95": float(r.quantile(.95)), "p99": float(r.quantile(.99)),
                                                  "above_3": int((r > 3).sum()), "above_5": int((r > 5).sum())},
               "origins_far_from_their_nearest_stop": int(len(odd)),
               "out": str(out / "oa_stop_walk.parquet")}
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    click.echo(json.dumps(res, indent=1))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


def _obs(ps: dict) -> tuple[dict, dict]:
    obs = {m: {k: ps[f"coverage.decay.{m}.{k}"] for k in ("mean_m", "p85_m")} for m in ("bus", "rail")}
    between = {"brt": ps["coverage.decay.brt_towards_rail"], "tram": ps["coverage.decay.tram_towards_rail"]}
    return obs, between


@coverage.command("score")
@click.option("--elevation", type=click.Choice(["none", "TOBLER", "MINETTI"]), default="none",
              show_default=True, help="Which walking-time matrix to use.")
def coverage_score(elevation: str) -> None:
    """Service-quality class, continuous score and frequent-service coverage per OA."""
    import duckdb
    import geopandas as gpd
    import pandas as pd
    from . import upstream
    from .supply import feeds, gtfs_rail
    cfg, raw, ps = _ctx()
    fn = None if elevation == "none" else elevation
    nv, hashes = _net(cfg, fn)
    cc = raw["coverage"]
    dep_files = {k: cfg.root / v["path"] for k, v in cc["deprivation"].items()}
    odwp = cfg.upstream_raw / raw["spikes"]["odwp01ew_oa"]
    rec = runrecord.build(cfg, command="coverage-score", inputs=[
        *[{"name": k, "sha256": v} for k, v in hashes.items()],
        *[{"name": str(p), "sha256": params.file_hash(p)}
          for p in (*dep_files.values(), odwp, cfg.upstream_raw / cc["ts001_oa"])]])
    run_dir = runrecord.write(cfg, rec).parent
    try:
        now = dt.datetime.now(dt.timezone.utc)
        for k, v in cc["deprivation"].items():
            feeds.register(cfg, feed_id=f"deprivation_{k}", kind="ref", source_url=v["url"],
                           path=dep_files[k], downloaded_at=now, licence=v["licence"], notes=v["page"])
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            sc = con.execute("SELECT * FROM stop_cluster").df()
            cs = con.execute("SELECT * FROM cluster_service").df()
            oa = con.execute("SELECT OA21CD, LSOA21CD FROM int_oa ORDER BY 1").df()
            bres = con.execute("SELECT LSOA21CD, jobs FROM nat_bres").df()
            wp = con.execute(f"""SELECT "OA of workplace code" OA21CD, sum("Count") workers
                                 FROM read_csv('{odwp}')
                                 WHERE "Place of work indicator (4 categories) code" = 3 GROUP BY 1""").df()
        if set(sc.network_version) != {nv}:
            raise click.ClickException(f"stop tables are for network {set(sc.network_version)}, not {nv}: "
                                       "run `lab coverage stops` with the same --elevation")
        ts001 = cfg.upstream_raw / cc["ts001_oa"]
        with upstream.connect(cfg) as up:
            up_pop = upstream.read_table(up, "ts001").df()
            pop = up.execute(f"""SELECT "geography code" OA21CD,
                                        "Residence type: Total; measures: Value" residents
                                 FROM read_csv('{ts001}')""").df()
            chk = up_pop.merge(pop, on="OA21CD", how="left", suffixes=("_up", ""))
            if not (chk.residents_up == chk.residents).all():
                raise click.ClickException("national TS001 file disagrees with upstream's ts001 table")
        bgc = cfg.root / cc["oa_bgc"]
        if not bgc.is_file():
            import os
            from .supply import avl, points
            got = points.fetch(cc["oa_bgc_service"], list(oa.OA21CD), bgc,
                               avl.user_agent(cfg, os.environ["LAB_CONTACT_EMAIL"]), batch=100)
            if got["missing"]:
                raise click.ClickException(f"OA boundaries missing from ONS: {got['missing'][:5]}")
        feeds.register(cfg, feed_id="ons_oa21_bgc_internal", kind="ref", source_url=cc["oa_bgc_service"],
                       path=bgc, downloaded_at=dt.datetime.fromtimestamp(bgc.stat().st_mtime, dt.timezone.utc),
                       licence="OGL v3")
        geom = gpd.read_file(bgc)[["OA21CD", "geometry"]]
        if set(geom.OA21CD) != set(oa.OA21CD):
            raise click.ClickException("OA boundary file does not match the internal OAs")
        walk = pd.read_parquet(cfg.root / cc["dir"] / nv / "oa_stop_walk.parquet")
        walk = walk.merge(sc[["stop_id", "cluster_id"]], on="stop_id") \
            .groupby(["OA21CD", "cluster_id"], as_index=False).walk_min.min()
        missing = sorted(set(oa.OA21CD) - set(pop.OA21CD))
        if missing:
            raise click.ClickException(f"{len(missing)} internal OAs have no population: {missing[:5]}")
        oa = oa.merge(pop, on="OA21CD")
        jobs, jobs_info = cov.oa_jobs(bres, oa.merge(wp, on="OA21CD", how="left").fillna({"workers": 0}))
        oa = oa.merge(jobs, on="OA21CD")
        eng = pd.read_csv(dep_files["england"], usecols=[cc["deprivation"]["england"]["lsoa_column"],
                                                         cc["deprivation"]["england"]["decile_column"]])
        eng.columns = ["LSOA21CD", "imd_decile_england"]
        wal = pd.DataFrame([f["properties"] for f in json.loads(dep_files["wales"].read_text())["features"]]) \
            .rename(columns={"lsoa": "LSOA21CD", "decile": "wimd_decile_wales"})[["LSOA21CD", "wimd_decile_wales"]]
        oa = oa.merge(eng, on="LSOA21CD", how="left").merge(wal, on="LSOA21CD", how="left")
        both = oa.imd_decile_england.notna() & oa.wimd_decile_wales.notna()
        neither = oa.imd_decile_england.isna() & oa.wimd_decile_wales.isna()
        if both.any() or neither.any():
            raise click.ClickException(f"deprivation index: {int(both.sum())} OAs in both indices, "
                                       f"{int(neither.sum())} in neither")
        obs, between = _obs(ps)
        curves = cov.decay_curves(obs, between)
        kmh = ps["routing.walk_speed_kmh"]
        scfg = {"walk_kmh": kmh, **{k.split(".", 1)[1]: ps[k] for k in (
            "coverage.frequent_headway_min", "coverage.score_cap_dph", "coverage.are_interval_bands_min",
            "coverage.are_stop_category", "coverage.are_distance_bands_m", "coverage.are_class",
            "coverage.rail_node_min_directions")}}
        rm_scale = ps["coverage.reduced_mobility_half_weight_m"] / cov.half_weight_m(curves["bus"])
        tables, per_period = {}, {}
        for per in ["HEADLINE", *_periods(ps)]:
            lv = cs[cs.period == per]
            s = cov.score(walk, lv, curves, scfg).set_index("OA21CD").reindex(oa.OA21CD)
            s["are_class"] = s.are_class.fillna("none")
            for c in s.columns:
                if s[c].dtype == object and c != "are_class":
                    s[c] = s[c].fillna(False).astype(bool)
            s = s.fillna({c: 0.0 for c in s.columns if c.endswith("_weight") or c == "score"})
            per_period[per] = s.reset_index()
        head = oa.merge(per_period["HEADLINE"], on="OA21CD")
        rm = cov.score(walk, cs[cs.period == "HEADLINE"], curves, scfg | {"scale": rm_scale}) \
            .set_index("OA21CD").reindex(oa.OA21CD).reset_index()
        hs = ps["coverage.frequent_headway_min"]
        for h in hs:
            head[f"frequent_{h}_reduced_mobility"] = rm[f"frequent_{h}"].fillna(False).astype(bool).to_numpy()
            for per in ("EVE", "AMPH", "PM", "AM", "IP"):
                head[f"frequent_{h}_{per}"] = per_period[per][f"frequent_{h}"].astype(bool).to_numpy()
        for per in ("EVE", "AMPH", "PM", "AM", "IP"):
            head[f"are_class_{per}"] = per_period[per].are_class.to_numpy()
        tot = {"residents": int(head.residents.sum()), "jobs": float(head.jobs.sum())}
        summ = cov.summarise(head)
        summ.T.to_csv(run_dir / "summary.csv", header=False)
        tables["all"] = summ.iloc[0].to_dict()
        for col, name in (("imd_decile_england", "by_imd_decile_england"), ("wimd_decile_wales", "by_wimd_decile_wales")):
            t = cov.summarise(head[head[col].notna()], col)
            t.to_csv(run_dir / f"summary_{name}.csv", index=False)
            tables[name] = t
        # sensitivities for the interpolated classes: BRT at the bus and at the rail curve
        sens = {}
        for w in (0.0, 1.0):
            c2 = cov.decay_curves(obs, between | {"brt": w})
            s2 = cov.score(walk, cs[cs.period == "HEADLINE"], c2, scfg).set_index("OA21CD").reindex(oa.OA21CD)
            sens[f"brt_towards_rail_{w:g}"] = {
                f"residents_frequent_{h}": int(head.residents[s2[f"frequent_{h}"].fillna(False).astype(bool).to_numpy()].sum())
                for h in hs}
        # R5 truncates walking times to the whole minute (measured, plans/P3.md §9): the
        # same score with half a minute added back, as a sensitivity (Q14).
        s3 = cov.score(walk.assign(walk_min=walk.walk_min + 0.5), cs[cs.period == "HEADLINE"], curves, scfg) \
            .set_index("OA21CD").reindex(oa.OA21CD)
        sens["walk_plus_half_minute"] = {
            f"residents_frequent_{h}": int(head.residents[s3[f"frequent_{h}"].fillna(False).astype(bool).to_numpy()].sum())
            for h in hs} | {"mean_score": round(float((s3.score.fillna(0).to_numpy() * head.residents).sum() / head.residents.sum()), 2)}
        cancelled = gtfs_rail.cancellations(
            cfg.root / raw["rail"]["darwin_timetable"], gtfs_rail.read_ref(cfg.root / raw["rail"]["darwin_ref"]),
            gtfs_rail.read_naptan(cfg.root / raw["rail"]["naptan"]),
            raw["modelled_date"] if isinstance(raw["modelled_date"], dt.date)
            else dt.date.fromisoformat(raw["modelled_date"]), cfg.extent)
        day = raw["modelled_date"]
        label = (f"Frequent-service coverage. Timetabled service on {day:%a %-d %b %Y} "
                 f"(the rail timetable is the on-the-day snapshot: it omits "
                 f"{cancelled.get('journeys_cancelled', 0)} cancelled journeys and "
                 f"{cancelled.get('calls_cancelled', 0)} cancelled calls at stations inside the extent). "
                 f"Walking on the network at {kmh:g} km/h, "
                 f"{'without gradient' if fn is None else 'with gradient (' + fn.title() + ')'}; "
                 f"whole-minute walking times. Service level = the worse of "
                 f"{' and '.join(ps['coverage.headline_periods'])}, departures in one direction. "
                 "No car times are used, so the P2 car spot-check fails do not apply. "
                 "Walk-to-stop distances are placeholders (not yet sourced). "
                 "Sketch-planning model: indicative and comparative, not for a business case.")
        g = gpd.GeoDataFrame(head.merge(geom, on="OA21CD"), geometry="geometry", crs=geom.crs)
        g.insert(0, "network_version", nv)
        g.to_parquet(run_dir / "coverage_oa.parquet")
        with duckdb.connect(str(cfg.lab_db)) as con:
            con.register("df_", pd.DataFrame(g.drop(columns="geometry")))
            con.execute("CREATE OR REPLACE TABLE coverage_oa AS SELECT * FROM df_")
        _coverage_map(g, hs, label, run_dir / "coverage_map.png")
        (run_dir / "label.txt").write_text(label + "\n")
        a = tables["all"]
        res = {
            "network_version": nv, "label": label, "totals": tot, "jobs_split": jobs_info,
            "decay_curves": {m: {k: round(v, 1) if isinstance(v, float) else v for k, v in c.items()}
                             for m, c in curves.items()},
            "exponential_misfit_p85_m": {m: round(cov.fit_exponential(**obs[m])["fit_p85_m"]) for m in obs},
            "reduced_mobility_scale": round(rm_scale, 3),
            "residents_by_class_headline": {c: int(a[f"residents_class_{c}"]) for c in cov.CLASS_ORDER},
            "mean_score_headline": round(float(a["mean_score_resident_weighted"]), 2),
            "rounding_effect": {m: cov.rounding_effect(curves[m], kmh, obs[m]["p85_m"]) for m in ("bus", "rail")},
            "sensitivity": sens, "rail_cancellations_inside_extent": cancelled,
        }
        for h in hs:
            f = f"frequent_{h}"
            res[f] = {
                "residents_served": int(a[f"residents_{f}"]), "residents_unserved": int(a[f"residents_not_{f}"]),
                "share_served": round(float(a[f"share_residents_{f}"]), 4),
                "decay_weighted_residents": round(float(a[f"weighted_residents_{f}"])),
                "jobs_served": round(float(a[f"jobs_{f}"])), "share_jobs_served": round(float(a[f"jobs_{f}"]) / tot["jobs"], 4),
                "residents_served_reduced_mobility": int(head.residents[head[f + "_reduced_mobility"]].sum()),
                **{f"residents_served_{per}": int(head.residents[head[f"{f}_{per}"]].sum())
                   for per in ("AM", "IP", "PM", "EVE", "AMPH")},
                "share_served_by_imd_decile_england": {int(r["imd_decile_england"]): round(float(r[f"share_residents_{f}"]), 3)
                                                       for r in tables["by_imd_decile_england"].to_dict("records")},
                "share_served_by_wimd_decile_wales": {int(r["wimd_decile_wales"]): round(float(r[f"share_residents_{f}"]), 3)
                                                      for r in tables["by_wimd_decile_wales"].to_dict("records")},
            }
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    click.echo(json.dumps(res, indent=1, default=str))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


def _coverage_map(g, hs: list, label: str, out: Path) -> None:
    import textwrap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    fig, ax = plt.subplots(1, 2, figsize=(17, 8.2))
    # Service-quality class: one sequential hue, light = poor (colour-blind safe).
    cls = {"A": "#08306b", "B": "#2171b5", "C": "#6baed6", "D": "#c6dbef", "none": "#eeeeee"}
    g.plot(ax=ax[0], color=g.are_class.map(cls), linewidth=0)
    ax[0].legend(handles=[Patch(color=v, label=k) for k, v in cls.items()], title="class", loc="lower left", frameon=False)
    ax[0].set_title("Service-quality class (after the Swiss ARE method), worse of AM and inter-peak")
    lo, hi = max(hs), min(hs)
    col = {2: "#1b7837", 1: "#a6dba0", 0: "#eeeeee"}
    lvl = g[f"frequent_{hi}"].astype(int) + g[f"frequent_{lo}"].astype(int)
    g.plot(ax=ax[1], color=lvl.map(col), linewidth=0)
    ax[1].legend(handles=[Patch(color=col[2], label=f"every {hi} min or better"),
                          Patch(color=col[1], label=f"every {lo} min or better"),
                          Patch(color=col[0], label="no frequent service in walking reach")],
                 loc="lower left", frameon=False)
    ax[1].set_title("Frequent service within the 85th-percentile walk for its mode")
    for a in ax:
        a.set_axis_off()
        a.set_aspect(1.6)
    fig.text(0.01, 0.01, "\n".join(textwrap.wrap(label, 210)), fontsize=7.5, va="bottom")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(out, dpi=140)
    plt.close(fig)
