"""Scenario engine: validate, build, capacity, cost, draw-rail, new."""
from __future__ import annotations

from pathlib import Path

import click

from .. import params, runrecord
from ..config import LabConfig

@click.group()
def scenario() -> None:
    """Create and manage scenarios."""


def _scenario_ctx():
    import yaml
    from .. import coverage as cov, scenario as scn
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    sdir = cfg.root / raw["baseline"]["scenarios_dir"]
    sections = set()
    for f in sorted((cfg.root / raw["baseline"]["infrastructure_dir"]).glob("*.yaml")):
        secs = (yaml.safe_load(f.read_text()) or {}).get("sections", [])
        if isinstance(secs, list):                 # capacity.yaml keys its overrides by section instead
            for sec in secs:
                sections.add((sec["from"], sec["to"]))
    feeds_ = [cov.read_gtfs(cfg.root / raw[k]["out"]) for k in ("bus", "rail")]
    periods = set(ps["scenario.headway_periods"]) | {"OP"}
    cat = scn.catalogue_from_gtfs(feeds_, sections, periods, cfg.extent,
                                  tuple(float(k) for k in ps["scenario.profile_allowance"]))
    return cfg, raw, ps, sdir, cat, scn


def _load_chain(scenario_id: str, sdir, cat, ps, scn):
    """Validate a scenario and its ancestors, root first; each child is checked against
    the network its parent's ops leave behind. Returns the list, root first."""
    chain, sid, seen = [], scenario_id, set()
    while sid is not None:
        if sid in seen:
            raise click.ClickException(f"scenario parents form a loop at {sid}")
        seen.add(sid)
        f = sdir / f"{sid}.yaml"
        if not f.is_file():
            raise click.ClickException(f"no scenario file {f}")
        import yaml
        chain.append(f)
        sid = (yaml.safe_load(f.read_text()) or {}).get("parent")
    out = []
    for f in reversed(chain):
        s = scn.load(f, cat, stop_tolerance_m=ps["scenario.stop_alignment_tolerance_m"],
                     offset_count=ps["scenario.offset_count"])
        out.append(s)
        # carry the network forward: re-apply this scenario's ops to the catalogue
        work = cat.copy()
        files: list = []
        for i, (kind, body) in enumerate(s.ops):
            scn._check_op(kind, body, f.parent, work, ps["scenario.stop_alignment_tolerance_m"],
                          f"{s.id} op {i + 1} ({kind})", files)
        cat = work
    return out


@scenario.command("validate")
@click.argument("scenario_id")
@click.option("--register/--no-register", default=True, show_default=True,
              help="Record the scenario and its ops in lab.duckdb.")
