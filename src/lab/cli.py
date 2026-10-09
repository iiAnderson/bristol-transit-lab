"""
`lab` — the command line.

    lab build-baseline B2026|B2028
    lab scenario new S015-a4-brt --from B2028
    lab run S015-a4-brt [--demand D1] [--periods AM,IP]
    lab run --noop                          # P0: writes a run record and nothing else
    lab compare S015-a4-brt B2028
    lab calibrate
    lab export-viz <run_id> [--compare <run_id>]
    lab view
    lab embed <component> <run_id> [--compare <run_id>] --out viz/embeds/<slug>
    lab params                              # list parameters and placeholders

Commands not yet built fail with a non-zero exit naming the phase that builds them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from . import FIDELITY_DISCLAIMER, __version__, params, runrecord
from .config import LabConfig


def _not_yet(phase: str) -> None:
    raise click.ClickException(f"not built yet — arrives in {phase} (see SPEC.md §10)")


@click.group(help=f"Bristol Transit Lab.\n\n{FIDELITY_DISCLAIMER}")
@click.version_option(__version__, prog_name="lab")
def cli() -> None:
    pass


@cli.command("build-baseline")
@click.argument("baseline", type=click.Choice(["B2026", "B2028"]))
def build_baseline(baseline: str) -> None:
    """Build a baseline network, skims and accessibility."""
    _not_yet("P2")


@cli.group()
def scenario() -> None:
    """Create and manage scenarios."""


@scenario.command("new")
@click.argument("scenario_id")
@click.option("--from", "parent", default="B2028", show_default=True)
def scenario_new(scenario_id: str, parent: str) -> None:
    """Create a scenario YAML from a parent."""
    _not_yet("P3")


@cli.command()
@click.argument("scenario_id", required=False)
@click.option("--demand", default=None, help="Demand version.")
@click.option("--periods", default="AM,IP", show_default=True)
@click.option("--noop", is_flag=True, help="Write a run record and do nothing else.")
def run(scenario_id: str | None, demand: str | None, periods: str, noop: bool) -> None:
    """Run a scenario through the model."""
    if not noop:
        if scenario_id is None:
            raise click.UsageError("give a SCENARIO_ID, or --noop")
        _not_yet("P6")
    if scenario_id is not None:
        raise click.UsageError("--noop takes no SCENARIO_ID")
    cfg = LabConfig.load()
    rec = runrecord.build(cfg, command="noop")
    runrecord.write(cfg, rec)
    path = runrecord.finish(cfg, rec, "ok")
    shown = path.relative_to(cfg.root) if path.is_relative_to(cfg.root) else path
    click.echo(f"wrote {shown}")
    up = rec["upstream"]
    click.echo(f"  upstream {up['package']} {up['package_version']} @ "
               f"{(up['commit'] or '?')[:9]}{' (dirty)' if up['dirty'] else ''}, "
               f"DB sha256 {up['db_sha256'][:12]}…, {len(up['stage_log'])} stage_log rows")
    if rec["git"]["sha"] is None:
        click.echo("  note: the lab repo has no commit yet, so git.sha is null")


@cli.command()
@click.argument("scenario_id")
@click.argument("parent_id", default="B2028")
def compare(scenario_id: str, parent_id: str) -> None:
    """Write a diff report between two runs."""
    _not_yet("P3")


@cli.command()
def calibrate() -> None:
    """Run the §9 calibration and validation gates against B2026."""
    _not_yet("P5")


@cli.group()
def demand() -> None:
    """Build demand matrices."""


@demand.command("commute")
def demand_commute() -> None:
    """P1: analysis-grade HBW matrix from upstream raw flows (low/central/high d)."""
    from .demand import commute
    cfg = LabConfig.load()
    rec = runrecord.build(cfg, command="demand-commute", demand_version="p1",
                          inputs=commute_inputs(cfg))
    runrecord.write(cfg, rec)
    try:
        b = commute.run(cfg)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"discounts": b.discounts, "checks": [list(c) for c in b.checks],
                     "summaries": b.summaries}
    path = runrecord.finish(cfg, rec, "ok")
    for name, lab, up, ok in b.checks:
        fmt = ",.3f" if abs(up) < 100 else ",.2f"
        click.echo(f"  {'ok  ' if ok else 'FAIL'} {name}: {lab:{fmt}} vs {up:{fmt}}")
    for v in commute.VARIANTS:
        s = b.summaries[v]
        click.echo(f"  {v:<8} d={s['d']:.3f}  median factor {s['median_raw_factor']:.3f}  "
                   f"max {s['max_raw_factor']:.2f}  external_out median factor "
                   f"{s['ext_out']['median']:.3f}")
        for z in s["ext_out"]["above_check"]:
            click.echo(f"           external {z['msoa']} {z['name']}: BRES {z['bres_jobs']:,}, "
                       f"census arrivals {z['census_arrivals']:,}, raw factor "
                       f"x{z['raw_factor']:.2f} -> in-extent fallback [MODELLED]")
    click.echo(f"wrote {path}")


def commute_inputs(cfg: LabConfig) -> list[dict]:
    """Hashes of the raw files P1 reads, for the run record."""
    files = [(cfg.root / "data" / "raw", f) for f in (
        "census2021/odwp14ew.zip", "nts2025/nts0412.ods", "nts2025/nts0504.ods",
        "ons/hybridsupplementary8januaryto30march2025.xlsx",
        "ons_geo/lsoa21_pwc_extent.geojson")]
    files += [(cfg.upstream_raw, f) for f in (
        "odwp01ew.zip", "ts058.zip", "bres_lsoa.csv")]
    out = []
    for root, f in files:
        p = root / f
        if not p.is_file():
            raise click.ClickException(f"missing input {p}; see sources.md P1")
        out.append({"name": str(p), "sha256": params.file_hash(p)})
    return out


@cli.group()
def supply() -> None:
    """P2 supply feeds."""


@supply.group()
def avl() -> None:
    """Bus vehicle locations (BODS SIRI-VM): archive fetch, live collection, status."""


def _env_secret(name: str) -> str:
    import os
    v = os.environ.get(name)
    if not v:
        raise click.ClickException(
            f"{name} is not set; it lives in ~/.config/bristol-transit-lab/secrets.env, "
            "which env.sh sources")
    return v


def _on_signal(obj) -> None:
    import signal

    def handler(signum, frame):
        obj.stop = True
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


@supply.command("rail")
def supply_rail() -> None:
    """Darwin timetable -> rail GTFS for the modelled date; presence check; feed registry."""
    import datetime as dt
    import yaml
    from .supply import avl as a, feeds, gtfs_rail as g
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    rc = raw["rail"]
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    box = a.clip_box(cfg)
    paths = {k: cfg.root / rc[k] for k in ("darwin_timetable", "darwin_ref", "naptan", "out")}
    rec = runrecord.build(cfg, command="supply-rail", inputs=[
        {"name": str(paths[k]), "sha256": feeds.sha256(paths[k])}
        for k in ("darwin_timetable", "darwin_ref", "naptan")])
    runrecord.write(cfg, rec)
    try:
        ref = g.read_ref(paths["darwin_ref"])
        nap = g.read_naptan(paths["naptan"])
        res = g.convert(paths["darwin_timetable"], ref, nap, day, box)
        pres = g.presence(res, nap, box, list(rc["not_served"]))
        bad = pres["unserved_expected"] or pres["served_but_not_open"]
        if bad:
            raise click.ClickException(f"rail station presence check failed: {pres}")
        g.write_gtfs(res, ref, paths["out"], raw["avl"]["timezone"])
        from .supply import validate
        val = validate.run(cfg.root / rc["validator"], paths["out"],
                           paths["out"].with_suffix(".validator"), day, "gb")
        if val["errors"]:
            raise click.ClickException(f"rail GTFS has validator errors: {val['codes']}")
        now = dt.datetime.now(dt.timezone.utc)
        darwin_licence = rc["darwin_licence"]
        for fid, kind, p, url, lic in [
            ("darwin_timetable", "rail_darwin", paths["darwin_timetable"],
             "supplied by Robbie (Darwin Push Port timetable)", darwin_licence),
            ("darwin_ref", "ref", paths["darwin_ref"],
             "supplied by Robbie (Darwin Push Port reference)", darwin_licence),
            ("naptan_rail", "ref", paths["naptan"], rc["naptan_url"], "OGL v3")]:
            feeds.register(cfg, feed_id=fid, kind=kind, source_url=url, path=p,
                           downloaded_at=dt.datetime.fromtimestamp(p.stat().st_mtime,
                                                                   dt.timezone.utc),
                           licence=lic, notes=f"timetable {res.timetable_id}"
                           if fid.startswith("darwin") else None)
        feeds.register(cfg, feed_id="rail_gtfs", kind="rail_gtfs",
                       source_url="built by `lab supply rail`", path=paths["out"],
                       downloaded_at=now, licence=darwin_licence,
                       valid_from=day, valid_to=day,
                       validator_errors=val["errors"], validator_warnings=val["warnings"],
                       notes=f"darwin-{res.timetable_id}; {val['jar']}: {val['codes']}")
        feeds.require_covers(feeds.get(cfg, "rail_gtfs"), day)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"timetable_id": res.timetable_id, "service_date": day.isoformat(),
                     "counts": dict(res.counts), "stops": len(res.stops),
                     "calls_by_tpl": dict(res.calls_by_tpl), "presence": pres,
                     "unmapped_not_adjacent": res.unmapped, "validator": val}
    path = runrecord.finish(cfg, rec, "ok")
    for k, v in sorted(res.counts.items()):
        click.echo(f"  {k:<40} {v:>8,}")
    click.echo(f"  {len(res.stops)} stations; presence check passed; validator "
               f"{val['errors']} errors, {val['warnings']} warnings; wrote {paths['out']}")
    click.echo(f"wrote {path}")


@supply.command("bus")
def supply_bus() -> None:
    """BODS GTFS -> clipped, de-duplicated single-date bus GTFS; validator; registry."""
    import datetime as dt
    import duckdb
    import yaml
    from .supply import avl as a, feeds, gtfs_bods as g, validate
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    bc = raw["bus"]
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    paths = {k: cfg.root / v for k, v in bc["feeds"].items()}
    out = cfg.root / bc["out"]
    rec = runrecord.build(cfg, command="supply-bus", inputs=[
        {"name": str(p), "sha256": feeds.sha256(p)} for p in paths.values()])
    runrecord.write(cfg, rec)
    try:
        con = duckdb.connect()
        g.load(con, paths)
        names = list(paths)
        zones = cfg.root / "data" / "raw" / "ons_geo" / "lsoa21_bgc_internal.geojson"
        if not zones.is_file():
            raise click.ClickException(f"{zones} missing: internal LSOA polygons are needed "
                                       "to keep trips serving zones beyond the extent")
        res = g.build(con, names, day, cfg.extent, a.clip_box(cfg), zones,
                      bc.get("exclude_trips", {}).get("trip_ids"))
        cmp_day = bc["compare_date"]
        now_r = g.trips_per_route_on(con, names, day)
        later = g.trips_per_route_on(con, names, cmp_day)
        changed = sorted(((k, now_r.get(k, 0), later.get(k, 0)) for k in set(now_r) | set(later)
                          if now_r.get(k, 0) != later.get(k, 0)),
                         key=lambda x: -abs(x[1] - x[2]))
        g.write(con, names, day, out, f"bods-{'+'.join(names)}-{day:%Y%m%d}")
        val = validate.run(cfg.root / raw["rail"]["validator"], out,
                           out.with_suffix(".validator"), day, "gb")
        if val["errors"]:
            raise click.ClickException(f"bus GTFS has validator errors: {val['codes']}")
        now = dt.datetime.now(dt.timezone.utc)
        for name, p in paths.items():
            feeds.register(cfg, feed_id=f"bods_{name}", kind="bus_gtfs",
                           source_url=bc["source_url"] + p.name, path=p, downloaded_at=
                           dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc),
                           licence="OGL v3 (BODS; archive: Open Innovations / National Data "
                                   "Library)", valid_from=day, valid_to=day,
                           notes="regional BODS GTFS archived on the modelled date")
        feeds.register(cfg, feed_id="bus_gtfs", kind="bus_gtfs",
                       source_url="built by `lab supply bus`", path=out, downloaded_at=now,
                       licence="OGL v3", valid_from=day, valid_to=day,
                       validator_errors=val["errors"], validator_warnings=val["warnings"],
                       notes=f"{val['jar']}: {val['codes']}")
        feeds.require_covers(feeds.get(cfg, "bus_gtfs"), day)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {**{k: v for k, v in res.items()}, "validator": val,
                     "check2_compare_date": str(cmp_day), "check2_changed_routes": changed}
    path = runrecord.finish(cfg, rec, "ok")
    for k, v in res.items():
        if isinstance(v, dict):
            click.echo(f"  {k:<36} {v}")
        elif not isinstance(v, list):
            click.echo(f"  {k:<36} {v:>9,}")
    click.echo(f"  routes {len(res['per_route'])}; >10% trips removed as duplicates: "
               f"{len(res['flagged_routes'])}; same-start groups kept: "
               f"{len(res['same_start_groups'])}")
    click.echo(f"  check 2 ({day} vs {cmp_day}): {len(changed)} routes differ")
    click.echo(f"  validator {val['errors']} errors, {val['warnings']} warnings")
    click.echo(f"wrote {path}")


@supply.command("points")
def supply_points() -> None:
    """Fetch OA population-weighted centroids for every internal OA; table int_oa_pwc."""
    import datetime as dt
    import duckdb
    import yaml
    from .supply import avl as a, feeds, points
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    out = cfg.root / "data" / "raw" / "ons_geo" / "oa21_pwc_internal.geojson"
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        codes = [r[0] for r in con.execute("SELECT OA21CD FROM int_oa ORDER BY 1").fetchall()]
    res = points.fetch(raw["oa_pwc_service"], codes, out,
                       a.user_agent(cfg, _env_secret("LAB_CONTACT_EMAIL")))
    if res["missing"]:
        raise click.ClickException(f"ONS returned no centroid for {res['missing']}")
    with duckdb.connect(str(cfg.lab_db)) as con:
        con.execute("INSTALL spatial; LOAD spatial")
        con.execute(f"""CREATE OR REPLACE TABLE int_oa_pwc AS SELECT OA21CD,
            ST_X(geom) lon, ST_Y(geom) lat FROM ST_Read('{out}')""")
        n = con.execute("SELECT count(*) FROM int_oa_pwc").fetchone()[0]
    feeds.register(cfg, feed_id="ons_oa21_pwc_internal", kind="ref",
                   source_url=raw["oa_pwc_service"], path=out,
                   downloaded_at=dt.datetime.now(dt.timezone.utc), licence="OGL v3",
                   notes=f"{n} internal OAs")
    click.echo(f"int_oa_pwc: {n} OAs")


@supply.command("smoke")
@click.option("--itin-pairs", default=200, show_default=True)
def supply_smoke(itin_pairs: int) -> None:
    """A8: build the B2026 network, route 50 pairs, and project skim runtimes (D3, D7)."""
    import datetime as dt
    import duckdb
    import yaml
    from .supply import feeds, smoke
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    for fid in ("osm_clip", "bus_gtfs", "rail_gtfs"):
        feeds.check_file_unchanged(feeds.get(cfg, fid))
    for fid in ("bus_gtfs", "rail_gtfs"):
        feeds.require_covers(feeds.get(cfg, fid), day)
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        lsoa = con.execute("SELECT LSOA21CD id, lon, lat FROM int_lsoa ORDER BY 1").df()
        oa = con.execute("SELECT OA21CD id, lon, lat FROM int_oa_pwc ORDER BY 1").df()
        n_int = con.execute("SELECT count(*) FROM int_oa").fetchone()[0]
    if len(oa) != n_int:
        raise click.ClickException(f"int_oa_pwc has {len(oa)} OAs, int_oa {n_int}; "
                                   "run `lab supply points`")
    rec = runrecord.build(cfg, command="supply-smoke", inputs=[
        {"name": fid, "sha256": feeds.get(cfg, fid)["sha256"]}
        for fid in ("osm_clip", "bus_gtfs", "rail_gtfs")])
    runrecord.write(cfg, rec)
    try:
        res = smoke.run(str(cfg.root / raw["osm"]["clip"]),
                        [str(cfg.root / raw["bus"]["out"]), str(cfg.root / raw["rail"]["out"])],
                        lsoa, oa, dt.datetime.combine(day, dt.time(8, 0)),
                        itin_pairs=itin_pairs)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    path = runrecord.finish(cfg, rec, "ok")
    for k, v in res.items():
        if k != "pairs":
            click.echo(f"  {k}: {v}")
    click.echo(f"wrote {path}")


@supply.command("bus-variants")
@click.option("--day", default=None, help="Archive day to check (default: the modelled date).")
def supply_bus_variants(day: str | None) -> None:
    """Resolve same-start timetable variants against vehicle destinations on the day."""
    import datetime as dt
    import json
    import yaml
    from .supply import bus_variants as bv
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    day = day or str(raw["modelled_date"])
    f = cfg.root / raw["paths"]["avl"] / "archive" / f"sirivm_{day}.parquet"
    if not f.is_file():
        raise click.ClickException(f"{f.name} not closed yet")
    runs = sorted(cfg.runs_dir.glob("*-supply-bus-*"))
    bus_rec = json.loads((runs[-1] / "run.json").read_text())
    groups = bus_rec["result"]["same_start_groups"]
    lines = sorted({g[1] for g in groups})
    ops = sorted({g[0] for g in groups})
    obs = __import__("pandas").concat([bv.observed_destinations(f, op, lines) for op in ops])
    res = bv.resolve(groups, obs)
    rec = runrecord.build(cfg, command="supply-bus-variants", inputs=[
        {"name": str(f), "sha256": params.file_hash(f)},
        {"name": "bus build run", "version": runs[-1].name}])
    runrecord.write(cfg, rec)
    out = cfg.runs_dir / rec["run_id"]
    res.to_csv(out / "variants.csv", index=False)
    obs.to_csv(out / "observed_destinations.csv", index=False)
    summ = res.groupby(["noc", "line", "terminal", "decision"]).size().rename("trips") \
        .reset_index().to_dict("records")
    rec["result"] = {"day": day, "bus_build_run": runs[-1].name, "summary": summ}
    click.echo(obs.to_string(index=False))
    click.echo(__import__("pandas").DataFrame(summ).to_string(index=False))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@supply.command("osm")
def supply_osm() -> None:
    """Download, verify, merge and clip the OSM extracts; register them as feeds."""
    import datetime as dt
    import os
    import yaml
    from .supply import avl as a, feeds, osm
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    oc = raw["osm"]
    ua = a.user_agent(cfg, _env_secret("LAB_CONTACT_EMAIL"))
    rec = runrecord.build(cfg, command="supply-osm")
    runrecord.write(cfg, rec)
    try:
        got = []
        for u in oc["extracts"]:
            url = osm.resolve(u, ua)
            d = osm.download(url, cfg.root / oc["raw_dir"] / url.rsplit("/", 1)[1], ua)
            got.append(d)
            click.echo(f"  {d['path'].name}: md5 ok, data to {d['replication_timestamp']}")
        clip = cfg.root / oc["clip"]
        stats = osm.merge_and_clip([d["path"] for d in got], a.clip_box(cfg),
                                   cfg.root / oc["merged"], clip)
        now = dt.datetime.now(dt.timezone.utc)
        for d in got:
            feeds.register(cfg, feed_id=f"osm_{d['path'].name.split('-')[0]}", kind="osm",
                           source_url=d["url"], path=d["path"], downloaded_at=now,
                           licence="ODbL 1.0 (© OpenStreetMap contributors)",
                           notes=f"md5 {d['md5']}; replication {d['replication_timestamp']}")
        dates = sorted({d["replication_timestamp"] for d in got})
        feeds.register(cfg, feed_id="osm_clip", kind="osm", source_url="merged + clipped "
                       "by `lab supply osm`", path=clip, downloaded_at=now,
                       licence="ODbL 1.0 (© OpenStreetMap contributors)",
                       notes=f"{stats}; OSM data dates {dates}; modelled date "
                             f"{raw['modelled_date']}; {osm.osmium_version()}")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["inputs"] = [{"name": d["url"], "md5": d["md5"],
                      "version": d["replication_timestamp"]} for d in got]
    rec["result"] = {**stats, "osm_dates": dates, "modelled_date": str(raw["modelled_date"]),
                     "osmium": osm.osmium_version()}
    click.echo(f"  clip: {stats['nodes']:,} nodes, {stats['ways']:,} ways; OSM dates {dates}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@avl.command("archive")
@click.option("--clear-halt", is_flag=True,
              help="Resume after a circuit-breaker halt (a human decision).")
def avl_archive(clear_halt: bool) -> None:
    """Fetch, clip and de-duplicate NDL archive snapshots (resumable; PAUSE file pauses)."""
    from .supply import avl as a
    cfg = LabConfig.load()
    ac = a.avl_config(cfg)
    get = a.requests_get(a.user_agent(cfg, _env_secret("LAB_CONTACT_EMAIL")),
                         ac["archive"]["policy"]["timeout_s"])
    f = a.ArchiveFetch(cfg, get)
    _on_signal(f)
    rec = runrecord.build(cfg, command="supply-avl-archive", inputs=[
        {"name": "NDL BODS-ARCHIVE sirivm", "url": ac["archive"]["base_url"],
         "version": [d.isoformat() for d in ac["archive"]["days"]]}])
    runrecord.write(cfg, rec)
    try:
        status = f.run(clear_halt=clear_halt)
    except a.Halted as e:
        rec["result"] = {**f.counters(), "halt": str(e)}
        runrecord.finish(cfg, rec, "halted")
        raise click.ClickException(f"HALTED: {e}")
    except Exception:
        rec["result"] = f.counters()
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {**f.counters(), "status": a.archive_status(cfg)}
    click.echo(f"archive fetch {status}; wrote {runrecord.finish(cfg, rec, status)}")


@avl.command("live")
@click.option("--wait", is_flag=True,
              help="Stay up and collect every remaining configured day, keeping the Mac "
                   "awake (caffeinate) only while a window is open.")
def avl_live(wait: bool) -> None:
    """Poll the BODS API through today's window, if today is a collection day."""
    import datetime as dt
    import os
    import subprocess
    import time
    from .supply import avl as a
    cfg = LabConfig.load()
    ac = a.avl_config(cfg)
    get = a.requests_get(a.user_agent(cfg, _env_secret("LAB_CONTACT_EMAIL")), 30)
    key = _env_secret("BODS_API_KEY")
    days = ac["live"]["days"] if wait else [None]
    for day in days:
        c = a.LiveCollect(cfg, get, key)
        _on_signal(c)
        if day is not None:
            start, end = a.window_utc(day, ac["live"]["hours"], ac["timezone"])
            if dt.datetime.now(dt.timezone.utc) >= end:
                continue
            click.echo(f"waiting for {day} window ({start:%H:%M} UTC)", err=True)
            # Wall-clock waits in short steps: survives the Mac sleeping in between.
            c._wait_until(start - dt.timedelta(minutes=2))
            if c.stop:
                return
        awake = subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())]) \
            if wait else None
        rec = runrecord.build(cfg, command="supply-avl-live", inputs=[
            {"name": "BODS SIRI-VM datafeed", "url": ac["live"]["endpoint"]}])
        runrecord.write(cfg, rec)
        try:
            status = c.run()
        except Exception:
            runrecord.finish(cfg, rec, "failed")
            raise
        finally:
            if awake:
                awake.terminate()
        rec["result"] = {"status": status, "live": a.live_status(cfg)}
        runrecord.finish(cfg, rec, "paused" if status == "stopped" else "ok")
        click.echo(f"live collection: {status}", err=True)
        if c.stop:
            return
        time.sleep(1)


