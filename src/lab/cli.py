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
        darwin_licence = "National Rail open data terms (wording to confirm, A5)"
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