def scenario_validate(scenario_id: str, register: bool) -> None:
    """Check a scenario file and its ancestors against every rule; print its spec_hash."""
    import datetime as dt
    import json
    import duckdb
    cfg, raw, ps, sdir, cat, scn = _scenario_ctx()
    try:
        chain = _load_chain(scenario_id, sdir, cat, ps, scn)
    except scn.ScenarioError as e:
        raise click.ClickException(str(e))
    s = chain[-1]
    click.echo(f"{s.id}: valid. parent {s.parent}; {len(s.ops)} ops; offsets "
               f"{[round(x, 3) for x in s.offsets]}; {len(s.files)} referenced files")
    for line in s.not_yet_modelled:
        click.echo(f"  {line}")
    click.echo(f"  spec_hash {s.spec_hash}")
    if register:
        with duckdb.connect(str(cfg.lab_db)) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS scenario (scenario_id VARCHAR PRIMARY KEY,
                parent_id VARCHAR, description VARCHAR, landuse_version VARCHAR, created_at TIMESTAMPTZ,
                spec_hash VARCHAR)""")
            con.execute("""CREATE TABLE IF NOT EXISTS scenario_op (scenario_id VARCHAR, seq INTEGER,
                op_type VARCHAR, params JSON)""")
            for x in chain:
                con.execute("INSERT OR REPLACE INTO scenario VALUES (?, ?, ?, ?, ?, ?)",
                            [x.id, x.parent, x.description, json.dumps(x.landuse),
                             dt.datetime.now(dt.timezone.utc), x.spec_hash])
                con.execute("DELETE FROM scenario_op WHERE scenario_id = ?", [x.id])
                for i, (kind, body) in enumerate(x.ops):
                    con.execute("INSERT INTO scenario_op VALUES (?, ?, ?, ?)", [x.id, i + 1, kind, json.dumps(body)])
        click.echo(f"  registered {[x.id for x in chain]} in lab.duckdb")


def _same_tables(a: dict, b: dict) -> bool:
    """Two feeds hold the same GTFS tables (as written and read back: all strings)."""
    import io
    import pandas as pd
    if set(a) != set(b):
        return False
    for t in a:
        buf = io.StringIO()
        b[t].to_csv(buf, index=False)
        buf.seek(0)
        if not a[t].fillna("").equals(pd.read_csv(buf, dtype=str).fillna("")):
            return False
    return True


def _build_scenario(scenario_id: str, log=click.echo) -> dict:
    """Generate a scenario's GTFS for every offset (cached by chain hash + offset +
    nothing else: the generator does not depend on the network version). Returns the
    scenario, its chain hash, the output directory and the per-offset reports."""
    import hashlib
    import json
    import shutil
    import datetime as dt
    import duckdb
    from .. import coverage as cov, generator as gen
    from ..supply import validate
    cfg, raw, ps, sdir, cat, scn = _scenario_ctx()
    try:
        chain = _load_chain(scenario_id, sdir, cat, ps, scn)
    except scn.ScenarioError as e:
        raise click.ClickException(str(e))
    s = chain[-1]
    src = {"bus": cfg.root / raw["bus"]["out"], "rail": cfg.root / raw["rail"]["out"]}
    chain_hash = hashlib.sha256("|".join([x.spec_hash for x in chain]
                                         + [params.file_hash(p) for p in src.values()]).encode()).hexdigest()[:12]
    gen_sha = params.file_hash(cfg.root / "src" / "lab" / "generator.py")
    out = cfg.root / raw["baseline"]["built_dir"] / s.id / chain_hash
    done = out / "report.json"
    old = json.loads(done.read_text()) if done.is_file() else None
    if old and old.get("generator_sha") == gen_sha:
        log(f"  {s.id}: feeds already built for chain {chain_hash}")
        return {"scenario": s, "chain": chain, "chain_hash": chain_hash, "dir": out, "report": old}
    # Built by other generator code (or not at all): generate again. A file whose tables come
    # out the same keeps its bytes and hash, so skims keyed to it stay valid; one that
    # differs is rewritten, and everything downstream is keyed to the new hash.
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        tp = con.execute("SELECT * FROM rail_timing_point").df()
    runs, dwell = gen.working_times(tp)
    ctx = {"periods": cov.parse_periods({k: ps[f"periods.{k}"] for k in ps["scenario.headway_periods"]}),
           "service_date": day.strftime("%Y%m%d"), "runs": runs, "dwell": dwell,
           "min_trains": ps["scenario.working_time_min_trains"],
           "allowance": ps["scenario.profile_allowance"],
           "agency_url": raw["baseline"]["generated_agency_url"]}
    parent = {k: gen.read_feed(p) for k, p in src.items()}
    ops = [op for x in chain for op in x.ops]
    report = {"scenario": s.id, "chain": [x.id for x in chain], "chain_hash": chain_hash,
              "generator_sha": gen_sha, "spec_hash": s.spec_hash, "offsets": s.offsets, "not_yet_modelled": s.not_yet_modelled,
              "by_offset": {}}
    for k, frac in enumerate(s.offsets if ops else [0.0]):
        try:
            feeds_k, reps = gen.apply(parent, ops, sdir, ctx, frac)
        except gen.GeneratorError as e:
            raise click.ClickException(f"{s.id}: {e}")
        d = out / f"offset_{k}"
        d.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, f in feeds_k.items():
            dst = d / f"{name}.zip"
            if name in parent and all(f[t].equals(parent[name][t]) for t in f):
                shutil.copyfile(src[name], dst)                 # untouched: the parent's file, same hash
                files[name] = {"changed": False}
            elif dst.is_file() and _same_tables(gen.read_feed(dst), f):
                was = (old or {}).get("by_offset", {}).get(str(k), {}).get("files", {}).get(name, {})
                files[name] = {"changed": True, "kept_existing_file": True,
                               "validator_errors": was.get("validator_errors", 0),
                               "validator_warnings": was.get("validator_warnings")}
            else:
                gen.write_feed(f, dst)
                val = validate.run(cfg.root / raw["rail"]["validator"], dst, d / f"{name}.validator", day, "gb")
                if val["errors"]:
                    raise click.ClickException(f"{s.id} offset {k}: {name}.zip fails the GTFS validator: {val['codes']}")
                files[name] = {"changed": True, "validator_errors": val["errors"],
                               "validator_warnings": val["warnings"]}
            files[name]["sha256"] = params.file_hash(dst)
        report["by_offset"][str(k)] = {"fraction": frac, "files": files, "ops": reps}
        log(f"  {s.id} offset {k} ({frac:.3f}): {', '.join(n + (' (changed)' if v['changed'] else '') for n, v in files.items())}")

        def _uses(o):                       # does any part of this op's report take the offset?
            return bool(o.get("uses_offsets")) or any(_uses(v) for v in o.values() if isinstance(v, dict))
        if k == 0 and not any(_uses(o) for o in reps):
            # every generated or re-timed service is anchored to the old timetable (or there
            # is none): the offsets would give the same network three times
            report["single_offset_reason"] = "no service in this scenario takes a timetable offset"
            log(f"  {s.id}: no service takes an offset; one network")
            break
    done.write_text(json.dumps(report, indent=1, default=str))
    return {"scenario": s, "chain": chain, "chain_hash": chain_hash, "dir": out, "report": report}


@scenario.command("build")
@click.argument("scenario_id")
def scenario_build(scenario_id: str) -> None:
    """Generate a scenario's GTFS feeds, one set per timetable offset; validate them."""
    import json
    cfg = LabConfig.load()
    rec = runrecord.build(cfg, command="scenario-build")
    runrecord.write(cfg, rec)
    try:
        b = _build_scenario(scenario_id)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["scenario"] = {"id": b["scenario"].id, "spec_hash": b["scenario"].spec_hash}
    rec["result"] = {"chain_hash": b["chain_hash"], "dir": str(b["dir"]), "report": b["report"]}
    r0 = b["report"]["by_offset"]["0"]
    for op in r0["ops"]:
        click.echo("  " + json.dumps({k: v for k, v in op.items() if k not in ("legs",)}, default=str)[:600])
        for holder in (op, op.get("extend_to", {})):
            for lg in holder.get("legs", []):
                click.echo(f"    {lg['from']} → {lg['to']}: {lg['dist_m'] / 1000:.2f} km; {lg['fwd_s'] / 60:.1f} min "
                           f"({lg['fwd_rule']}); back {lg['rev_s'] / 60:.1f} min ({lg['rev_rule']})")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


