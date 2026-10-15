"""Kept investigations whose fits back a tagged parameter."""
from __future__ import annotations

from pathlib import Path

import click

from .. import params, runrecord
from ..config import LabConfig

@click.group()
def spike() -> None:
    """Kept investigations whose fits back a tagged parameter. The P2a spikes are in git tag archive/pre-v1-scope."""


@spike.command("walk-faults")
@click.option("--stops-run", default=None, help="A `coverage stops` run directory (default: the latest).")
def spike_walk_faults(stops_run: str | None) -> None:
    """Stop pairs cut by the barrier test: classify, plot the artefact candidates, draft links."""
    import json
    import zipfile
    import pandas as pd
    import yaml
    from ..spikes import walk_faults as wf
    from ..supply import feeds
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    src = Path(stops_run) if stops_run else sorted(cfg.runs_dir.glob("*-coverage-stops-*"))[-1]
    pairs = pd.read_csv(src / "cluster_pairs_cut_by_barrier_test.csv")
    with zipfile.ZipFile(cfg.root / raw["bus"]["out"]) as z:
        stops = pd.read_csv(z.open("stops.txt"), dtype=str).set_index("stop_id")
    stops = stops.rename(columns={"stop_lon": "lon", "stop_lat": "lat"}).astype({"lon": float, "lat": float})
    osm = feeds.get(cfg, "osm_clip")
    rec = runrecord.build(cfg, command="spike-walk-faults",
                          inputs=[{"name": "osm_clip", "sha256": osm["sha256"]},
                                  {"name": str(src / "cluster_pairs_cut_by_barrier_test.csv"),
                                   "sha256": params.file_hash(src / "cluster_pairs_cut_by_barrier_test.csv")}])
    run_dir = runrecord.write(cfg, rec).parent
    recs = wf.analyse(pairs, stops, str(cfg.root / raw["osm"]["clip"]))
    art = [r for r in recs if r["kind"] == "separately mapped path"]
    wf.plot(art, run_dir / "walk_fault_candidates.png")
    patch = wf.draft_patch(recs, raw["osm"]["extract_date"])
    (run_dir / "walk_links.draft.geojson").write_text(json.dumps(patch, indent=1))
    draft = cfg.root / raw["osm"]["walk_patch_draft"]
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(json.dumps(patch, indent=1))
    (run_dir / "pairs.json").write_text(json.dumps(
        [{k: v for k, v in r.items() if k != "ways"} for r in recs], indent=1, default=str))
    rec["result"] = {"pairs": len(recs), "by_kind": pd.Series([r["kind"] for r in recs]).value_counts().to_dict(),
                     "links_drafted": len(patch["features"]),
                     "artefacts_with_nothing_drafted": [r["name"] for r in art if not r.get("links")],
                     "draft": str(draft), "applied": False,
                     "pairs_list": [{"name": r["name"], "kind": r["kind"], "dist_m": round(r["dist_m"]),
                                     "walk_min": r["walk_min"],
                                     "links_m": [ln["length_m"] for ln in r.get("links", [])]} for r in recs]}
    click.echo(json.dumps(rec["result"], indent=1))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@spike.command("profile-allowance")