@avl.command("status")
def avl_status() -> None:
    """Per-day progress, requests, failures, backoff and gaps for both sources."""
    from .supply import avl as a
    cfg = LabConfig.load()
    s = a.archive_status(cfg)
    click.echo(f"archive: {s['state']}; {s['requests']} requests, {s['failed']} failed, "
               f"{s['backoff_s']} s in backoff")
    if s["halt"]:
        click.echo(f"  HALT: {s['halt']}")
    for d in s["days"]:
        click.echo(f"  {d['day']}  listed {d['listed'] if d['listed'] is not None else '-':>5}"
                   f"  done {d['done']:>5}  gaps {d['gap']:>3}  rows {d['rows']:>9,}"
                   f"  {'closed ' + d['sha256'][:12] if d['closed'] else ''}")
    for e in s["policy_events"]:
        click.echo(f"  policy: {e}")
    for d in a.live_status(cfg)["days"]:
        click.echo(f"live {d['day']}: {d['ok_polls']} ok polls, {d['failed_polls']} failed, "
                   f"{d['rows']:,} rows, {d['gap_minutes']} min of gaps"
                   f"{'  closed ' + d['sha256'][:12] if d['closed'] else ''}")
    if s["state"] == "halted":
        sys.exit(2)


@cli.group()
def congestion() -> None:
    """P2 car congestion: inputs, calibration and validation."""