def _capacity(cfg, raw, ps, b: dict, tp) -> dict:
    """The capacity table for a built scenario, one check per timetable offset."""
    import pandas as pd
    import yaml
    from .. import capacity as cp, coverage as cov, generator as gen
    idir = cfg.root / raw["baseline"]["infrastructure_dir"]
    sections = []
    for f in sorted(idir.glob("*.yaml")):
        sections += (yaml.safe_load(f.read_text()) or {}).get("sections") or [] if f.name != "capacity.yaml" else []
    capd = yaml.safe_load((idir / "capacity.yaml").read_text())
    tp_runs = cp.section_run_times(tp)
    periods = cov.parse_periods({k: ps[f"periods.{k}"] for k in ps["scenario.headway_periods"]})
    parent_rail = gen.read_feed(cfg.root / raw["rail"]["out"])
    tables, summary = [], {}
    for k, off in b["report"]["by_offset"].items():
        feeds_k = {f.stem: gen.read_feed(f) for f in sorted((b["dir"] / f"offset_{k}").glob("*.zip"))}
        # today's trains no longer in the scenario's rail feed have been removed or re-timed away
        gone = set(parent_rail["trips"].trip_id) - set(feeds_k["rail"]["trips"].trip_id)
        # journeys cut short (`truncate_at`): counted again below, but only the part that still runs
        cut = [(t, op["truncate"]) for op in off["ops"] if "truncate" in op for t in op["truncate"]["trips"]]
        gone |= {t for t, _ in cut}
        retimed = sorted({r for r in feeds_k["rail"]["trips"].route_id[feeds_k["rail"]["trips"].trip_id.str.startswith("gen")]})
        base = cp.base_use(tp, gone)
        run_min = {}
        for op in off["ops"]:
            for holder in (op, op.get("extend_to", {})):
                for lg in holder.get("legs", []):
                    tps = lg.get("timing_points") or []
                    for a, c in zip(tps, tps[1:]):
                        share = 1 / max(len(tps) - 1, 1)
                        run_min[(a, c)], run_min[(c, a)] = lg["fwd_s"] / 60 * share, lg["rev_s"] / 60 * share
        # works to existing railway in this scenario's chain: redoubling, passing loops
        capk = cp.apply_works(capd, [op["works"] for op in off["ops"] if "works" in op])
        lim = cp.limits(sections, capk, cp.base_use(tp), run_min, tp_runs)    # limits from today's timetable, untouched
        # journeys added as copies of today's (fill_span, and re-timing), unless a later op took them away
        in_feed = set(feeds_k["rail"]["trips"].trip_id)
        copies = [c for op in off["ops"] for key in ("fill_span", "headways") for c in (op.get(key) or {}).get("copies", [])
                  if c.get("new") in in_feed]
        copies += [{"of": t, "shift_min": 0.0, "new": t, "trim": {"tiploc": u["tiploc"], "keep": u["keep"]}} for t, u in cut if t in in_feed]
        try:
            added = cp.copied_use(tp, copies)
        except ValueError as e:
            raise click.ClickException(f"{b['scenario'].id}: {e}")
        use = pd.concat([cp.scenario_use(feeds_k, off["ops"]), added]).groupby(["from", "to", "hour"]).trains.sum().reset_index()
        chk = cp.check(lim, base, use, periods)
        chk.insert(0, "offset", int(k))
        tables.append(chk)
        at = chk[chk.total_tph == chk.limit_tph] if len(chk) else chk
        summary[k] = {"sections_used": int(chk[["from", "to"]].drop_duplicates().shape[0]) if len(chk) else 0,
                      "over": chk[chk.over][["from", "to", "period", "total_tph", "limit_tph", "limit_from"]].to_dict("records") if len(chk) else [],
                      "at_the_limit": at[["from", "to", "limit_tph", "limit_from", "modelled_track_tph"]].drop_duplicates().to_dict("records") if len(chk) else [],
                      "sections_where_todays_floor_is_the_limit": int(chk[chk.limit_from.str.startswith("today")][["from", "to"]].drop_duplicates().shape[0]) if len(chk) else 0,
                      "rail_routes_retimed_counted_over_the_timing_points_they_copy": retimed,
                      # trips added by `fill_span`: counted on the track of the journey each copies
                      "trips_added_to_todays_routes_counted_on_todays_track": {
                          op["route_id"]: sum(len(v) for v in op["fill_span"]["added"].values())
                          for op in off["ops"] if "fill_span" in op}}
    table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    return {"table": table, "summary": summary,
            "caveat": "Catches overloads only; proves nothing about a timetable. Base use is today's trains in the "
                      "Darwin timetable (no freight); freight is a modelled allowance of trains per hour on the "
                      "sections named in scenarios/infrastructure/capacity.yaml; every limit is modelled; "
                      "junction conflicts are not assessed."}


