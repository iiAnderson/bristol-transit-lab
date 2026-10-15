"""Car congestion calibration (frozen: see README)."""
from __future__ import annotations


import click

from .. import params, runrecord
from ..config import LabConfig
from .supply import _env_secret

@click.group()
def congestion() -> None:
    """P2 car congestion: inputs, calibration and validation."""


@congestion.command("network")
def congestion_network() -> None:
    """Car segment table from the OSM clip, annotated (authority, area type, class, direction, B1)."""
    import duckdb
    import yaml
    from ..congestion import annotate as an, network as nw
    from ..supply import feeds
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    d = cfg.root / "data" / "interim" / "congestion"
    geo = cfg.root / "data" / "raw" / "ons_geo"
    for fid in ("osm_clip", "ons_lsoa21_bgc_internal", "ons_ruc21_lsoa", "dft_aadf_by_direction"):
        feeds.check_file_unchanged(feeds.get(cfg, fid))
    rec = runrecord.build(cfg, command="congestion-network", inputs=[
        {"name": f, "sha256": feeds.get(cfg, f)["sha256"]}
        for f in ("osm_clip", "ons_lsoa21_bgc_internal", "ons_ruc21_lsoa", "dft_aadf_by_direction")]
        + [{"name": str(geo / "lad24_bgc_extent.geojson"),
            "sha256": params.file_hash(geo / "lad24_bgc_extent.geojson")}])
    runrecord.write(cfg, rec)
    try:
        seg = nw.build_segments(cfg.root / raw["osm"]["clip"], d / "segments.parquet")
        with duckdb.connect() as con:
            con.execute("INSTALL spatial; LOAD spatial")
            con.execute(f"ATTACH '{cfg.lab_db}' AS lab (READ_ONLY)")
            cl = an.centre_lsoas(con, geo / "lsoa21_bgc_internal.geojson",
                                 cfg.root / "data/raw/ons/ruc21_lsoa_ew.csv",
                                 ps["area_type.centre_job_density"], raw["centre_min_cluster_lsoas"])
            res = an.annotate(con, d / "segments.parquet", geo / "lad24_bgc_extent.geojson",
                              geo / "lsoa21_bgc_internal.geojson",
                              cfg.root / "data/raw/ons/ruc21_lsoa_ew.csv",
                              cfg.root / "data/interim/aadf_by_direction_clip.parquet",
                              raw["centres"], cl, d / "segments_annotated.parquet")
            res["propagation"] = an.apply_propagation(con, d / "segments_annotated.parquet",
                                                      d / "segments_annotated.parquet")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {**seg, **res, "centre_lsoas": cl}
    for k, v in rec["result"].items():
        click.echo(f"  {k}: {v}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@congestion.command("srn")
@click.option("--max-match-m", default=150.0, show_default=True)
def congestion_srn(max_match_m: float) -> None:
    """B3: WebTRIS speeds by site × period (neutral days), matched to SRN segments."""
    import duckdb
    import yaml
    from ..congestion import webtris as w
    from ..supply import avl as a
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    periods = {p: tuple(ps[f"periods.{p}"].split("-")) for p in ("AM", "IP", "PM")}
    wc = raw["webtris"]
    d = cfg.root / "data" / "interim" / "congestion"
    rec = runrecord.build(cfg, command="congestion-srn")
    runrecord.write(cfg, rec)
    try:
        sites = w.site_table(cfg.root / wc["dir"] / "sites_all.json", a.clip_box(cfg))
        days = w.day_types(raw["calendar_2025_26"], wc["start"], wc["end"])
        with duckdb.connect() as con:
            res = w.process(con, cfg.root / wc["dir"], sites, days, periods,
                            d / "segments_annotated.parquet", max_match_m,
                            am_peak_hour=tuple(ps["skims.am_peak_hour"].split("-")))
            con.execute(f"COPY wspeed TO '{d / 'webtris_speed.parquet'}' (FORMAT parquet)")
            con.execute(f"""COPY (SELECT s.*, m.way_id, m.seq, m.forward, m.u, m.v, m.match_m
                FROM wsite s LEFT JOIN wmatch m USING (site_id))
                TO '{d / 'webtris_sites.parquet'}' (FORMAT parquet)""")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    for k, v in res.items():
        click.echo(f"  {k}: {v}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@congestion.command("avl")
@click.option("--day", "days", multiple=True, required=True, help="YYYY-MM-DD; repeatable.")
@click.option("--source", type=click.Choice(["archive", "live"]), default="archive",
              show_default=True)
@click.option("--snapshot-s", type=int, default=None,
              help="Spacing-bias test: thin to the latest position per vehicle per N s of "
                   "poll time; writes traversals_<source><N>_<day>.parquet.")
@click.option("--set", "overrides", multiple=True, metavar="KEY=VALUE",
              help="Sensitivity: override a congestion.* parameter for this run (needs --tag).")
@click.option("--tag", default=None, help="Sensitivity: suffix for the traversal files "
              "(traversals_<source>-<tag>_<day>.parquet), so the base files are untouched.")
def congestion_avl(days: tuple[str, ...], source: str, snapshot_s: int | None,
                   overrides: tuple[str, ...], tag: str | None) -> None:
    """B2: map-match one or more closed AVL days; write per-day segment traversals."""
    import zipfile
    import duckdb
    import yaml
    from ..congestion import avl_speeds as av
    from ..supply import feeds, osrm
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    p = {k.split(".", 1)[1]: v for k, v in ps.items() if k.startswith("congestion.")}
    if overrides and not tag:
        raise click.ClickException("--set needs --tag, so base traversals are not overwritten")
    for o in overrides:
        k, v = o.split("=", 1)
        if k not in p:
            raise click.ClickException(f"unknown congestion parameter {k}")
        p[k] = float(v) if not float(v).is_integer() else type(p[k])(float(v))
    periods = {k: tuple(ps[f"periods.{k}"].split("-")) for k in ("AM", "IP", "PM")}
    d = cfg.root / "data" / "interim" / "congestion"
    busways = d / "busway_points.parquet"
    if not busways.is_file():
        av.busway_points(cfg.root / raw["osm"]["clip"], busways)
    stops = d / "bus_stops.parquet"
    with zipfile.ZipFile(cfg.root / raw["bus"]["out"]) as z:
        (d / "_stops.txt").write_bytes(z.read("stops.txt"))
    duckdb.connect().execute(f"""COPY (SELECT stop_lon::DOUBLE lon, stop_lat::DOUBLE lat
        FROM read_csv('{d / '_stops.txt'}', all_varchar=true)) TO '{stops}' (FORMAT parquet)""")
    (d / "_stops.txt").unlink()
    avl_dir = cfg.root / raw["paths"]["avl"] / source
    stem = "sirivm" if source == "archive" else "sirivm_live"
    base = cfg.root / raw["osm"]["osrm_base"]
    osrm.customize(base)                                  # free-flow speeds for matching
    rec = runrecord.build(cfg, command="congestion-avl", inputs=[
        {"name": str(avl_dir / f"{stem}_{x}.parquet"),
         "sha256": params.file_hash(avl_dir / f"{stem}_{x}.parquet")} for x in days]
        + [{"name": "bus_gtfs", "sha256": feeds.get(cfg, "bus_gtfs")["sha256"]},
           {"name": "osrm", "version": osrm.version()}])
    runrecord.write(cfg, rec)
    res = {}
    try:
        with osrm.Server(base) as srv:
            for x in days:
                res[x] = av.process_day(avl_dir / f"{stem}_{x}.parquet", srv.port, stops,
                                        busways, raw["metrobus"], p, periods,
                                        raw["avl"]["timezone"],
                                        d / "avl" / f"traversals_{source}{snapshot_s or ''}{'-' + tag if tag else ''}_{x}.parquet",
                                        progress=lambda m: click.echo(f"    {x}: {m}", err=True),
                                        snapshot_s=snapshot_s)
                click.echo(f"  {x}: {res[x]}")
    except Exception:
        rec["result"] = res
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {**res, "overrides": list(overrides), "tag": tag}
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


def _anpr_inputs(cfg) -> dict | None:
    """ANPR constraint inputs from `lab congestion anpr-prep`, if present."""
    import pandas as pd
    d = cfg.root / "data" / "interim" / "congestion"
    if not (d / "anpr_paths.parquet").is_file():
        return None
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    paths = pd.read_parquet(d / "anpr_paths.parquet")
    ratio = (paths.groupby("link_id")["route_m"].first()
             / paths.groupby("link_id")["link_m"].first())
    bad = ratio[(ratio - 1).abs() > ps["congestion.validation_max_len_diff"]]
    return {"paths": paths[~paths["link_id"].isin(bad.index)],
            "obs": pd.read_parquet(d / "anpr_obs.parquet"),
            "signals": set(pd.read_parquet(d / "signal_nodes.parquet")["node"]),
            "scope": (set(pd.read_parquet(d / "anpr_scope.parquet").query("in_scope")["LSOA21CD"])
                      if (d / "anpr_scope.parquet").is_file() else None),
            "excluded": [{"link_id": k, "route_over_link": round(float(v), 3)}
                         for k, v in bad.items()]}


def _calibration_inputs(cfg, raw, trav_files) -> dict:
    """Everything `lab congestion calibrate` and `validate` read, loaded once."""
    import duckdb
    import pandas as pd
    from ..congestion import calibrate as cal, fit, targets as tg
    from ..supply import avl as a
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    periods = {k: tuple(ps[f"periods.{k}"].split("-")) for k in ("AM", "IP", "PM")}
    d = cfg.root / "data" / "interim" / "congestion"
    dc = cfg.root / "data" / "raw" / "dft_congestion"
    targets = tg.dft_targets(dc / "cgn0503.ods", dc / "cgn0509.ods")
    targets = targets[targets["lad"].isin(raw["authorities"].values())]
    cov = tg.coverage(duckdb.connect(), d / "main_roads_full.parquet",
                      cfg.root / "data/raw/ons_geo/lad24_bgc_extent.geojson",
                      cfg.root / "data/interim/aadf_by_direction_clip.parquet", a.clip_box(cfg))
    year, prof = tg.tra0307_profile(
        cfg.root / "data/raw/dft_traffic/tra0307-traffic-distribution-by-time-of-day.ods")
    months = [f"{m} 2025" for m in ("April", "May", "June", "July", "August", "September",
                                    "October", "November", "December")] + \
             [f"{m} 2026" for m in ("January", "February", "March")]
    # leg ids restart each day: offset them so legs stay distinct across days
    cat = lambda fs: pd.concat([pd.read_parquet(f).assign(leg=lambda x, i=i: x["leg"] + i * 10**7)  # noqa: E731
                                for i, f in enumerate(fs)], ignore_index=True)
    trav = cat(trav_files)
    # Zero-distance legs (both fixes snapped to one point) have no speed: they zero the
    # harmonic means. Dropped here for traversal files written before avl_speeds did.
    zero = trav["kmh"] <= 0
    click.echo(f"  dropped {int(zero.sum())} zero-distance traversal rows "
               f"({trav.loc[zero, 'leg'].nunique()} legs)", err=True)
    live = sorted((d / "avl").glob("traversals_live_*.parquet"))
    trav_pm = cat(live) if live else None
    return {"seg": pd.read_parquet(d / "segments_annotated.parquet"),
            "trav": trav[~zero].reset_index(drop=True),
            "trav_pm": None if trav_pm is None else trav_pm[trav_pm["kmh"] > 0],
            "live_files": [f.name for f in live],
            "wspeed": pd.read_parquet(d / "webtris_speed.parquet"),
            "wsites": pd.read_parquet(d / "webtris_sites.parquet"),
            "targets": targets, "cov": cov, "P": fit.period_weights(prof, periods),
            "national": cal.dft_national_ratios(str(dc / "cgn0503.ods"), months),
            "year": year, "periods": periods}


@congestion.command("calibrate")
@click.option("--days", default=None, help="Comma-separated AVL days to use (default: all "
              "closed archive days).")
def congestion_calibrate(days: str | None) -> None:
    """P2b: fit per-period link speeds; write link_speed and OSRM speed files."""
    import json
    import yaml
    from ..congestion import calibrate as cal
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    p = {k.split(".", 1)[1]: v for k, v in ps.items() if k.startswith("congestion.")}
    ffp = {k.split(".", 1)[1]: v for k, v in ps.items() if k.startswith("free_flow.")}
    periods = {k: tuple(ps[f"periods.{k}"].split("-")) for k in ("AM", "IP", "PM")}
    d = cfg.root / "data" / "interim" / "congestion"
    avl_dir = cfg.root / raw["paths"]["avl"] / "archive"
    closed = [json.loads(x)["day"] for x in (avl_dir / "days.jsonl").read_text().splitlines()]
    use_days = days.split(",") if days else closed
    trav_files = [d / "avl" / f"traversals_archive_{x}.parquet" for x in use_days]
    missing = [f.name for f in trav_files if not f.is_file()]
    if missing:
        raise click.ClickException(f"run `lab congestion avl` first for: {missing}")
    rec = runrecord.build(cfg, command="congestion-calibrate", inputs=[
        {"name": str(f), "sha256": params.file_hash(f)} for f in
        [d / "segments_annotated.parquet", d / "webtris_speed.parquet",
         d / "webtris_sites.parquet", *trav_files]])
    runrecord.write(cfg, rec)
    try:
        inp = _calibration_inputs(cfg, raw, trav_files)
        seg, trav, wspeed, wsites, targets, cov, P, national, year = (
            inp[k] for k in ("seg", "trav", "wspeed", "wsites", "targets", "cov", "P",
                             "national", "year"))
        anpr = _anpr_inputs(cfg)
        ls, ls_base, rep = cal.run(seg, trav, wspeed, wsites, targets, cov, P, p, ffp, national,
                          p["target_min_coverage"], p["fit_ridge_lambda"],
                          p["fit_road_ridge_lambda"], anpr, inp["trav_pm"])
        rep["pm_shape_live_files"] = inp["live_files"]
        ls.to_parquet(d / "link_speed.parquet", compression="zstd")
        ls_base.to_parquet(d / "link_speed_base.parquet", compression="zstd")
        files = cal.write_speed_files(ls, d / "osrm_speeds")
        cal.write_speed_files(ls_base, d / "osrm_speeds_base")
        rep.update({"days": use_days, "tra0307_year": year, "period_weights": P,
                    "speed_files": {k: str(v) for k, v in files.items()}})
        (cfg.runs_dir / rec["run_id"] / "calibration.json").write_text(
            json.dumps(rep, indent=1, default=str))
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {k: v for k, v in rep.items() if k not in ("shape",)}
    rec["result"]["level"] = {k: v for k, v in rep["level"].items() if k != "targets"}
    rec["result"]["leave_one_road_out"] = {k: v for k, v in rep["leave_one_road_out"].items()
                                           if k != "roads"}
    click.echo(f"  period weights {({k: round(v, 3) for k, v in P.items()})}")
    click.echo(f"  srn {rep['srn']}")
    click.echo(f"  rho {rep['shape']['rho']}  national {national}")
    click.echo(f"  base g {rep['base']['g']}")
    b, h, lo = rep["base"], rep["level"], rep["leave_one_road_out"]
    click.echo(f"  base: fitted params {b['n_params_fitted_to_dft']} (edf {b['edf']:.1f}), "
               f"median |err| {b['median_abs_rel_error']:.3f}, p90 {b['p90_abs_rel_error']:.3f}")
    click.echo(f"  hybrid: params {h['n_params']} (edf {h['edf']:.1f}), median |err| "
               f"{h['median_abs_rel_error']:.3f}, within 5% {h['share_within_5pct']:.2f}")
    click.echo(f"  leave-one-road-out: median |err| {lo['median_abs_rel_error']:.3f}, p90 "
               f"{lo['p90_abs_rel_error']:.3f}, pass (<=15%): {lo['passes_15pct_median']}")
    click.echo(f"  median factor by period {rep['median_factor']}")
    if "anpr_layer" in rep:
        click.echo(f"  ANPR layer (hybrid): {rep['anpr_layer']['hybrid']}")
        for k, v in rep["anpr"].items():
            if k.startswith("hybrid"):
                click.echo(f"  ANPR {k}: n {v['n']}, modelled/observed {v['median_ratio']:.3f}, "
                           f"median |err| {v['median_abs_rel_error']:.3f}")
        da = rep["dft_after_layer"]
        click.echo(f"  DfT all-day after the layer, by scope: {da['by_scope']}")
        click.echo(f"  DfT all-day after the layer, by authority: {da['by_authority']}")
        l2 = rep["leave_one_road_out_after_layer"]
        click.echo(f"  leave-one-road-out after the layer: median |err| "
                   f"{l2['median_abs_rel_error']:.3f}, p90 {l2['p90_abs_rel_error']:.3f}; "
                   f"by scope after {l2['by_scope']} before {l2['before_by_scope']}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@congestion.command("anpr-prep")
@click.option("--block-km", default=1.0, show_default=True)
def congestion_anpr_prep(block_km: float) -> None:
    """ANPR inputs: free-flow link paths, a spatial calibration/validation split balanced
    within each area type, observed times per period (hour stamp per config)."""
    import numpy as np
    import yaml
    from ..congestion import signals as sg
    from ..supply import osrm
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    d = cfg.root / "data" / "interim" / "congestion"
    an = cfg.root / "data" / "raw" / "bristol_anpr"
    base = cfg.root / raw["osm"]["osrm_base"]
    osrm.customize(base)                                  # free flow for path finding
    rec = runrecord.build(cfg, command="congestion-anpr-prep")
    runrecord.write(cfg, rec)
    with osrm.Server(base) as srv:
        paths = sg.link_paths(an / "journey_links.geojson", srv.port)
    import pandas as pd
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    periods = {k: tuple(ps[f"periods.{k}"].split("-")) for k in ("AM", "IP", "PM")}
    seg = pd.read_parquet(d / "segments_annotated.parquet",
                          columns=["u", "v", "area_type", "length_m"]).drop_duplicates(["u", "v"])
    seg_area = seg.rename(columns={"area_type": "area"})
    seg_area["area"] = seg_area["area"].replace({"buffer": "rural"})
    area = sg.link_area(paths, seg_area)
    split = sg.split_links_balanced(paths, area, block_km)
    paths["half"] = paths["link_id"].map(split)
    stamp = raw["anpr"]["hour_stamp"]
    obs = sg.anpr_obs([an / "journey_counts_2023.parquet", an / "journey_counts_2024.parquet"],
                      raw["avl"]["timezone"], periods, stamp)
    obs["half"] = obs["link_id"].map(split)
    obs["area"] = obs["link_id"].map(area)
    sig = sg.signal_nodes(cfg.root / raw["osm"]["clip"])
    # where the layer applies: the built-up area in config (plotted for review)
    import duckdb
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ..congestion import annotate as an_
    geo = cfg.root / "data" / "raw" / "ons_geo"
    ruc = cfg.root / "data/raw/ons/ruc21_lsoa_ew.csv"
    sc = raw["anpr"]["layer_scope"]
    with duckdb.connect() as con:
        con.execute("INSTALL spatial; LOAD spatial")
        con.execute(f"ATTACH '{cfg.lab_db}' AS lab (READ_ONLY)")
        cl = an_.centre_lsoas(con, geo / "lsoa21_bgc_internal.geojson", ruc,
                              ps["area_type.centre_job_density"], raw["centre_min_cluster_lsoas"])
        scope = an_.builtup_scope(con, geo / "lsoa21_bgc_internal.geojson", ruc,
                                  geo / "lad24_bgc_extent.geojson", cfg.root / sc["bua"],
                                  "lab.lsoa_pwc", cl, sc["seed_bua"],
                                  sc["core_lad"], sc["fringe_lads"])
    scope.to_parquet(d / "anpr_scope.parquet")
    zg = gpd.read_file(geo / "lsoa21_bgc_internal.geojson")[["LSOA21CD", "geometry"]] \
        .merge(scope, on="LSOA21CD")
    zg["cls"] = np.where(zg["in_scope"], "in scope: " + zg["area_type"],
                         "outside: " + zg["area_type"])
    fig, ax = plt.subplots(figsize=(11, 10))
    zg.plot(column="cls", ax=ax, legend=True, linewidth=0.1, edgecolor="white", cmap="tab20",
            legend_kwds={"loc": "lower left", "fontsize": 8})
    lk = gpd.read_file(an / "journey_links.geojson")
    lk.plot(ax=ax, color="black", linewidth=0.8)
    ax.set_title(raw["anpr"]["labels"]["scope_plot_title"])
    ax.set_axis_off()
    fig.savefig(cfg.runs_dir / rec["run_id"] / "anpr_layer_scope.png", dpi=150, bbox_inches="tight")
    paths.to_parquet(d / "anpr_paths.parquet")
    obs.to_parquet(d / "anpr_obs.parquet")
    pd.DataFrame({"node": sorted(sig)}).to_parquet(d / "signal_nodes.parquet")
    res = {"links_routed": int(paths["link_id"].nunique()), "hour_stamp": stamp,
           "by_half": split.value_counts().to_dict(),
           "links_with_obs_by_area_half": obs[obs["period"] == "IP"]
           .groupby(["area", "half"]).size().to_dict(),
           "obs_by_period": obs.groupby(["period", "half"]).size().to_dict(),
           "signal_nodes": len(sig), "block_km": block_km,
           "scope_lsoas": scope[scope["in_scope"]].groupby(["lad", "area_type"]).size().to_dict(),
           "outside_scope_urban_lsoas": scope[~scope["in_scope"] & (scope["area_type"] != "rural")]
           .groupby("lad").size().to_dict(),
           "scope_built_up_areas": scope.attrs["bua_members"]}
    rec["result"] = {k: {str(kk): vv for kk, vv in v.items()} if isinstance(v, dict) else v
                     for k, v in res.items()}
    click.echo(rec["result"])
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@congestion.command("validate")
@click.option("--days", default=None, help="Comma-separated AVL days (default: all closed).")
@click.option("--trav-tag", default=None, help="Sensitivity: use traversals written by "
              "`lab congestion avl --tag`.")
@click.option("--set", "overrides", multiple=True, metavar="KEY=VALUE",
              help="Sensitivity: override a congestion.* parameter for this run.")
def congestion_validate(days: str | None, trav_tag: str | None, overrides: tuple[str, ...]) -> None:
    """P2b held-out validation (spatially blocked bus speeds; ANPR 2023–24), hybrid vs base,
    against the pre-registered rule."""
    import json
    import numpy as np
    import yaml
    from ..congestion import calibrate as cal, validate as va
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    p = {k.split(".", 1)[1]: v for k, v in ps.items() if k.startswith("congestion.")}
    ffp = {k.split(".", 1)[1]: v for k, v in ps.items() if k.startswith("free_flow.")}
    d = cfg.root / "data" / "interim" / "congestion"
    avl_dir = cfg.root / raw["paths"]["avl"] / "archive"
    closed = [json.loads(x)["day"] for x in (avl_dir / "days.jsonl").read_text().splitlines()]
    use_days = days.split(",") if days else closed
    for o in overrides:
        k_, v_ = o.split("=", 1)
        if k_ not in p:
            raise click.ClickException(f"unknown congestion parameter {k_}")
        p[k_] = float(v_) if not float(v_).is_integer() else type(p[k_])(float(v_))
    trav_files = [d / "avl" / f"traversals_archive{'-' + trav_tag if trav_tag else ''}_{x}.parquet"
                  for x in use_days]
    rec = runrecord.build(cfg, command="congestion-validate", inputs=[
        {"name": str(f), "sha256": params.file_hash(f)} for f in trav_files])
    runrecord.write(cfg, rec)
    try:
        inp = _calibration_inputs(cfg, raw, trav_files)

        def run_cal(trav):
            ls, ls_b, rep = cal.run(inp["seg"], trav, inp["wspeed"], inp["wsites"],
                                    inp["targets"], inp["cov"], inp["P"], p, ffp,
                                    inp["national"], p["target_min_coverage"],
                                    p["fit_ridge_lambda"], p["fit_road_ridge_lambda"],
                                    _anpr_inputs(cfg), inp["trav_pm"])
            return ls, ls_b, rep
        ls_h, ls_b, rep = run_cal(inp["trav"])
        seg = inp["seg"].copy()
        # bus hold-out
        k = int(p["validation_folds"])
        fold = va.blocks(seg, p["validation_block_km"], k)
        bus = va.bus_holdout(seg, inp["trav"], fold, k, lambda t: run_cal(t)[:2],
                             p["min_obs_per_link"])
        bus_sum = {v: va.summarise(g["rel_error"]) for v, g in bus.groupby("variant")}
        bus_by = bus.groupby(["variant", "road_class", "area_type", "period"])["rel_error"] \
            .agg(n="size", median_abs=lambda e: float(e.abs().median())).reset_index()
        # data noise floor and corridor-level comparison (bus)
        nf = va.noise_floor(inp["trav"], seg, p["min_obs_per_link"])
        nf_by = nf.groupby(["road_class", "area_type"])["rel_diff"].agg(
            n="size", median_abs_half_diff="median").reset_index()
        cor = va.corridors(bus, seg)
        cor_sum = {v: va.summarise(g["rel_error"]) for v, g in cor.groupby("variant")}
        # ANPR validation half (the calibration half fed the ANPR layer); the rule's ANPR
        # figure is the mean over the six periods of the median |error|
        an = rep.get("anpr", {})
        vper = ("AM", "AMPH", "IP", "PM", "OP", "WE")
        anpr_sum = {v: {"median_abs_rel_error": float(np.mean(
                            [an[f"{v}|{q}|validation"]["median_abs_rel_error"] for q in vper])),
                        "by_period": {q: an[f"{v}|{q}|validation"] for q in vper}}
                    for v in ("hybrid", "base")}
        excluded = rep.get("anpr_excluded_links", [])
        # pre-registered rule
        lo = rep["leave_one_road_out"]["median_abs_rel_error"]
        worse_bus = bus_sum["hybrid"]["median_abs_rel_error"] - bus_sum["base"]["median_abs_rel_error"]
        worse_anpr = anpr_sum["hybrid"]["median_abs_rel_error"] - anpr_sum["base"]["median_abs_rel_error"]
        rule = {"loro_median_le_15pct": lo <= 0.15,
                "bus_hybrid_minus_base_pp": round(100 * worse_bus, 2),
                "anpr_hybrid_minus_base_pp": round(100 * worse_anpr, 2),
                "keep_road_multipliers": bool(worse_bus <= 0.01 and worse_anpr <= 0.01)}
        rule["verdict"] = ("keep per-road adjustments" if rule["keep_road_multipliers"]
                           else "drop per-road adjustments (held-out validation worse)")
        out = cfg.runs_dir / rec["run_id"]
        bus.to_parquet(out / "bus_holdout.parquet")
        cor.to_parquet(out / "corridors.parquet")
        nf.to_parquet(out / "noise_floor.parquet")
        (out / "validation.json").write_text(json.dumps(
            {"days": use_days, "rule": rule, "leave_one_road_out": rep["leave_one_road_out"],
             "bus_cell": bus_sum, "bus_by_cell": bus_by.to_dict("records"),
             "bus_corridor": cor_sum, "noise_floor_by_class_area": nf_by.to_dict("records"),
             "noise_floor_overall": float(nf["rel_diff"].median()),
             "anpr_validation_half": anpr_sum, "anpr_all": an,
             "anpr_layer": rep.get("anpr_layer"), "dft_after_layer": rep.get("dft_after_layer"),
             "leave_one_road_out_after_layer": rep.get("leave_one_road_out_after_layer"),
             "anpr_excluded_links": excluded},
            indent=1, default=str))
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"days": use_days, "trav_tag": trav_tag, "overrides": list(overrides),
                     "leave_one_road_out": {k_: rep["leave_one_road_out"][k_] for k_ in
                                            ("median_abs_rel_error", "p90_abs_rel_error")},
                     "level_g": {k_: float(v_) for k_, v_ in rep["level"]["g"].items()},
                     "median_factor": rep["median_factor"],
                     "rule": rule, "bus_cell": bus_sum,
                     "bus_corridor": cor_sum, "noise_floor_overall": float(nf["rel_diff"].median()),
                     "noise_floor_by_class_area": nf_by.round(4).to_dict("records"),
                     "anpr_validation_half": anpr_sum, "anpr_layer": rep.get("anpr_layer")}
    click.echo(json.dumps(rec["result"], indent=1, default=str))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@congestion.command("bus-speed-ratio")
@click.option("--block-km", default=1.0, show_default=True)
@click.option("--folds", default=5, show_default=True)
def congestion_bus_speed_ratio(block_km: float, folds: int) -> None:
    """Fit bus moving speed ÷ calibrated car speed by road class × area type × period."""
    import json
    import pandas as pd
    from ..congestion import bus_ratio as br
    cfg = LabConfig.load()
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    d = cfg.root / "data" / "interim" / "congestion"
    arch = sorted((d / "avl").glob("traversals_archive_*.parquet"))
    live = sorted((d / "avl").glob("traversals_live_*.parquet"))
    inputs = [*arch, *live, d / "link_speed.parquet", d / "segments_annotated.parquet"]
    rec = runrecord.build(cfg, command="congestion-bus-speed-ratio",
                          inputs=[{"name": f.name, "sha256": params.file_hash(f)} for f in inputs])
    run_dir = runrecord.write(cfg, rec).parent
    try:
        a = pd.concat([pd.read_parquet(f, columns=["u", "v", "kmh", "period", "hour"]) for f in arch], ignore_index=True)
        lv = pd.concat([pd.read_parquet(f, columns=["u", "v", "kmh", "period", "hour"]) for f in live], ignore_index=True)
        # AM and IP from the nine archive days (07:00–16:00); the 08:00–09:00 skim hour from
        # the same; PM from the three live days, the only ones that run past 16:00
        trav = pd.concat([a[a.period.isin(["AM", "IP"])], a[a.hour == 8].assign(period="AMPH"),
                          lv[lv.period == "PM"]], ignore_index=True)
        cell = br.cells(trav, ps["congestion.min_obs_per_link"])
        car = pd.read_parquet(d / "link_speed.parquet", columns=["period", "u", "v", "speed_kmh"])
        seg = pd.read_parquet(d / "segments_annotated.parquet")
        c = br.join(cell, car, seg)
        lanes = br.fit(c[c.bus_flag], ("period",)).set_index("period").ratio.round(3).to_dict()
        c = c[~c.bus_flag]
        k = br.fit(c)
        k.to_csv(run_dir / "bus_speed_ratio.csv", index=False)
        ho = br.hold_out(c, block_km, folds, ps["scenario.bus_speed_ratio_min_cells"])
        res = {"archive_days": len(arch), "live_days": len(live), "cells": int(len(c)),
               "km_of_link_direction": round(float(c.drop_duplicates(["u", "v"]).length_m.sum() / 1000), 1),
               "ratio_by_period": br.fit(c, ("period",)).set_index("period").ratio.round(3).to_dict(),
               "ratio_by_area_period": {f"{r.area_type}|{r.period}": round(r.ratio, 3)
                                        for r in br.fit(c, ("area_type", "period")).itertuples()},
               "ratio_on_links_with_bus_lane_tags_not_used": lanes,
               "table": k.assign(ratio=k.ratio.round(3), km=k.km.round(1)).to_dict("records"),
               "hold_out": ho, "p2_held_out_bus_speed_error_per_cell": 0.27}
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    click.echo(k.assign(ratio=k.ratio.round(3), km=k.km.round(1)).to_string(index=False))
    click.echo(json.dumps({x: res[x] for x in res if x != "table"}, indent=1))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@congestion.command("spacing-bias")
def congestion_spacing_bias() -> None:
    """Bias test: bus speeds from the live days at full resolution vs thinned to 30 s
    snapshots (`lab congestion avl --source live [--snapshot-s 30]` first)."""
    import pandas as pd
    from ..congestion import spacing as sp
    cfg = LabConfig.load()
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    d = cfg.root / "data" / "interim" / "congestion"
    full = sorted((d / "avl").glob("traversals_live_*.parquet"))
    thin = sorted((d / "avl").glob("traversals_live30_*.parquet"))
    if not full or len(full) != len(thin):
        raise click.ClickException("map-match the live days at full resolution and with "
                                   "--snapshot-s 30 first")
    rec = runrecord.build(cfg, command="congestion-spacing-bias", inputs=[
        {"name": str(f), "sha256": params.file_hash(f)} for f in full + thin])
    runrecord.write(cfg, rec)
    try:
        # leg ids restart each day: offset them so legs stay distinct across days
        cat = lambda fs: pd.concat([pd.read_parquet(f).assign(leg=lambda x, i=i: x["leg"] + i * 10**7)  # noqa: E731
                                    for i, f in enumerate(fs)], ignore_index=True)
        cellsdf, res = sp.compare(cat(full), cat(thin),
                                  pd.read_parquet(d / "segments_annotated.parquet"),
                                  pd.read_parquet(d / "bus_stops.parquet"),
                                  ps["congestion.min_obs_per_link"])
        cellsdf.to_parquet(cfg.runs_dir / rec["run_id"] / "spacing_cells.parquet")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    thr = ps["congestion.spacing_bias_threshold"]
    over = [r for r in res["by_class_area"] if abs(r["median_ratio"] - 1) > thr]
    res.update({"threshold": thr, "cells_over_threshold": over,
                "correction_needed": bool(over), "days": [f.stem[-10:] for f in full]})
    rec["result"] = res
    for k in ("overall", "by_period", "by_stop_band", "by_class_area"):
        click.echo(f"  {k}: {pd.DataFrame(res[k] if isinstance(res[k], list) else [res[k]]).round(3).to_string(index=False)}")
    click.echo(f"  legs full {res['legs_full']}, thinned {res['legs_thin']}; "
               f"class × area cells beyond ±{thr:.0%}: {len(over)}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@congestion.command("webtris")
def congestion_webtris() -> None:
    """Fetch 15-minute WebTRIS data for every active site in the clip box (resumable)."""
    import yaml
    from ..supply import avl as a, webtris as w
    cfg = LabConfig.load()
    wc = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())["webtris"]
    out = cfg.root / wc["dir"]
    c = w.Client(a.user_agent(cfg, _env_secret("LAB_CONTACT_EMAIL")), wc["min_interval_s"])
    rec = runrecord.build(cfg, command="congestion-webtris", inputs=[
        {"name": "WebTRIS API", "url": w.BASE,
         "version": f"{wc['start']}..{wc['end']}"}])
    runrecord.write(cfg, rec)
    try:
        sites = w.sites_in_box(c, a.clip_box(cfg), out / "sites_all.json")
        active = [s for s in sites if s["Status"] == "Active"]
        click.echo(f"{len(sites)} sites in the clip box, {len(active)} active", err=True)
        done = []
        for i, s in enumerate(active, 1):
            r = w.fetch_site(c, s, wc["start"], wc["end"], out)
            done.append(r)
            click.echo(f"  [{i}/{len(active)}] {s['Name']}: {r}", err=True)
    except Exception:
        rec["result"] = {"requests": c.requests}
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"sites_in_box": len(sites), "active": len(active),
                     "requests": c.requests,
                     "rows": sum(d.get("rows", 0) for d in done),
                     "empty_sites": [d["site"] for d in done if d.get("rows") == 0]}
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")
