"""Supply: rail, bus, OSM, DEM, AVL."""
from __future__ import annotations

import sys

import click

from .. import params, runrecord
from ..config import LabConfig

@click.group()
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
    from ..supply import avl as a, feeds, gtfs_rail as g
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
        from ..supply import validate
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


@supply.command("rail-infrastructure")
def supply_rail_infrastructure() -> None:
    """Darwin timing points (calls and passing points) as a side table, and the sections
    between them as data in scenarios/infrastructure/ (plans/P3.md D6, D8)."""
    import datetime as dt
    import duckdb
    import pandas as pd
    import yaml
    from ..supply import avl as a, feeds, gtfs_rail as g
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    rc = raw["rail"]
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    box = a.clip_box(cfg)
    paths = {k: cfg.root / rc[k] for k in ("darwin_timetable", "darwin_ref", "naptan")}
    rec = runrecord.build(cfg, command="supply-rail-infrastructure", inputs=[
        {"name": k, "sha256": feeds.get(cfg, k if k != "naptan" else "naptan_rail")["sha256"]} for k in paths])
    runrecord.write(cfg, rec)
    try:
        ref, nap = g.read_ref(paths["darwin_ref"]), g.read_naptan(paths["naptan"])
        rows = g.timing_points(paths["darwin_timetable"], ref, nap, day, box)
        secs = g.sections_from_points(rows, ref, nap, box)
        df = pd.DataFrame(rows)
        df.insert(0, "service_date", str(day))
        with duckdb.connect(str(cfg.lab_db)) as con:
            con.register("df_", df)
            con.execute("CREATE OR REPLACE TABLE rail_timing_point AS SELECT * FROM df_")
        out = cfg.root / raw["baseline"]["infrastructure_dir"] / "darwin_sections.yaml"
        head = ("# GENERATED by `lab supply rail-infrastructure` — do not edit by hand.\n"
                f"# Directed sections between consecutive Darwin timing points (calls and passing\n"
                f"# points) used on {day} by journeys with a station in the clip box, from one point\n"
                "# before the first such station to one after the last. `trains` is that day's count\n"
                "# of journeys of every kind (Darwin holds no freight); base use by period is counted\n"
                "# in the capacity check, not here. Limits and freight allowances are set in P3b-3.\n"
                f"# Source: Darwin Push Port timetable {ref.timetable_id} (NRE OGL; National Rail).\n")
        out.write_text(head + yaml.safe_dump({"timetable": ref.timetable_id, "service_date": str(day),
                                              "sections": secs}, sort_keys=False, width=200))
        j = df.drop_duplicates("rid")
        res = {"journeys": int(len(j)), "timing_points": int(len(df)),
               "by_kind": df.kind.value_counts().to_dict(),
               "journeys_by_exclusion": j.exclusion.fillna("in passenger GTFS or cut by the box").value_counts().to_dict(),
               "sections": len(secs), "sections_between_two_stations": sum(s["from_station"] and s["to_station"] for s in secs),
               "busiest_sections": [(s["from"], s["to"], s["trains"]) for s in sorted(secs, key=lambda s: -s["trains"])[:6]],
               "table": "lab.duckdb rail_timing_point", "out": str(out)}
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = res
    for k, v in res.items():
        click.echo(f"  {k}: {v}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@supply.command("bus")
def supply_bus() -> None:
    """BODS GTFS -> clipped, de-duplicated single-date bus GTFS; validator; registry."""
    import datetime as dt
    import duckdb
    import yaml
    from ..supply import avl as a, feeds, gtfs_bods as g, validate
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
    from ..supply import avl as a, feeds, points
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
    from ..supply import feeds, smoke
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
    import json
    import yaml
    from ..supply import bus_variants as bv
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


@supply.command("dem")
def supply_dem() -> None:
    """Terrain raster for the clip box from the national grid in config; registered as a feed."""
    import datetime as dt
    import hashlib
    import yaml
    from ..supply import avl as a, dem, feeds
    cfg = LabConfig.load()
    dc = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())["dem"]
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    src, out = cfg.root / dc["national_zip"], cfg.root / dc["out"]
    md5 = hashlib.md5(src.read_bytes()).hexdigest()
    if md5 != dc["md5"]:
        raise click.ClickException(f"{src}: md5 {md5} is not the published {dc['md5']}")
    rec = runrecord.build(cfg, command="supply-dem", inputs=[
        {"name": dc["source_url"], "md5": md5, "version": dc["version"]}])
    runrecord.write(cfg, rec)
    try:
        stats = dem.build(src, a.clip_box(cfg), out, ps["supply.dem_res_deg"])
        now = dt.datetime.now(dt.timezone.utc)
        feeds.register(cfg, feed_id="dem_national", kind="dem", source_url=dc["source_url"],
                       path=src, downloaded_at=now, licence=dc["licence"],
                       notes=f"version {dc['version']}; md5 {md5}")
        feeds.register(cfg, feed_id="dem_clip", kind="dem", source_url="mosaicked + warped "
                       "by `lab supply dem`", path=out, downloaded_at=now,
                       licence=dc["licence"], notes=str(stats))
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = stats
    click.echo(f"  {stats}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@supply.command("osm")
def supply_osm() -> None:
    """Download, verify, merge and clip the OSM extracts; register them as feeds."""
    import datetime as dt
    import yaml
    from ..supply import avl as a, feeds, osm
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
    from ..supply import avl as a
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
    from ..supply import avl as a
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
    from ..supply import avl as a
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