@scenario.command("capacity")
@click.argument("scenario_id")
def scenario_capacity(scenario_id: str) -> None:
    """Track-capacity check for a scenario's generated and extended rail services."""
    import json
    import duckdb
    import yaml
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    rec = runrecord.build(cfg, command="scenario-capacity")
    run_dir = runrecord.write(cfg, rec).parent
    try:
        b = _build_scenario(scenario_id)
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            tp = con.execute("SELECT * FROM rail_timing_point").df()
        c = _capacity(cfg, raw, ps, b, tp)
        c["table"].to_csv(run_dir / "capacity.csv", index=False)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["scenario"] = {"id": b["scenario"].id, "spec_hash": b["scenario"].spec_hash}
    rec["result"] = {"summary": c["summary"], "caveat": c["caveat"], "table": str(run_dir / "capacity.csv")}
    t = c["table"]
    if len(t):
        show = t[t.offset == 0].drop(columns=["offset", "limit_basis", "busiest_today_tph"])
        click.echo(show.to_string(index=False))
    click.echo(json.dumps(c["summary"], indent=1))
    click.echo(c["caveat"])
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


def _scenario_cost(b: dict, stage: int | None = None) -> dict:
    """Capital and operating cost of a scenario's own ops against its parent (SPEC §8.3)."""
    import json
    import pandas as pd
    import yaml
    from .. import costs, generator as gen
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    lib = costs.Library.load(cfg.root / "params" / "costs.yaml")
    s = b["scenario"]
    sdir = s.path.parent
    r0 = b["report"]["by_offset"]["0"]
    reps = r0["ops"][len(r0["ops"]) - len(s.ops):]              # this scenario's own ops, last in the chain
    mine = {n: gen.read_feed(b["dir"] / "offset_0" / f"{n}.zip") for n in r0["files"]}
    if s.parent == raw["baseline"]["scenario"]:
        pdir = {n: cfg.root / raw[n]["out"] for n in ("bus", "rail")}
    else:
        pb = _build_scenario(s.parent, lambda m: None)
        pdir = {n: pb["dir"] / "offset_0" / f"{n}.zip" for n in pb["report"]["by_offset"]["0"]["files"]}
    # only the feeds that differ from the parent's are read twice
    differ = {n for n in mine if n not in pdir or params.file_hash(pdir[n]) != r0["files"][n]["sha256"]}
    km_s = costs.feed_km({n: mine[n] for n in differ})
    km_p = costs.feed_km({n: gen.read_feed(pdir[n]) for n in differ if n in pdir})

    def stops_of(path: str) -> list[dict]:
        return [f["properties"] for f in json.loads((sdir / path).read_text())["features"]]
    services = []
    for (kind, op), rep in zip(s.ops, reps):
        if kind in ("add_line", "replace_route"):
            q = costs.service_quantities(rep, op["mode"], stops_of(op["stops"]), op["headways_min"])
            q["name"] = rep["route_id"]
            q["traction"] = op.get("traction", "diesel")
        elif kind == "modify_route" and "extend_to" in rep:
            routes = pd.concat([f["routes"] for f in mine.values()])
            rt = routes.set_index("route_id").route_type.get(op["route_id"])
            mode = {"2": "heavy_rail", "0": "tram", "1": "metro"}.get(rt, "bus")
            hw = (rep.get("fill_span") or {}).get("headway_min") or op.get("headways_min")
            q = costs.service_quantities(rep["extend_to"], mode, stops_of(op["extend_to"]["stops"]), hw)
            q["name"] = f"{op['route_id']} extension"
        else:
            continue
        q["fleet"] = costs.fleet(q, lib)
        services.append(q)
    # two directions of one service extended over the same track and stops: built once
    seen, build = set(), []
    for q in services:
        key = (tuple(sorted(q["km_by_type"].items())), tuple(x["stop_id"] for x in q["new_stops"]))
        if q["is_extension"] and key in seen:
            build.append(q | {"km_by_type": {}, "tunnel_runs": [], "new_stops": [],
                              "fleet": q["fleet"] | {"in_service": None}})
            continue
        seen.add(key)
        build.append(q)
    cap, notes = costs.capex(build, lib, stage)
    wcap, wnotes = costs.works_capex([rep["works"] for (kind, _), rep in zip(s.ops, reps) if kind == "works"], lib, stage)
    cap, notes = cap + wcap, notes + wnotes
    op_lines, op_notes = costs.opex(km_p, km_s, build, lib)
    out = costs.summarise(cap, op_lines, notes + op_notes, lib, stage)
    out["services"] = [{k: q[k] for k in ("name", "mode", "km_by_type", "tunnel_runs", "one_way_min", "fleet")}
                       | {"new_stops": [x["stop_id"] for x in q["new_stops"]]} for q in services]
    out["against"] = s.parent
    return out