@congestion.command("network")
def congestion_network() -> None:
    """Car segment table from the OSM clip, annotated (authority, area type, class, direction, B1)."""
    import duckdb
    import yaml
    from .congestion import annotate as an, network as nw
    from .supply import feeds
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
    import datetime as dt
    import duckdb
    import yaml
    from .congestion import webtris as w
    from .supply import avl as a
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
    import io
    import zipfile
    import duckdb
    import yaml
    from .congestion import avl_speeds as av
    from .supply import feeds, osrm
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
    from .congestion import calibrate as cal, fit, targets as tg
    from .supply import avl as a
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
    import duckdb
    import pandas as pd
    import yaml
    from .congestion import calibrate as cal, fit, targets as tg
    from .supply import avl as a
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
    from .congestion import signals as sg
    from .supply import osrm
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
    from .congestion import annotate as an_
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
    ax.set_title("ANPR layer scope: Bristol built-up area (black: ANPR links)")
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
    import pandas as pd
    import yaml
    from .congestion import calibrate as cal, validate as va
    from .supply import osrm
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


@congestion.command("spacing-bias")
def congestion_spacing_bias() -> None:
    """Bias test: bus speeds from the live days at full resolution vs thinned to 30 s
    snapshots (`lab congestion avl --source live [--snapshot-s 30]` first)."""
    import pandas as pd
    from .congestion import spacing as sp
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
    from .supply import avl as a, webtris as w
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