@click.option("--accel", default=0.5, show_default=True, help="Acceleration of the profile being tested, m/s².")
@click.option("--min-tagged", default=0.8, show_default=True, help="Share of a section's track that must carry a line speed.")
@click.option("--folds", default=5, show_default=True)
def spike_profile_allowance(accel: float, min_tagged: float, folds: int) -> None:
    """The speed-profile rule against Darwin's working times on every timed section; fits the allowance."""
    import json
    import duckdb
    import yaml
    from ..spikes import profile_allowance as pa
    from ..supply import feeds, gtfs_rail as g, rail_geometry as rg
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    rec = runrecord.build(cfg, command="spike-profile-allowance", inputs=[
        {"name": k, "sha256": feeds.get(cfg, k)["sha256"]} for k in ("osm_clip", "darwin_timetable", "naptan_rail")])
    run_dir = runrecord.write(cfg, rec).parent
    try:
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            tp = con.execute("SELECT * FROM rail_timing_point").df()
        nap = g.read_naptan(cfg.root / raw["rail"]["naptan"])
        coords = {t: (v[0], v[1]) for t, v in nap.items()}
        sec = pa.sections(tp, ps["scenario.working_time_min_trains"])
        G = rg.rail_graph(str(cfg.root / raw["osm"]["clip"]), {"rail"})
        d = pa.with_profile(sec, coords, G, accel, ps["scenario.draw_snap_m"], min_tagged)
        d["timetable_over_profile"] = d.timetable_s / d.profile_s
        d.to_csv(run_dir / "sections.csv", index=False)
        f = pa.fit(d)
        res = {"accel_ms2": accel, "sections_with_enough_trains": int(len(sec)), "sections_compared": int(len(d)),
               "why_fewer": "a station outside the clip box (no coordinate), no track path, or a line speed on "
                            f"under {min_tagged:.0%} of the track",
               "timetable_over_profile": {k: round(float(v), 3) for k, v in d.timetable_over_profile.describe().items()},
               "fit": {k: round(v, 4) for k, v in f.items()}, "evaluation": pa.evaluate(d, folds),
               "by_accel": {str(a): {k: round(v, 4) for k, v in pa.fit(pa.with_profile(
                   sec, coords, G, a, ps["scenario.draw_snap_m"], min_tagged)).items()} for a in (0.3, 0.5, 0.7, 1.0)}}
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    click.echo(json.dumps(res, indent=1))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@spike.command("walk-fault-class")
@click.option("--reach-m", default=400.0, show_default=True, help="How far to search the network for a carriageway.")
def spike_walk_fault_class(reach_m: float) -> None:
    """Every stop (and OA centroid) that snaps to a separately mapped path with no link to
    the carriageway beside it; and what linking the stops would do to coverage."""
    import datetime as dt
    import json
    import duckdb
    import pandas as pd
    import yaml
    from pyproj import Transformer
    from .. import coverage as cov, network
    from ..coverage_cli import _obs, _walk_matrix
    from ..spikes import walk_faults as wf
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    nw = network.settings(cfg, raw, ps)
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    near = ps["coverage.walk_fault_link_m"]
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        stops = con.execute("SELECT stop_id, stop_name, lon, lat, cluster_id, feed FROM stop_cluster").df()
        cs = con.execute("SELECT * FROM cluster_service WHERE period = 'HEADLINE'").df()
        oa = con.execute("SELECT OA21CD, residents, jobs FROM coverage_oa ORDER BY 1").df()
        pwc = con.execute("SELECT OA21CD stop_id, OA21CD stop_name, lon, lat FROM int_oa_pwc ORDER BY 1").df()
    rec = runrecord.build(cfg, command="spike-walk-fault-class",
                          inputs=[{"name": k, "sha256": v} for k, v in nw["hashes"].items()])
    run_dir = runrecord.write(cfg, rec).parent
    try:
        g, edges = wf.walk_graph(str(cfg.root / raw["osm"]["clip"]))
        sc = wf.snap_class(stops, g, edges, near, reach_m)
        oc = wf.snap_class(pwc, g, edges, near, reach_m)
        sc.drop(columns=["road_x", "road_y"]).to_csv(run_dir / "stops_snap_class.csv", index=False)
        oc.drop(columns=["road_x", "road_y"]).to_csv(run_dir / "oa_centroids_snap_class.csv", index=False)

        def tally(o):
            f = o[o.fault_class]
            n = f.road_network_m
            return {"points": int(len(o)), "snap_to_a_path": int(o.snaps_to_path.sum()),
                    "of_those_with_a_carriageway_within_link_m": int((o.snaps_to_path & (o.road_straight_m <= near)).sum()),
                    "fault_class": int(len(f)), "share": round(len(f) / len(o), 4),
                    "by_network_distance_to_the_carriageway_m": {f"over_{c}": int(((n > c) | n.isna()).sum())
                                                                 for c in (20, 50, 100, 200)},
                    "further_than_50_m_from_any_walkable_way": int((o.snap_m > 50).sum())}
        res = {"link_m": near, "stops": tally(sc), "oa_centroids": tally(oc)}
        f = sc[sc.fault_class]
        # (b) what linking the stops would do: the fault-class stops moved onto the
        # carriageway beside them, walking times to them recomputed, the shorter time kept
        back = Transformer.from_crs(27700, 4326, always_xy=True).transform
        lon, lat = back(f.road_x.to_numpy(), f.road_y.to_numpy())
        moved = pd.DataFrame({"id": f.stop_id.to_numpy(), "lon": lon, "lat": lat})
        o_pts = pwc.rename(columns={"stop_id": "id"})[["id", "lon", "lat"]]
        m = _walk_matrix(str(cfg.root / raw["osm"]["clip"]), nw, o_pts, moved, ps, ps["coverage.max_walk_min"], day)
        m = m.rename(columns={"from_id": "OA21CD", "to_id": "stop_id", "travel_time": "walk_min"})
        m["walk_min"] = m.walk_min.astype(float)
        base = pd.read_parquet(cfg.root / raw["coverage"]["dir"] / nw["version"] / "oa_stop_walk.parquet")[
            ["OA21CD", "stop_id", "walk_min"]]
        both = pd.concat([base, m], ignore_index=True).groupby(["OA21CD", "stop_id"], as_index=False).walk_min.min()
        j = base.merge(m, on=["OA21CD", "stop_id"], suffixes=("", "_linked"))
        res["effect_on_walking_times"] = {
            "pairs_with_a_fault_class_stop": int(len(j)),
            "pairs_shorter_once_linked": int((j.walk_min_linked < j.walk_min).sum()),
            "mean_saving_min_where_shorter": round(float((j.walk_min - j.walk_min_linked)[j.walk_min_linked < j.walk_min].mean()), 2),
            "pairs_newly_within_the_walk_cap": int(len(m) - len(j))}
        obs, between = _obs(ps)
        curves = cov.decay_curves(obs, between)
        scfg = {"walk_kmh": ps["routing.walk_speed_kmh"], **{k.split(".", 1)[1]: ps[k] for k in (
            "coverage.frequent_headway_min", "coverage.score_cap_dph", "coverage.are_interval_bands_min",
            "coverage.are_stop_category", "coverage.are_distance_bands_m", "coverage.are_class",
            "coverage.rail_node_min_directions")}, "served_cut_min": dict(ps["coverage.served_walk_min"])}
        cl = stops.set_index("stop_id").cluster_id

        def served(w):
            w = w.assign(cluster_id=w.stop_id.map(cl)).groupby(["OA21CD", "cluster_id"], as_index=False).walk_min.min()
            w["walk_min"] += ps["coverage.walk_truncation_correction_min"]
            s_ = cov.score(w, cs, curves, scfg).set_index("OA21CD").reindex(oa.OA21CD)
            return {f"frequent_{h}": int(oa.residents[s_[f"frequent_{h}"].fillna(False).astype(bool).to_numpy()].sum())
                    for h in ps["coverage.frequent_headway_min"]}
        a, b = served(base), served(both)
        res["effect_on_headline_coverage"] = {"residents_served_now": a, "residents_served_with_stops_linked": b,
                                              "difference": {k: b[k] - a[k] for k in a},
                                              "note": "stops only: the same fault at OA centroids and at "
                                                      "stops met mid-journey is not corrected here"}
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    click.echo(json.dumps(res, indent=1))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")