@scenario.command("cost")
@click.argument("scenario_id")
@click.option("--stage", type=click.IntRange(1, 3), default=None, help="TAG optimism-bias stage (default: the library's).")
def scenario_cost(scenario_id: str, stage: int | None) -> None:
    """Capital and operating cost of a scenario's own ops, against its parent."""
    import json
    cfg = LabConfig.load()
    rec = runrecord.build(cfg, command="scenario-cost")
    run_dir = runrecord.write(cfg, rec).parent
    try:
        b = _build_scenario(scenario_id)
        c = _scenario_cost(b, stage)
        (run_dir / "cost.json").write_text(json.dumps(c, indent=1, default=str))
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["scenario"] = {"id": b["scenario"].id, "spec_hash": b["scenario"].spec_hash}
    rec["result"] = {k: c[k] for k in ("price_base_year", "optimism_bias_stage", "complete", "not_costed", "against")} | {
        "capex": {k: c["capex"][k] for k in ("raw", "with_optimism_bias")},
        "opex_per_year": {k: v for k, v in c["opex_per_year"].items() if k != "lines"}, "table": str(run_dir / "cost.json")}
    m = lambda v: "(no central figure)" if v is None else (f"£{v / 1e6:,.1f}m" if abs(v) >= 1e6 else f"£{v / 1e3:,.0f}k")  # noqa: E731
    click.echo(f"{b['scenario'].id} against {c['against']}, {c['price_base_year']} prices")
    for x in c["capex"]["lines"]:
        cost = (f"up to {m(x['high'])}, an upper bound only  (+{x['ob']:.0%})" if x.get("upper_bound_only") else
                "not costed" if x["cost"] is None else f"{m(x['low'])} – {m(x['cost'])} – {m(x['high'])}  (+{x['ob']:.0%})")
        click.echo(f"  {x['item']:<58} {x['quantity']:>8g} {x['per']:<10} {cost}  [{x['tag']}]")
    r, o = c["capex"]["raw"], c["capex"]["with_optimism_bias"]
    click.echo(f"  capital, low – central – high: {m(r['low'])} – {m(r['central'])} – {m(r['high'])}; with optimism bias "
               f"(stage {c['optimism_bias_stage']}) {m(o['low'])} – {m(o['central'])} – {m(o['high'])}")
    for x in c["opex_per_year"]["lines"]:
        cost = "not costed" if x["cost"] is None else f"{m(x['low'])} – {m(x['cost'])} – {m(x['high'])} a year"
        click.echo(f"  {x['item']:<58} {x['quantity']:>12,.0f} {x['per']:<18} {cost}  [{x['tag']}]")
    oy = c["opex_per_year"]
    click.echo(f"  operating, a year: {m(oy['low'])} – {m(oy['central'])} – {m(oy['high'])}")
    for n in c["not_costed"]:
        click.echo(f"  NOT COSTED: {n}")
    for n in c["notes"]:
        click.echo(f"  note: {n}")
    click.echo(f"  placeholders behind these figures: {', '.join(c['unit_costs_used_by_tag'].get('PLACEHOLDER', [])) or 'none'}")
    click.echo(c["caveat"])
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@scenario.command("draw-rail")
@click.argument("spec", type=click.Path(exists=True))
def scenario_draw_rail(spec: str) -> None:
    """Write a rail alignment GeoJSON from a drawing spec (waypoints routed along OSM track)."""
    import json
    import yaml
    from ..supply import rail_geometry as rg
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    sp = Path(spec)
    doc = yaml.safe_load(sp.read_text())
    g = rg.rail_graph(str(cfg.root / raw["osm"]["clip"]), set(doc["kinds"]))
    feats = []
    for i, leg in enumerate(doc["legs"]):
        if leg.get("straight"):
            # not along existing track (a tunnel, a new chord): the drawn points themselves,
            # starting where the previous leg ended
            import math
            pts = ([feats[-1]["geometry"]["coordinates"][-1]] if feats else []) + [list(p) for p in leg["via"]]
            m = sum(math.hypot((b[0] - a[0]) * 111_320 * math.cos(math.radians(a[1])), (b[1] - a[1]) * 111_320)
                    for a, b in zip(pts, pts[1:]))
            r = {"coords": pts, "length_m": round(m, 1), "snap_m": [], "kinds_m": {"drawn": round(m, 1)}}
        else:
            r = rg.path(g, [tuple(p) for p in leg["via"]], 2 * ps["scenario.draw_snap_m"], ps["scenario.draw_snap_m"])
        if feats and feats[-1]["geometry"]["coordinates"][-1] != r["coords"][0]:
            # successive legs may meet a station on different tracks: join them
            r["coords"] = [feats[-1]["geometry"]["coordinates"][-1]] + r["coords"]
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": r["coords"]},
                      "properties": {"alignment_type": leg["alignment_type"], "timing_points": leg.get("timing_points"),
                                     **({"speed_profile": leg["speed_profile"]} if leg.get("speed_profile") else {}),
                                     "length_m": r["length_m"], "osm_railway_m": r["kinds_m"],
                                     "source": f"OSM ways via `lab scenario draw-rail {sp.name}`; extract {raw['osm']['extract_date']}"}})
        click.echo(f"  leg {i + 1}: {r['length_m'] / 1000:.2f} km, waypoints {r['snap_m']} m from the track used, {r['kinds_m']}")
    out = sp.parent / doc["out"]
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    click.echo(f"wrote {out}")