@cli.group()
def spike() -> None:
    """P2a decision spikes (plans/P2.md A9)."""


@spike.command("d2")
@click.option("--ways", default=20, show_default=True)
@click.option("--synthetic", is_flag=True, help="One straight road; no alternatives.")
def spike_d2(ways: int, synthetic: bool) -> None:
    """Does R5 honour per-direction maxspeed edits? (D2 option A)"""
    import datetime as dt
    import yaml
    from .spikes import d2_speed_direction as d2
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    rec = runrecord.build(cfg, command="spike-d2")
    runrecord.write(cfg, rec)
    try:
        dep = dt.datetime.combine(day, dt.time(8, 0))
        work = cfg.runs_dir / rec["run_id"] / "work"
        box = tuple(raw["spikes"]["d2_box"])
        if synthetic:
            res = d2.run_synthetic(work, dep, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        else:
            res = d2.run(cfg.root / raw["osm"]["clip"], box, work, dep, n=ways)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    click.echo(f"  {res.get('summary', res)}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@spike.command("d3")
def spike_d3() -> None:
    """Job-weighted LSOA destination points vs PWC: change in HBW PT and car times."""
    import datetime as dt
    import duckdb
    import yaml
    from .spikes import d3_job_points as d3
    from .supply import feeds, osrm
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    base = cfg.root / raw["osm"]["osrm_base"]
    if not base.with_suffix(".osrm.partition").exists():
        osrm.prepare(cfg.root / raw["osm"]["clip"], base)
    osrm.customize(base)                         # free flow: the default profile speeds
    odwp = cfg.upstream_raw / raw["spikes"]["odwp01ew_oa"]
    rec = runrecord.build(cfg, command="spike-d3", demand_version="p1-central", inputs=[
        {"name": str(odwp), "sha256": params.file_hash(odwp)},
        *[{"name": f, "sha256": feeds.get(cfg, f)["sha256"]}
          for f in ("osm_clip", "bus_gtfs", "rail_gtfs")],
        {"name": "osrm", "version": osrm.version()}])
    runrecord.write(cfg, rec)
    try:
        with duckdb.connect(str(cfg.lab_db)) as con:
            res = d3.run(con, str(odwp), base, str(cfg.root / raw["osm"]["clip"]),
                         [str(cfg.root / raw["bus"]["out"]), str(cfg.root / raw["rail"]["out"])],
                         dt.datetime.combine(day, dt.time(8, 0)),
                         ps["skims.hbw_dest_point_abs_min"], ps["skims.hbw_dest_point_rel"])
            pts = res.pop("points")
            con.register("pts", pts)
            con.execute("CREATE OR REPLACE TABLE int_lsoa_jobpoint AS SELECT id LSOA21CD, "
                        "jlon lon, jlat lat, workers, moved_m FROM pts")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    for k, v in res.items():
        click.echo(f"  {k}: {v}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@cli.group()
def skims() -> None:
    """P2c skims: PT (r5r), walk and cycle (r5py)."""


@skims.command("pt")
@click.option("--period", type=click.Choice(["AM", "IP"]), required=True)
@click.option("--chunk", default=100, show_default=True)
@click.option("--provisional/--final", default=True, show_default=True,
              help="Provisional until the First Bristol 5/77 variants are resolved.")
def skims_pt(period: str, chunk: int, provisional: bool) -> None:
    """PT skims OA -> clip-box LSOAs from r5r's expanded matrix (per-pair summary)."""
    import datetime as dt
    import shutil
    import duckdb
    import yaml
    from . import skims as sk
    from .supply import feeds
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    for fid in ("osm_clip", "bus_gtfs", "rail_gtfs"):
        feeds.check_file_unchanged(feeds.get(cfg, fid))
        if fid != "osm_clip":
            feeds.require_covers(feeds.get(cfg, fid), day)
    work = cfg.root / "data" / "interim" / "skims" / f"pt_{period}"
    net = cfg.root / "data" / "interim" / "r5r" / "net"
    work.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        con.execute("SELECT OA21CD id, lat, lon FROM int_oa_pwc ORDER BY 1").df() \
            .to_csv(work / "origins.csv", index=False)
        con.execute("SELECT LSOA21CD id, lat, lon FROM skim_dest ORDER BY 1").df() \
            .to_csv(work / "destinations.csv", index=False)
    shutil.copyfile(cfg.root / "data" / "interim" / "r5r" / "d3_origins.csv",
                    work / "sample_ids.csv")
    rcfg = {"net_dir": str(net), "origins": str(work / "origins.csv"),
            "destinations": str(work / "destinations.csv"),
            "departure": f"{day} {ps[f'skims.window_start.{period}']}",
            "window_min": ps["skims.departure_window_min"],
            "max_rides": ps["routing.max_rides"], "walk_speed_kmh": ps["routing.walk_speed_kmh"],
            "max_walk_min": ps["routing.max_walk_min"], "max_trip_min": ps["routing.max_trip_min"],
            "reach_share_min": ps["routing.pt_reachable_share_min"], "chunk": chunk,
            "sample_ids": str(work / "sample_ids.csv"), "out_dir": str(work / "chunks"),
            "java_mem": "10G"}
    rec = runrecord.build(cfg, command=f"skims-pt-{period}", inputs=[
        {"name": f, "sha256": feeds.get(cfg, f)["sha256"]} for f in
        ("osm_clip", "bus_gtfs", "rail_gtfs")] + [{"name": "r5r", "version": "2.4.0 (R5 7.5.1)"}])
    runrecord.write(cfg, rec)
    try:
        sk.run_pt(cfg.root / "src" / "lab" / "r" / "pt_skims.R", rcfg, work / "config.json",
                  lambda m: click.echo(f"  {m}", err=True),
                  [feeds.get(cfg, f)["sha256"] for f in ("osm_clip", "bus_gtfs", "rail_gtfs")]
                  + [params.file_hash(cfg.root / "src" / "lab" / "r" / "pt_skims.R")])
        s = sk.combine(work / "chunks")
        w = {k: ps[f"generalised_cost.{k}"] for k in ("w_walk", "w_wait", "p_interchange")}
        curve = ps["generalised_cost.first_wait_curve"]
        s["gc_min"] = sk.gc_tag(s, w, curve).where(~s["unreachable"])
        s["gc_min_random_arrival"] = sk.gc_from_components(s, w).where(~s["unreachable"])
        # the PT alternative proper: at least one ride, in at least the same share of
        # window minutes as the reachability rule
        s["gc_ride_min"] = sk.gc_tag(sk.ride_only(s), w, curve).where(
            s["ride_share"] >= ps["routing.pt_reachable_share_min"])
        s["provisional"] = provisional
        out = cfg.root / "data" / "interim" / "skims" / f"pt_{period}_oa_lsoa.parquet"
        s.to_parquet(out, compression="zstd")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"pairs": len(s), "unreachable_share": float(s["unreachable"].mean()),
                     "median_p50": float(s["p50"].median()), "provisional": provisional,
                     "out": str(out)}
    click.echo(f"  {rec['result']}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@skims.command("pt-gc")
@click.option("--period", "periods", multiple=True, default=["AM", "IP"], show_default=True)
def skims_pt_gc(periods: tuple[str, ...]) -> None:
    """Recompute PT GC on stored skims from params (no re-routing): gc_min uses the TAG
    M3.2 first-wait curve; gc_min_random_arrival keeps half-headway waiting."""
    import pandas as pd
    from . import skims as sk
    cfg = LabConfig.load()
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    w = {k: ps[f"generalised_cost.{k}"] for k in ("w_walk", "w_wait", "p_interchange")}
    curve = ps["generalised_cost.first_wait_curve"]
    rec = runrecord.build(cfg, command="skims-pt-gc")
    runrecord.write(cfg, rec)
    res = {}
    for per in periods:
        f = cfg.root / "data" / "interim" / "skims" / f"pt_{per}_oa_lsoa.parquet"
        s = pd.read_parquet(f)
        s["gc_min"] = sk.gc_tag(s, w, curve).where(~s["unreachable"])
        s["gc_min_random_arrival"] = sk.gc_from_components(s, w).where(~s["unreachable"])
        s["gc_ride_min"] = sk.gc_tag(sk.ride_only(s), w, curve).where(
            s["ride_share"] >= ps["routing.pt_reachable_share_min"])
        s.to_parquet(f, compression="zstd")
        ok = s[~s["unreachable"]]
        d = ok["gc_min_random_arrival"] - ok["gc_min"]
        res[per] = {"median_gc_tag": float(ok["gc_min"].median()),
                    "median_gc_random": float(ok["gc_min_random_arrival"].median()),
                    "median_reduction_min": float(d.median()),
                    "p90_reduction_min": float(d.quantile(0.9))}
        click.echo(f"  {per}: {res[per]}")
    rec["result"] = res
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@skims.command("car")
@click.option("--period", "periods", multiple=True, default=["AMPH", "IP", "AM"],
              show_default=True, help="AMPH = 08:00–09:00 skim hour; AM (07:00–10:00) is "
              "the stored sensitivity.")
@click.option("--variant", type=click.Choice(["hybrid", "base"]), default="hybrid",
              show_default=True)
def skims_car(periods: tuple[str, ...], variant: str) -> None:
    """Car skims OA -> clip-box LSOAs from OSRM with calibrated per-period speeds."""
    import duckdb
    import numpy as np
    import pandas as pd
    import yaml
    from .congestion import validate as va
    from .supply import osrm
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    d = cfg.root / "data" / "interim" / "congestion"
    sp = d / ("osrm_speeds" if variant == "hybrid" else "osrm_speeds_base")
    base = cfg.root / raw["osm"]["osrm_base"]
    rec = runrecord.build(cfg, command="skims-car", inputs=[
        {"name": str(sp / f"speeds_{p}.csv"), "sha256": params.file_hash(sp / f"speeds_{p}.csv")}
        for p in periods] + [{"name": "osrm", "version": osrm.version()}])
    runrecord.write(cfg, rec)
    res = {}
    try:
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            o = con.execute("SELECT OA21CD id, lon, lat FROM int_oa_pwc ORDER BY 1").df()
            dst = con.execute("""SELECT d.LSOA21CD id, d.lon, d.lat,
                CASE WHEN d.internal THEN 1 ELSE 0 END internal FROM skim_dest d ORDER BY 1""").df()
        park = {k: ps.get(f"car.parking_search_min.{k}") for k in ("centre", "urban", "rural")}
        walk = {k: ps.get(f"car.access_walk_min.{k}") for k in ("centre", "urban", "rural")}
        for per in periods:
            ds = va.period_dataset(base, sp / f"speeds_{per}.csv",
                                   cfg.root / "data" / "interim" / "osrm" / f"{variant}_{per}")
            with osrm.Server(ds) as srv:
                m = srv.table(list(zip(o.lon, o.lat)), list(zip(dst.lon, dst.lat))) / 60
            t = pd.DataFrame({"from_id": np.repeat(o["id"].to_numpy(), len(dst)),
                              "to_id": np.tile(dst["id"].to_numpy(), len(o)),
                              "ivt_min": m.ravel()})
            t["parking_search_min"] = np.nan    # modelled range, applied in `lab gapmap`
            t["access_walk_min"] = np.nan       # (by destination area type)
            t["gc_min"] = np.nan
            t["variant"], t["period"] = variant, per
            out = cfg.root / "data" / "interim" / "skims" / f"car_{per}_oa_lsoa.parquet"
            t.to_parquet(out, compression="zstd")
            res[per] = {"pairs": len(t), "unroutable": int(t["ivt_min"].isna().sum()),
                        "median_ivt_min": float(t["ivt_min"].median())}
            click.echo(f"  {per}: {res[per]}")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"variant": variant, **res,
                     "note": "in-vehicle time only; car GC adds the modelled parking/access "
                             "range in `lab gapmap`"}
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@skims.command("active")
def skims_active() -> None:
    """Walk and cycle skims OA -> clip-box LSOAs (r5py, shared routing settings)."""
    import datetime as dt
    import duckdb
    import geopandas as gpd
    import yaml
    from shapely.geometry import Point
    from r5py import TransportMode, TransportNetwork, TravelTimeMatrix
    from .supply import feeds
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        o = con.execute("SELECT OA21CD id, lon, lat FROM int_oa_pwc ORDER BY 1").df()
        d = con.execute("SELECT LSOA21CD id, lon, lat FROM skim_dest ORDER BY 1").df()
    g = lambda df: gpd.GeoDataFrame({"id": df["id"]}, crs="EPSG:4326",  # noqa: E731
                                    geometry=[Point(x, y) for x, y in zip(df.lon, df.lat)])
    rec = runrecord.build(cfg, command="skims-active", inputs=[
        {"name": "osm_clip", "sha256": feeds.get(cfg, "osm_clip")["sha256"]}])
    runrecord.write(cfg, rec)
    res = {}
    try:
        net = TransportNetwork(str(cfg.root / raw["osm"]["clip"]), [])
        dep = dt.datetime.combine(day, dt.time.fromisoformat(ps["skims.window_start.AM"]))
        mx = dt.timedelta(minutes=ps["routing.max_trip_min"])
        for mode, tm in (("walk", TransportMode.WALK), ("cycle", TransportMode.BICYCLE)):
            t = TravelTimeMatrix(net, origins=g(o), destinations=g(d), departure=dep,
                                 transport_modes=[tm], max_time=mx,
                                 speed_walking=ps["routing.walk_speed_kmh"],
                                 speed_cycling=ps["routing.cycle_speed_kmh"],
                                 max_bicycle_traffic_stress=ps["routing.max_bicycle_lts"])
            out = cfg.root / "data" / "interim" / "skims" / f"{mode}_oa_lsoa.parquet"
            t.to_parquet(out)
            res[mode] = {"pairs": len(t), "reachable": int(t["travel_time"].notna().sum()),
                         "median_min": float(t["travel_time"].median())}
            click.echo(f"  {mode}: {res[mode]}")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@cli.command("access")
@click.option("--thresholds", default="30,45", show_default=True)
def access_cmd(thresholds: str) -> None:
    """C2: cumulative BRES jobs reachable per OA (PT AM p50; walk for context)."""
    import duckdb
    from .supply import avl as a
    cfg = LabConfig.load()
    sk = cfg.root / "data" / "interim" / "skims"
    th = [int(x) for x in thresholds.split(",")]
    x0, y0, x1, y1 = cfg.extent
    rec = runrecord.build(cfg, command="access", inputs=[
        {"name": str(sk / f), "sha256": params.file_hash(sk / f)}
        for f in ("pt_AM_oa_lsoa.parquet", "walk_oa_lsoa.parquet")])
    runrecord.write(cfg, rec)
    try:
        with duckdb.connect(str(cfg.lab_db)) as con:
            cols = ", ".join(
                f"sum(j.jobs) FILTER (WHERE s.t <= {t}) AS jobs_{t}" for t in th)
            con.execute(f"""CREATE OR REPLACE TEMP VIEW pt AS
                SELECT from_id, to_id, CASE WHEN NOT unreachable THEN p50 END t
                FROM read_parquet('{sk / 'pt_AM_oa_lsoa.parquet'}')""")
            con.execute(f"""CREATE OR REPLACE TEMP VIEW wk AS
                SELECT from_id, to_id, travel_time t FROM read_parquet('{sk / 'walk_oa_lsoa.parquet'}')""")
            for mode in ("pt", "wk"):
                con.execute(f"""CREATE OR REPLACE TEMP TABLE acc_{mode} AS
                    SELECT o.OA21CD, {cols}
                    FROM int_oa_pwc o
                    LEFT JOIN {mode} s ON s.from_id = o.OA21CD
                    LEFT JOIN (SELECT d.LSOA21CD, coalesce(b.jobs, 0) jobs FROM skim_dest d
                               LEFT JOIN nat_bres b USING (LSOA21CD)) j ON j.LSOA21CD = s.to_id
                    GROUP BY 1""")
            # edge OAs: PWC within the clip buffer width of the extent edge
            km = 5.0
            con.execute(f"""CREATE OR REPLACE TABLE access_oa AS
                SELECT o.OA21CD, o.lon, o.lat, i.LSOA21CD,
                       {", ".join(f"coalesce(p.jobs_{t}, 0) pt_jobs_{t}" for t in th)},
                       {", ".join(f"coalesce(w.jobs_{t}, 0) walk_jobs_{t}" for t in th)},
                       least((o.lon - ({x0})) * 69.4, (({x1}) - o.lon) * 69.4,
                             (o.lat - ({y0})) * 111.3, (({y1}) - o.lat) * 111.3) < {km} AS edge
                FROM int_oa_pwc o JOIN int_oa i USING (OA21CD)
                LEFT JOIN acc_pt p USING (OA21CD) LEFT JOIN acc_wk w USING (OA21CD)""")
            res = con.execute(f"""SELECT edge, count(*) n,
                {", ".join(f"round(median(pt_jobs_{t})) pt_median_{t}" for t in th)},
                {", ".join(f"round(median(walk_jobs_{t})) walk_median_{t}" for t in th)}
                FROM access_oa GROUP BY 1 ORDER BY 1""").df()
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"by_edge": res.to_dict("records"), "table": "lab.duckdb access_oa",
                     "note": "decay-weighted measure pending: beta is a PLACEHOLDER"}
    click.echo(res.to_string(index=False))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@cli.command("compare-dft")
@click.option("--minutes", default=45, show_default=True)
def compare_dft(minutes: int) -> None:
    """C3: Spearman rho between our PT job accessibility and the DfT Connectivity Metric."""
    import duckdb
    from scipy.stats import spearmanr
    cfg = LabConfig.load()
    cdir = cfg.root / "data" / "interim" / "connectivity"
    rec = runrecord.build(cfg, command="compare-dft", inputs=[
        {"name": "dft_connectivity_2025", "sha256": __import__("lab.supply.feeds", fromlist=["x"])
         .get(cfg, "dft_connectivity_2025")["sha256"]}])
    runrecord.write(cfg, rec)
    try:
        with duckdb.connect(str(cfg.lab_db)) as con:
            def dft(sheet, key):
                return con.execute(f"""SELECT * FROM read_csv('{cdir / sheet}', skip=2,
                    header=true, all_varchar=true)""").df().rename(columns={key: "code"})
            oa = dft("OA.csv", "OA21CD")
            lsoa = dft("LSOA.csv", "LSOA21CD")
            ours = con.execute(f"SELECT OA21CD code, LSOA21CD, pt_jobs_{minutes} v, edge FROM access_oa").df()
        out = {}
        for label, col in (("employment_pt", "Business (public transport)"),
                           ("overall_pt", "Overall (public transport)")):
            m = ours.merge(oa[["code", col]], on="code")
            m[col] = m[col].astype(float)
            r_oa = spearmanr(m["v"], m[col]).statistic
            r_oa_core = spearmanr(m[~m["edge"]]["v"], m[~m["edge"]][col]).statistic
            l = ours.groupby("LSOA21CD")["v"].mean().rename("v").reset_index() \
                .merge(lsoa[["code", col]], left_on="LSOA21CD", right_on="code")
            r_lsoa = spearmanr(l["v"], l[col].astype(float)).statistic
            m["rank_diff"] = m["v"].rank(pct=True) - m[col].rank(pct=True)
            worst = m.reindex(m["rank_diff"].abs().sort_values(ascending=False).index).head(15)
            out[label] = {"dft_column": col, "rho_oa": round(float(r_oa), 3),
                          "rho_oa_excluding_edge": round(float(r_oa_core), 3),
                          "rho_lsoa": round(float(r_lsoa), 3), "n_oa": len(m), "n_lsoa": len(l),
                          "target_met_0.8": bool(min(r_oa, r_lsoa) >= 0.8),
                          "largest_rank_differences": worst[["code", "v", col, "rank_diff", "edge"]]
                          .round(3).to_dict("records")}
            m[["code", "v", col, "rank_diff", "edge"]].to_parquet(
                cfg.runs_dir / rec["run_id"] / f"rank_diff_{label}.parquet")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"minutes": minutes, **out,
                     "note": "DfT's PT employment column is labelled 'Business (public "
                             "transport)' (employment position); its metadata licence says TBA"}
    for k, v in out.items():
        click.echo(f"  {k}: rho OA {v['rho_oa']} (excl. edge {v['rho_oa_excluding_edge']}), "
                   f"LSOA {v['rho_lsoa']}; target >= 0.8 met: {v['target_met_0.8']}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


PT_SPOT_README = """PT spot checks (plans/P2.md C4, amended 2026-09-28)

One row per journey × departure slot: AM 08:15, IP 13:20, EVE 20:40 (EVE is a
diagnostic and is not in the pass count). Model columns come from r5py over a 60-minute
window starting at depart_local on the modelled date (Wed 23 Sep 2026):
  model_best_min      fastest departure in the window (excludes the initial wait)
  model_p25/p50/p75   percentiles over departure minutes (random arrival)

Fill in, from a public journey planner (no scraping; the modelled date, or the same
weekday pattern if the planner no longer shows it):
  planner_duration_min  the planner's displayed journey duration for its suggested
                        departure (excludes the initial wait)
  observed_elapsed_min  minutes from depart_local to arrival for the first itinerary you
                        could take leaving at depart_local (includes the wait)

Two pairings; a row passes if EITHER passes:
  A  planner_duration_min vs model_best_min, within ±15%
  B  observed_elapsed_min within [model_p25, model_p75], or within ±15% of model_p50
     (interpretation of "p25–p75 and p50 ± 15%": either sub-check; Robbie to confirm)
Acceptance: at least 16 of the 20 AM rows and 16 of the 20 IP rows pass, each failure
explained.
"""


@cli.command("gapmap")
@click.option("--car-period", default="AMPH", show_default=True)
@click.option("--pt-period", default="AM", show_default=True,
              help="PT AM skim is the 08:00–09:00 window (skims.window_start).")
def gapmap_cmd(car_period: str, pt_period: str) -> None:
    """C5: gap map — PT GC (≥ 1 ride) vs car GC per internal HBW LSOA pair beyond
    gapmap.min_distance_km; ratio of sums, flow-weighted median, GC difference; both ends
    of the car parking/access range; by distance band; robustness of the rankings."""
    import json
    import duckdb
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import yaml
    from . import gapmap as gm
    from .congestion import annotate as an
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    sk = cfg.root / "data" / "interim" / "skims"
    geo = cfg.root / "data" / "raw" / "ons_geo"
    ruc = cfg.root / "data/raw/ons/ruc21_lsoa_ew.csv"
    pt, car = sk / f"pt_{pt_period}_oa_lsoa.parquet", sk / f"car_{car_period}_oa_lsoa.parquet"
    park = {k: ps[f"car.parking_search_min.{k}"] for k in ("centre", "urban", "rural")}
    walk = {k: ps[f"car.access_walk_min.{k}"] for k in ("centre", "urban", "rural")}
    min_km = ps["gapmap.min_distance_km"]
    # the caveat on the label comes from the latest calibration's held-out ANPR figure
    cals = sorted(cfg.runs_dir.glob("*-congestion-calibrate-*/calibration.json"))
    anpr_val = json.loads(cals[-1].read_text())["anpr"][f"hybrid|{car_period}|validation"]["median_ratio"]
    label = gm.LABEL.format(min_km=min_km, anpr=anpr_val,
                            **{k: park[k] + walk[k] for k in park})
    rec = runrecord.build(cfg, command="gapmap", inputs=[
        {"name": str(f), "sha256": params.file_hash(f)} for f in (pt, car, ruc)]
        + [{"name": "calibration", "run": cals[-1].parent.name}])
    runrecord.write(cfg, rec)
    out = cfg.runs_dir / rec["run_id"]
    try:
        with duckdb.connect() as con:
            con.execute("INSTALL spatial; LOAD spatial")
            con.execute(f"ATTACH '{cfg.lab_db}' AS lab (READ_ONLY)")
            cl = an.centre_lsoas(con, geo / "lsoa21_bgc_internal.geojson", ruc,
                                 ps["area_type.centre_job_density"], raw["centre_min_cluster_lsoas"])
            for t in ("int_oa", "demand", "lsoa_pwc"):
                con.execute(f"CREATE TEMP TABLE {t} AS SELECT * FROM lab.{t}")
            con.execute("""CREATE TEMP TABLE oa_w AS
                SELECT o_oa OA21CD, sum(n)::DOUBLE w FROM lab.nat_oa_flows GROUP BY 1""")
            con.execute(f"""CREATE TEMP TABLE lsoa_area_type AS
                SELECT LSOA21CD, CASE WHEN LSOA21CD IN (SELECT unnest(?)) THEN 'centre'
                                      WHEN Urban_rural_flag = 'Urban' THEN 'urban'
                                      ELSE 'rural' END area_type
                FROM read_csv('{ruc}')""", [cl])
            m = gm.build(con, str(pt), str(car), park, walk)
        m["mapped"] = gm.in_map(m, min_km)
        g = m[m["mapped"]]
        old = gm.by_band_old_metric(m)
        new = gm.by_band_new_metric(m)
        head = gm.headline(g)
        orig = gm.origin_summary(g)
        rob = gm.robustness(g, orig)
        by_area = pd.DataFrame([{"area_type": a, **gm.headline(x)}
                                for a, x in g.groupby("area_type")])
        m.to_parquet(out / "gapmap_od.parquet", compression="zstd")
        old.to_csv(out / "by_band_old_metric.csv", index=False)
        new.to_csv(out / "by_band_new_metric.csv", index=False)
        for v in ("high", "low"):
            for by in ("ratio", "diff"):
                gm.top_pairs(g, v, by).to_csv(out / f"gapmap_top50_{by}_{v}.csv", index=False)
        z = gpd.read_file(geo / "lsoa21_bgc_internal.geojson")[["LSOA21CD", "geometry"]]
        gz = z.merge(orig, left_on="LSOA21CD", right_on="o_zone", how="left").drop(columns="o_zone")
        gz.to_file(out / "gapmap_origin.geojson", driver="GeoJSON")
        fig, ax = plt.subplots(2, 2, figsize=(16, 10.5))
        for a, col, t, vmin, vmax, lab in (
                (ax[0, 0], "ratio_high", "PT ÷ car GC — car parking/access: high", 1, 5, "Σ PT GC ÷ Σ car GC"),
                (ax[0, 1], "ratio_low", "PT ÷ car GC — car parking/access: low (none)", 1, 5, "Σ PT GC ÷ Σ car GC"),
                (ax[1, 0], "diff_high", "PT − car GC (min) — high", 0, 120, "flow-weighted mean, minutes"),
                (ax[1, 1], "diff_low", "PT − car GC (min) — low (none)", 0, 120, "flow-weighted mean, minutes")):
            gz.plot(column=col, ax=a, cmap="RdYlGn_r", vmin=vmin, vmax=vmax, legend=True,
                    missing_kwds={"color": "lightgrey"}, linewidth=0,
                    legend_kwds={"label": lab, "shrink": 0.6})
            a.set_title(t)
            a.set_axis_off()
        fig.suptitle("\n".join(__import__("textwrap").wrap(label, 150)), fontsize=9)
        fig.savefig(out / "gapmap.png", dpi=150, bbox_inches="tight")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    short = m[~m["mapped"]]
    rec["result"] = {
        "headline_mapped": head, "min_distance_km": min_km,
        "excluded_short": {"pairs": int(len(short)), "trips": float(short["trips"].sum()),
                           "trip_share": float(short["trips"].sum() / m["trips"].sum()),
                           "headline": gm.headline(short)},
        "by_band_old_metric": old.round(3).to_dict("records"),
        "by_band_new_metric": new.round(3).to_dict("records"),
        "by_destination_area_type": by_area.round(3).to_dict("records"),
        "robustness_low_vs_high": {k: round(v, 3) if isinstance(v, float) else v
                                   for k, v in rob.items()},
        "origin_ratio_p10_p50_p90": {v: orig[f"ratio_{v}"].quantile([0.1, 0.5, 0.9]).round(2).tolist()
                                     for v in ("high", "low")},
        "centre_lsoas": len(cl), "car_period": car_period, "pt_period": pt_period,
        "weights": "OA resident commuters (nat_oa_flows)", "label": label}
    pd.set_option("display.width", 250)
    click.echo("old metric (mean of ratios, PT incl. walk-only) by distance band:")
    click.echo(old.round(3).to_string(index=False))
    click.echo("revised metric by distance band:")
    click.echo(new.round(3).to_string(index=False))
    click.echo(f"headline (mapped pairs): {json.dumps({k: round(v, 3) for k, v in head.items()})}")
    click.echo(f"by destination area type:\n{by_area.round(3).to_string(index=False)}")
    click.echo(f"robustness: {rec['result']['robustness_low_vs_high']}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@cli.command("spotchecks")
@click.option("--slots", default="AM=08:15,IP=13:20,EVE=20:40", show_default=True)
@click.option("--walk-kmh", type=float, default=None,
              help="Diagnostic: override routing.walk_speed_kmh for this run only.")
def spotchecks_cmd(slots: str, walk_kmh: float | None) -> None:
    """C4: model PT times for the spot-check journeys at three departure slots; CSV with
    blank observed columns for both pairings, and a README."""
    import datetime as dt
    import geopandas as gpd
    import pandas as pd
    import yaml
    from shapely.geometry import Point
    from r5py import TransportMode, TransportNetwork, TravelTimeMatrix
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    sc = pd.read_csv(cfg.root / "config" / "spotchecks_pt.csv").drop(columns=["depart_local"])
    slot = dict(x.split("=") for x in slots.split(","))
    if walk_kmh is not None:
        ps["routing.walk_speed_kmh"] = walk_kmh
    rec = runrecord.build(cfg, command="spotchecks")
    runrecord.write(cfg, rec)
    net = TransportNetwork(str(cfg.root / raw["osm"]["clip"]),
                           [str(cfg.root / raw["bus"]["out"]), str(cfg.root / raw["rail"]["out"])])
    rows = []
    for name, hhmm in slot.items():
        for _, r in sc.iterrows():
            o = gpd.GeoDataFrame({"id": [0]}, geometry=[Point(r.o_lon, r.o_lat)], crs="EPSG:4326")
            d = gpd.GeoDataFrame({"id": [1]}, geometry=[Point(r.d_lon, r.d_lat)], crs="EPSG:4326")
            t = TravelTimeMatrix(net, origins=o, destinations=d,
                                 departure=dt.datetime.combine(day, dt.time.fromisoformat(hhmm)),
                                 departure_time_window=dt.timedelta(minutes=ps["skims.departure_window_min"]),
                                 percentiles=[1, 25, 50, 75],
                                 transport_modes=[TransportMode.TRANSIT, TransportMode.WALK],
                                 max_time=dt.timedelta(minutes=ps["routing.max_trip_min"]),
                                 speed_walking=ps["routing.walk_speed_kmh"],
                                 max_public_transport_rides=ps["routing.max_rides"])
            rows.append({**r.to_dict(), "slot": name, "depart_local": hhmm,
                         "diagnostic_only": name == "EVE",
                         "model_best_min": t["travel_time_p1"].iloc[0],
                         "model_p25_min": t["travel_time_p25"].iloc[0],
                         "model_p50_min": t["travel_time_p50"].iloc[0],
                         "model_p75_min": t["travel_time_p75"].iloc[0]})
    out_df = pd.DataFrame(rows)
    out_df["modelled_date"] = str(day)
    for c in ("planner_duration_min", "observed_elapsed_min", "observed_date", "planner",
              "observed_route", "comment"):
        out_df[c] = ""
    out = cfg.runs_dir / rec["run_id"] / "pt_spotchecks.csv"
    out_df.to_csv(out, index=False)
    (out.parent / "README.md").write_text(PT_SPOT_README)
    rec["result"] = {"csv": str(out), "rows": len(out_df), "slots": slot,
                     "walk_speed_kmh": ps["routing.walk_speed_kmh"],
                     "walk_speed_overridden": walk_kmh is not None}
    click.echo(out_df[["id", "slot", "origin", "destination", "model_best_min", "model_p50_min"]]
               .to_string(index=False))
    click.echo(f"wrote {out}")
    runrecord.finish(cfg, rec, "ok")


@cli.command("spotchecks-eval")
@click.argument("filled", type=click.Path(exists=True))
@click.option("--model", "model_csv", type=click.Path(exists=True), default=None,
              help="Take the model columns from another `lab spotchecks` CSV (same rows).")
def spotchecks_eval(filled: str, model_csv: str | None) -> None:
    """C4: score a filled PT spot-check CSV. ``observed_elapsed_min`` holds
    "Leave H:MM, Arrive H:MM. N" (12-hour clock, resolved against the slot). Pairing A:
    planner duration (arrive − leave) vs model_best, ±15%. Pairing B: elapsed from the
    slot time (arrive − depart_local) within [p25, p75] or ±15% of p50. A row passes if
    either does; acceptance is ≥ 16 of 20 in AM and in IP (EVE is diagnostic)."""
    import re
    import pandas as pd
    cfg = LabConfig.load()
    r = pd.read_csv(filled)
    mcols = ["model_best_min", "model_p25_min", "model_p50_min", "model_p75_min"]
    if model_csv:
        m = pd.read_csv(model_csv)[["id", "slot", *mcols]]
        r = r.drop(columns=mcols).merge(m, on=["id", "slot"], validate="1:1")

    def parse(row):
        g = re.match(r"\s*Leave (\d+):(\d+), Arrive (\d+):(\d+)\.\s*(\d+)", str(row["observed_elapsed_min"]))
        if not g:
            raise click.ClickException(f"row {row['id']} {row['slot']}: cannot read "
                                       f"{row['observed_elapsed_min']!r}")
        lh, lm, ah, am, stated = map(int, g.groups())
        dh, dm = map(int, row["depart_local"].split(":"))
        dep = dh * 60 + dm

        def clock(h, mi):                      # 12-hour clock → first time at/after the slot
            t = (h % 12) * 60 + mi
            while t < dep:
                t += 720
            return t
        leave = clock(lh, lm)
        arrive = clock(ah, am)
        if arrive < leave:
            arrive += 720
        return pd.Series({"planner_duration_min": arrive - leave, "stated_duration_min": stated,
                          "elapsed_from_slot_min": arrive - dep})
    x = pd.concat([r, r.apply(parse, axis=1)], axis=1)
    x["a_ratio"] = x["planner_duration_min"] / x["model_best_min"]
    x["b_ratio_p50"] = x["elapsed_from_slot_min"] / x["model_p50_min"]
    x["pass_a"] = (x["a_ratio"] - 1).abs() <= 0.15
    x["pass_b"] = (x["elapsed_from_slot_min"].between(x["model_p25_min"], x["model_p75_min"])
                   | ((x["b_ratio_p50"] - 1).abs() <= 0.15))
    x["pass"] = x["pass_a"] | x["pass_b"]
    x["duration_mismatch"] = x["planner_duration_min"] != x["stated_duration_min"]
    rec = runrecord.build(cfg, command="spotchecks-eval", inputs=[
        {"name": f, "sha256": params.file_hash(Path(f))} for f in [filled, model_csv] if f])
    runrecord.write(cfg, rec)
    out = cfg.runs_dir / rec["run_id"] / "pt_spotchecks_scored.csv"
    x.to_csv(out, index=False)
    summ = x.groupby("slot").agg(rows=("pass", "size"), pass_a=("pass_a", "sum"),
                                 pass_b=("pass_b", "sum"), passing=("pass", "sum"),
                                 median_a_ratio=("a_ratio", "median"),
                                 median_b_ratio=("b_ratio_p50", "median")).round(3)
    acc = {s_: bool(summ.loc[s_, "passing"] >= 16) for s_ in ("AM", "IP") if s_ in summ.index}
    rec["result"] = {"csv": str(out), "summary": summ.reset_index().to_dict("records"),
                     "acceptance_16_of_20": acc,
                     "failures": x[~x["pass"] & (x["slot"] != "EVE")][
                         ["id", "slot", "origin", "destination"]].to_dict("records"),
                     "stated_duration_differs_from_clock_times":
                         x[x["duration_mismatch"]][["id", "slot", "observed_elapsed_min"]]
                         .to_dict("records")}
    click.echo(summ.to_string())
    click.echo(f"acceptance (>= 16 of 20): {acc}")
    click.echo(f"wrote {out}")
    runrecord.finish(cfg, rec, "ok")


@cli.command("spotchecks-car")
@click.option("--n", "n_links", default=20, show_default=True)
def spotchecks_car(n_links: int) -> None:
    """Car spot checks from held-out ANPR routes (validation half): the n links with
    observations whose routed length best matches the ANPR path. Modelled time = OSRM
    route on the calibrated period speeds (what the car skims use, turn penalties
    included) vs observed, IP and the AM peak hour. Pass: ≥ 16 of 20 within ±15%."""
    import pandas as pd
    import yaml
    from .congestion import validate as va
    from .supply import osrm
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    d = cfg.root / "data" / "interim" / "congestion"
    anpr = _anpr_inputs(cfg)
    if anpr is None:
        raise click.ClickException("run `lab congestion anpr-prep` first")
    obs = anpr["obs"]
    val = anpr["paths"][anpr["paths"]["half"] == "validation"]
    has = set(obs[obs["period"] == "IP"]["link_id"]) & set(obs[obs["period"] == "AMPH"]["link_id"])
    val = val[val["link_id"].isin(has)]
    fit_q = (val.groupby("link_id")["route_m"].first() / val.groupby("link_id")["link_m"].first() - 1).abs()
    pick = set(fit_q.sort_values().head(n_links).index)
    rec = runrecord.build(cfg, command="spotchecks-car", inputs=[
        {"name": str(d / "osrm_speeds" / f"speeds_{q}.csv"),
         "sha256": params.file_hash(d / "osrm_speeds" / f"speeds_{q}.csv")} for q in ("IP", "AMPH")])
    runrecord.write(cfg, rec)
    rows = []
    try:
        for per in ("IP", "AMPH"):
            ds = va.period_dataset(cfg.root / raw["osm"]["osrm_base"], d / "osrm_speeds" / f"speeds_{per}.csv",
                                   cfg.root / "data" / "interim" / "osrm" / f"hybrid_{per}")
            with osrm.Server(ds) as srv:
                m = va.anpr_modelled(cfg.root / "data/raw/bristol_anpr/journey_links.geojson", srv.port)
            m["link_id"] = m["link_id"].astype(obs["link_id"].dtype)
            j = m[m["link_id"].isin(pick)].merge(
                obs[obs["period"] == per][["link_id", "obs_s", "area"]], on="link_id")
            rows.append(j.assign(period=per).rename(columns={"mod_s": "model_s"}))
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    res = pd.concat(rows)
    res["rel_error"] = res["model_s"] / res["obs_s"] - 1
    res["within_15pct"] = res["rel_error"].abs() <= 0.15
    out = cfg.runs_dir / rec["run_id"] / "car_spotchecks_anpr.csv"
    res.to_csv(out, index=False)
    summ = res.groupby("period").agg(n=("within_15pct", "size"), passing=("within_15pct", "sum"),
                                     median_ratio=("rel_error", lambda e: 1 + e.median()),
                                     median_abs_err=("rel_error", lambda e: e.abs().median()))
    rec["result"] = {"csv": str(out), "links": len(pick),
                     "summary": summ.round(3).reset_index().to_dict("records"),
                     "pass_rule": ">= 16 of 20 within ±15%",
                     "passes": bool((summ["passing"] >= 16).all())}
    click.echo(summ.round(3).to_string())
    click.echo(f"wrote {out}")
    runrecord.finish(cfg, rec, "ok")


@cli.command("export-viz")
@click.argument("run_id")
@click.option("--compare", "compare_id", default=None, help="Run to compare against.")
def export_viz(run_id: str, compare_id: str | None) -> None:
    """Write tiles and JSON for the viewer and blog components."""
    _not_yet("P7a")


@cli.command()
def view() -> None:
    """Serve the viewer locally."""
    _not_yet("P7a")


@cli.command()
@click.argument("component", type=click.Choice(
    ["swipe-choropleth", "gap-map", "line-loads", "scorecard-bars"]))
@click.argument("run_id")
@click.option("--compare", "compare_id", default=None, help="Run to compare against.")
@click.option("--out", "out_dir", required=True, type=click.Path(),
              help="viz/embeds/<slug>")
def embed(component: str, run_id: str, compare_id: str | None, out_dir: str) -> None:
    """Build a self-contained blog embed from a recorded run."""
    _not_yet("P7a (gap-map) / P7b (the rest)")


@cli.command("params")
def params_cmd() -> None:
    """List every parameter with its provenance tag; flag placeholders."""
    cfg = LabConfig.load()
    for f in sorted((cfg.root / "params").glob("*.yaml")):
        ps = params.load(f)
        click.echo(f"== {f.name}: {len(ps)} parameters, "
                   f"{len(params.placeholders(ps))} [PLACEHOLDER]")
        for p in ps:
            unit = f" {p.unit}" if p.unit else ""
            click.echo(f"  [{p.tag:<11}] {p.path} = {p.value}{unit}")


def main() -> None:
    try:
        cli(standalone_mode=True)
    except Exception as exc:  # noqa: BLE001 — fail loudly with the real message
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
