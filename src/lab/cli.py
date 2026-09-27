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
        res = g.build(con, names, day, cfg.extent, a.clip_box(cfg), zones)
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
        if not isinstance(v, list):
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
                            d / "segments_annotated.parquet", max_match_m)
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
def congestion_avl(days: tuple[str, ...], source: str) -> None:
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
                                        d / "avl" / f"traversals_{source}_{x}.parquet")
                click.echo(f"  {x}: {res[x]}")
    except Exception:
        rec["result"] = res
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
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