@scenario.command("new")
@click.argument("scenario_id")
@click.option("--from", "parent", default="B2028", show_default=True)
@click.option("--description", default=None)
def scenario_new(scenario_id: str, parent: str, description: str | None) -> None:
    """Create a scenario YAML from a parent: a valid file with no ops yet."""
    cfg, raw, ps, sdir, cat, scn = _scenario_ctx()
    out = sdir / f"{scenario_id}.yaml"
    if out.exists():
        raise click.ClickException(f"{out} already exists")
    if not (sdir / f"{parent}.yaml").is_file():
        raise click.ClickException(f"parent {parent} has no scenario file in {sdir}")
    import yaml
    pdoc = yaml.safe_load((sdir / f"{parent}.yaml").read_text())
    out.write_text(
        f"# Scenario {scenario_id}, from {parent}. Ops are applied in order to the parent's network\n"
        "# (SPEC §5). Alignments go in lines/, stops in stops/; validate with\n"
        f"# `lab scenario validate {scenario_id}`.\n"
        + yaml.safe_dump({"id": scenario_id, "parent": parent,
                          "description": description or f"(describe {scenario_id})",
                          "landuse": pdoc.get("landuse"), "ops": []}, sort_keys=False))
    try:
        _load_chain(scenario_id, sdir, cat, ps, scn)
    except scn.ScenarioError as e:
        out.unlink()
        raise click.ClickException(str(e))
    click.echo(f"wrote {out}")


def _offset_skims(cfg, raw, ps, b: dict, skim_dir: Path, per: str) -> list[Path]:
    """The PT skim of each offset's own network for one period, as `lab run` named them;
    an error if any is missing."""
    from .. import run as rn
    out = []
    for k in b["report"]["by_offset"]:
        gtfs = {f.stem: f for f in sorted((b["dir"] / f"offset_{k}").glob("*.zip"))}
        f_k = skim_dir / "pt" / f"{per}.offset_{k}.{rn.offset_network(cfg, raw, ps, gtfs)['version']}.parquet"
        if not f_k.is_file():
            raise click.ClickException(f"{b['scenario'].id}: no {per} skim for offset {k} ({f_k.name}): "
                                       f"run `lab run {b['scenario'].id}` first")
        out.append(f_k)
    return out


def _scenario_skim_dir(cfg, raw, b: dict, base_version: str) -> Path:
    return cfg.root / raw["baseline"]["skims"] / b["scenario"].id / f"{base_version}+{b['chain_hash']}"


def _run_scenario(scenario_id: str, periods: list[str]) -> None:
    """Supply, skims and connectivity for a scenario, against its parent (P3b-5).
    Demand-dependent metrics (mode shares, boardings, loads, benefits) arrive in P5–P6."""
    import datetime as dt
    import json
    import duckdb
    import pandas as pd
    import yaml
    from .. import network, run as rn
    from ..coverage_cli import coverage_score, coverage_stops, coverage_walk
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    rec = runrecord.build(cfg, command="run", scenario={"id": scenario_id, "spec_hash": "pending"})
    run_dir = runrecord.write(cfg, rec).parent
    log = lambda m: click.echo(f"  {m}", err=True)  # noqa: E731
    try:
        b = _build_scenario(scenario_id, click.echo)
        s = b["scenario"]
        rec["scenario"] = {"id": s.id, "spec_hash": s.spec_hash}
        base = network.settings(cfg, raw, ps)
        root_id = raw["baseline"]["scenario"]
        if s.parent is None:
            raise click.ClickException(f"{s.id} is a root baseline: its skims come from `lab skims`, not `lab run`")
        out_dir = _scenario_skim_dir(cfg, raw, b, base["version"])
        # the capacity check first: it is cheap, and an overloaded section should be seen
        # before two hours of skims
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            tp = con.execute("SELECT * FROM rail_timing_point").df()
        capc = _capacity(cfg, raw, ps, b, tp)
        capc["table"].to_csv(run_dir / "capacity.csv", index=False)
        for k, v in capc["summary"].items():
            click.echo(f"  capacity, offset {k}: {v['sections_used']} sections used, {len(v['over'])} over, "
                       "against modelled limits")
        nets, combined, spread, by_offset = {}, {}, {}, {}
        for per in periods:
            frames = []
            for k in b["report"]["by_offset"]:
                gtfs = {f.stem: f for f in sorted((b["dir"] / f"offset_{k}").glob("*.zip"))}
                nw = rn.offset_network(cfg, raw, ps, gtfs)
                nets[k] = {"network_version": nw["version"], "feeds": {n: params.file_hash(p) for n, p in gtfs.items()}}
                f_k = out_dir / "pt" / f"{per}.offset_{k}.{nw['version']}.parquet"
                if f_k.is_file():
                    click.echo(f"  {s.id} {per} offset {k}: skim already built")
                    frames.append(pd.read_parquet(f_k))
                    continue
                click.echo(f"  {s.id} {per} offset {k}: PT skim on network {nw['version']}")
                f = rn.pt_skim(cfg, raw, ps, nw, per, out_dir / "_work" / f"pt_{per}_offset_{k}", day, log)
                f_k.parent.mkdir(parents=True, exist_ok=True)
                f.to_parquet(f_k, compression="zstd")
                frames.append(f)
            combined[per] = rn.combine_offsets(frames)
            by_offset[per] = frames
            combined[per].to_parquet(out_dir / "pt" / f"{per}.parquet", compression="zstd")
            gen_routes = sorted({op.get("route_id") for op in b["report"]["by_offset"]["0"]["ops"]
                                 if op["type"] in ("add_line", "replace_route")} - {None})
            spread[per] = rn.offset_spread(frames, gen_routes)
        # external rail passengers' onward legs on this scenario's networks (src/lab/gateway.py);
        # skipped, and said so, until `lab demand external` has been run
        from .gateway import DEFAULT_VERSION, build_skims as gateway_skims, demand_points
        if demand_points(cfg, DEFAULT_VERSION) is None:
            click.echo("  gateway legs: no gateway demand yet (`lab demand external`); not skimmed")
        else:
            gateway_skims(cfg, raw, ps, base, day, s.id, periods, DEFAULT_VERSION, lambda m: click.echo(m, err=True))
        # the parent's skims: the baseline's, or a scenario's that has been run
        def parent_skim(per: str) -> pd.DataFrame:
            if s.parent == root_id:
                return pd.read_parquet(network.skim(base, "pt", per))
            pb = _build_scenario(s.parent, lambda m: None)
            f = _scenario_skim_dir(cfg, raw, pb, base["version"]) / "pt" / f"{per}.parquet"
            if not f.is_file():
                raise click.ClickException(f"parent {s.parent} has no {per} skim: run `lab run {s.parent}` first")
            return pd.read_parquet(f)
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            jobs = con.execute("""SELECT d.LSOA21CD, coalesce(b.jobs, 0) jobs FROM skim_dest d
                                  LEFT JOIN nat_bres b USING (LSOA21CD)""").df().set_index("LSOA21CD").jobs
            oa = con.execute("SELECT OA21CD, residents, edge FROM coverage_oa JOIN access_oa USING (OA21CD)").df() \
                .set_index("OA21CD")
        th = [30, 45]
        caveats = [f"{x.id}: {c}" for x in b["chain"] for c in x.caveats]
        card: dict = {"scenario": s.id, "parent": s.parent, "caveats": caveats,
                      "offsets": s.offsets[:len(b["report"]["by_offset"])], "networks": nets,
                      "not_yet_modelled": s.not_yet_modelled, "accessibility": {}, "pt_skim_change": {}, "people": {}}
        for per in periods:
            par = parent_skim(per)
            a_p = rn.access_summary(rn.jobs_within(par, jobs, th), oa.residents, oa.edge, th)
            a_s = rn.access_summary(rn.jobs_within(combined[per], jobs, th), oa.residents, oa.edge, th)
            card["accessibility"][per] = {"scenario": a_s, "parent": a_p, "difference": rn.diff(a_p, a_s)}
            card["pt_skim_change"][per] = rn.skim_diff(par, combined[per])
            centre = raw["centres"][0]["lsoa"]             # the first centre in config: the main one
            if s.parent == root_id:
                par_offs = [network.skim(base, "pt", per)]
            else:
                pb = _build_scenario(s.parent, lambda m: None)
                par_offs = _offset_skims(cfg, raw, ps, pb, _scenario_skim_dir(cfg, raw, pb, base["version"]), per)
            pairs, how = rn.offset_pairs(by_offset[per], par_offs)
            ppl, by_oa = rn.people_over_offsets(pairs, oa.residents, jobs, centre)
            ppl["pairing"] = how
            card["people"][per] = ppl
            by_oa.to_csv(run_dir / f"people_by_oa_{per}.csv", index=False)
        card["offset_spread"] = spread
        # coverage on the scenario's feeds
        ctx = click.get_current_context()
        for cmd in (coverage_stops, coverage_walk, coverage_score):
            ctx.invoke(cmd, scenario=s.id)
        sfx = lambda sid: "" if sid == root_id else "__" + sid.replace("-", "_")  # noqa: E731
        hs = ps["coverage.frequent_headway_min"]
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            def cov_sum(sid: str) -> dict:
                try:
                    c = con.execute(f"SELECT * FROM coverage_oa{sfx(sid)}").df()
                except duckdb.CatalogException:
                    raise click.ClickException(f"no coverage table for {sid}: run `lab run {sid}` first")
                return {f"frequent_{h}": {"residents": int(c.residents[c[f"frequent_{h}"]].sum()),
                                          "jobs": round(float(c.jobs[c[f"frequent_{h}"]].sum()))} for h in hs} | {
                    "residents_in_no_class": int(c.residents[c.are_class == "none"].sum())}
            c_s, c_p = cov_sum(s.id), cov_sum(s.parent)
        card["coverage"] = {"scenario": c_s, "parent": c_p, "difference": rn.diff(c_p, c_s),
                            "walk_speed_kmh_flat": ps["routing.walk_speed_kmh"], "gradient": base["elevation"]}
        card["capacity"] = {"summary": capc["summary"], "caveat": capc["caveat"], "table": "capacity.csv"}
        card["build"] = b["report"]["by_offset"]["0"]["ops"]
        card["notes"] = ["Accessibility: jobs within 30 / 45 min by PT on the pair's median time, walking at "
                         f"{ps['routing.walk_speed_kmh']:g} km/h on flat ground with gradient; PT times are the mean "
                         "over the timetable offsets.",
                         "Demand-dependent metrics (mode shares, boardings, loads, benefits) are not yet built (P5–P6).",
                         "Sketch-planning model: indicative and comparative, not for a business case."]
        (run_dir / "scorecard.json").write_text(json.dumps(card, indent=1, default=str))
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"scorecard": str(run_dir / "scorecard.json"), "skims": str(out_dir),
                     "networks": nets, "chain_hash": b["chain_hash"], "caveats": card["caveats"],
                     "accessibility_difference": {p: card["accessibility"][p]["difference"] for p in periods},
                     "people": card["people"], "offset_spread": card["offset_spread"],
                     "coverage_difference": card["coverage"]["difference"]}
    click.echo(json.dumps(rec["result"], indent=1, default=str))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")
