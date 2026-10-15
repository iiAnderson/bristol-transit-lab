"""Accessibility, comparison, gap map, spot checks, parameters."""
from __future__ import annotations

from pathlib import Path

import click

from .. import params, runrecord
from ..config import LabConfig
from .congestion import _anpr_inputs
from .scenario import _build_scenario, _offset_skims, _scenario_cost, _scenario_skim_dir

@click.command("access")
@click.option("--thresholds", default="30,45", show_default=True)
def access_cmd(thresholds: str) -> None:
    """C2: cumulative BRES jobs reachable per OA (PT AM p50; walk for context)."""
    import duckdb
    import yaml
    from .. import network
    cfg = LabConfig.load()
    nw = network.settings(cfg, yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text()),
                          {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")})
    f_pt, f_wk = network.skim(nw, "pt", "AM"), network.skim(nw, "walk")
    import pyarrow.parquet as pq
    # walk times: R5's truncated minutes plus the half-minute correction where the skim has it
    wk_col = "travel_time_corrected" if "travel_time_corrected" in pq.read_schema(f_wk).names else "travel_time"
    th = [int(x) for x in thresholds.split(",")]
    x0, y0, x1, y1 = cfg.extent
    rec = runrecord.build(cfg, command="access", inputs=[
        {"name": str(f), "sha256": params.file_hash(f)} for f in (f_pt, f_wk)]
        + [{"name": "network_version", "version": nw["version"]}])
    runrecord.write(cfg, rec)
    try:
        with duckdb.connect(str(cfg.lab_db)) as con:
            cols = ", ".join(
                f"sum(j.jobs) FILTER (WHERE s.t <= {t}) AS jobs_{t}" for t in th)
            con.execute(f"""CREATE OR REPLACE TEMP VIEW pt AS
                SELECT from_id, to_id, CASE WHEN NOT unreachable THEN p50 END t
                FROM read_parquet('{f_pt}')""")
            con.execute(f"""CREATE OR REPLACE TEMP VIEW wk AS
                SELECT from_id, to_id, {wk_col} t FROM read_parquet('{f_wk}')""")
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


@click.command("compare")
@click.argument("scenario_id")
@click.argument("against")
@click.option("--periods", default="AM,IP", show_default=True)
@click.option("--unrelated", is_flag=True,
              help="Allow two scenarios neither of which is built on the other (the difference then mixes both).")
def compare_cmd(scenario_id: str, against: str, periods: str, unrelated: bool) -> None:
    """Compare a scenario with another scenario or the baseline: who gains and loses,
    accessibility, coverage and cost, and what the figures rest on. Both must have been
    run (`lab run`)."""
    import json
    import duckdb
    import geopandas as gpd
    import pandas as pd
    import yaml
    from .. import compare as cmp_, network, run as rn
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    plist = params.load(cfg.root / "params" / "base.yaml")
    ps = {p.path: p.value for p in plist}
    base = network.settings(cfg, raw, ps)
    root_id = raw["baseline"]["scenario"]
    pers = [p for p in periods.split(",") if p]
    if scenario_id == root_id:
        raise click.ClickException(f"{root_id} is the baseline: give the scenario first, `lab compare <scenario> {root_id}`")
    rec = runrecord.build(cfg, command="compare", scenario={"id": scenario_id, "spec_hash": "pending"})
    run_dir = runrecord.write(cfg, rec).parent

    def side(sid: str) -> dict:
        if sid == root_id:
            return {"id": sid, "b": None, "dir": None, "caveats": [], "later": [], "table": "coverage_oa",
                    "file": lambda per: network.skim(base, "pt", per)}
        b = _build_scenario(sid, lambda m: None)
        d = _scenario_skim_dir(cfg, raw, b, base["version"]) / "pt"
        return {"id": sid, "b": b, "dir": d, "caveats": [f"{x.id}: {c}" for x in b["chain"] for c in x.caveats],
                "later": b["scenario"].not_yet_modelled, "table": "coverage_oa__" + sid.replace("-", "_"),
                "file": lambda per: d / f"{per}.parquet"}
    try:
        A, B = side(scenario_id), side(against)
        chain_a = [x.id for x in A["b"]["chain"]]
        chain_b = [x.id for x in B["b"]["chain"]] if B["b"] else [root_id]
        related = B["id"] in chain_a or A["id"] in chain_b
        if not related and not unrelated:
            raise click.ClickException(
                f"{A['id']} (built on {' → '.join(chain_a)}) and {B['id']} (built on {' → '.join(chain_b)}) are not "
                "ancestor and descendant: the difference would mix both scenarios' changes. Re-point one at the "
                "other, or pass --unrelated")
        rec["scenario"] = {"id": A["id"], "spec_hash": A["b"]["scenario"].spec_hash}
        for x in (A, B):
            missing = [p for p in pers if not Path(x["file"](p)).is_file()]
            if missing:
                raise click.ClickException(f"{x['id']} has no PT skim for {', '.join(missing)}: run `lab run {x['id']}` first")
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            jobs = con.execute("""SELECT d.LSOA21CD, coalesce(b.jobs, 0) jobs FROM skim_dest d
                                  LEFT JOIN nat_bres b USING (LSOA21CD)""").df().set_index("LSOA21CD").jobs
            oa = con.execute("""SELECT OA21CD, residents, edge, lon, lat FROM coverage_oa
                                JOIN access_oa USING (OA21CD)""").df().set_index("OA21CD")
            cov = {}
            for x in (A, B):
                try:
                    cov[x["id"]] = con.execute(f"SELECT * FROM {x['table']}").df().set_index("OA21CD")
                except duckdb.CatalogException:
                    raise click.ClickException(f"no coverage table for {x['id']}: run `lab run {x['id']}` first")
        th = list(ps["connectivity.job_thresholds_min"])
        beta, rt_, cper = ps["connectivity.decay_beta_per_min"], ps["connectivity.pt_car_threshold_min"], ps["connectivity.car_period"]
        car_jobs = rn.car_jobs_within(pd.read_parquet(network.skim(base, "car", cper), columns=["from_id", "to_id", "ivt_min"]),
                                      jobs, rt_)
        centre = raw["centres"][0]["lsoa"]
        out_oa = oa[["residents", "lon", "lat"]].copy()
        conn, people, pairs = {}, {}, {}
        for per in pers:
            fa, fb = pd.read_parquet(A["file"](per)), pd.read_parquet(B["file"](per))
            sa, oa_a = rn.connectivity(fa, car_jobs, jobs, oa.residents, oa.edge, th, beta, rt_)
            sb, oa_b = rn.connectivity(fb, car_jobs, jobs, oa.residents, oa.edge, th, beta, rt_)
            conn[per] = {"scenario": sa, "against": sb, "difference": rn.diff(sb, sa)}
            offs_a = _offset_skims(cfg, raw, ps, A["b"], A["dir"].parent, per)
            offs_b = _offset_skims(cfg, raw, ps, B["b"], B["dir"].parent, per) if B["b"] else [B["file"](per)]
            try:
                op, how = rn.offset_pairs(offs_a, offs_b)
            except ValueError as e:
                raise click.ClickException(f"{A['id']} against {B['id']}: {e}")
            ppl, by_oa = rn.people_over_offsets(op, oa.residents, jobs, centre)
            ppl["pairing"] = how
            people[per] = ppl
            pairs[per] = rn.skim_diff(fb, fa) if "p50_min_offsets" in fa else None
            by_oa = by_oa.set_index("OA21CD")
            out_oa[f"centre_change_min_{per}"] = by_oa.centre_change_min
            out_oa[f"jobs_time_change_min_{per}"] = by_oa.jobs_time_change_min
            for col in oa_a.columns:
                if col != "pt_car_ratio":
                    out_oa[f"{col}_{per}_change"] = oa_a[col] - oa_b[col]
            out_oa[f"pt_car_ratio_{per}"] = oa_a.get("pt_car_ratio")
        hs = ps["coverage.frequent_headway_min"]

        def cov_sum(c: pd.DataFrame) -> dict:
            return {f"frequent_{h}": {"residents": int(c.residents[c[f"frequent_{h}"]].sum()),
                                      "jobs": round(float(c.jobs[c[f"frequent_{h}"]].sum()))} for h in hs} | {
                "residents_without_frequent_service": int(c.residents[~c[f"frequent_{max(hs)}"]].sum()),
                "residents_in_no_class": int(c.residents[c.are_class == "none"].sum())}
        ca, cb = cov_sum(cov[A["id"]]), cov_sum(cov[B["id"]])
        for h in hs:
            out_oa[f"frequent_{h}_change"] = (cov[A["id"]][f"frequent_{h}"].astype(int)
                                              - cov[B["id"]][f"frequent_{h}"].astype(int)).reindex(out_oa.index)
        cost = {x["id"]: (_scenario_cost(x["b"]) if x["b"] else None) for x in (A, B)}
        chain_ids = [x.id for x in A["b"]["chain"]]
        cost_note = None
        if A["b"]["scenario"].parent != B["id"]:
            cost_note = (f"Costs are each scenario's own ops against its own parent, not {A['id']} less {B['id']}: "
                         + (f"{B['id']} is an ancestor of {A['id']} but not its parent, so the ops between them are not in "
                            f"{A['id']}'s cost." if B["id"] in chain_ids else f"{B['id']} is not an ancestor of {A['id']}."))
        used: dict = {}
        for k in cost.values():
            for tag, paths in ((k or {}).get("unit_costs_used_by_tag") or {}).items():
                used.setdefault(tag, set()).update(paths)
        c = {"scenario": A["id"], "against": B["id"], "periods": pers, "modelled_date": str(raw["modelled_date"]),
             "walk_speed_kmh": ps["routing.walk_speed_kmh"], "gradient": base["elevation"], "threshold_min": 2.0,
             "decay_beta": beta, "car_period": cper,
             "caveats": {A["id"]: A["caveats"], B["id"]: B["caveats"]},
             "people": people, "connectivity": conn, "pt_skim_change": pairs,
             "coverage": {"scenario": ca, "against": cb, "difference": rn.diff(cb, ca)},
             "cost": cost, "cost_note": cost_note, "inherited": raw["inherited"],
             "not_yet_modelled": {A["id"]: A["later"], B["id"]: B["later"]},
             "params_behind": cmp_.params_behind(plist, used),
             "skims": {"scenario": str(A["dir"]), "against": str(B["dir"] or network.skim(base, "pt", pers[0]).parent)}}
        (run_dir / "compare.json").write_text(json.dumps(c, indent=1, default=str))
        (run_dir / "compare.md").write_text(cmp_.markdown(c))
        g = gpd.GeoDataFrame(out_oa.drop(columns=["lon", "lat"]).reset_index(),
                             geometry=gpd.points_from_xy(out_oa.lon, out_oa.lat), crs="EPSG:4326")
        g.to_parquet(run_dir / "change_oa.parquet")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"report": str(run_dir / "compare.md"), "data": str(run_dir / "compare.json"),
                     "change_map": str(run_dir / "change_oa.parquet"),
                     "placeholders": c["params_behind"].get("PLACEHOLDER", [])}
    click.echo((run_dir / "compare.md").read_text())
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@click.command("compare-dft")
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


@click.command("gapmap")
@click.option("--car-period", default="AMPH", show_default=True)
@click.option("--pt-period", default="AM", show_default=True,
              help="PT AM skim is the 08:00–09:00 window (skims.window_start).")
def gapmap_cmd(car_period: str, pt_period: str) -> None:
    """C5: gap map — PT (≥ 1 ride) vs car per internal HBW LSOA pair beyond
    gapmap.min_distance_km: door-to-door time difference (published default), GC
    difference and GC ratio (generalised minutes); both ends of the car parking/access
    range; by distance band; robustness of the rankings."""
    import json
    import duckdb
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import yaml
    from .. import gapmap as gm
    from ..congestion import annotate as an
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    from .. import network
    nw = network.settings(cfg, raw, ps)
    geo = cfg.root / "data" / "raw" / "ons_geo"
    ruc = cfg.root / "data/raw/ons/ruc21_lsoa_ew.csv"
    pt, car = network.skim(nw, "pt", pt_period), network.skim(nw, "car", car_period)
    park = {k: ps[f"car.parking_search_min.{k}"] for k in ("centre", "urban", "rural")}
    walk = {k: ps[f"car.access_walk_min.{k}"] for k in ("centre", "urban", "rural")}
    min_km = ps["gapmap.min_distance_km"]
    # the caveat on the label comes from the latest calibration's held-out ANPR figure
    cals = sorted(cfg.runs_dir.glob("*-congestion-calibrate-*/calibration.json"))
    anpr_val = json.loads(cals[-1].read_text())["anpr"][f"hybrid|{car_period}|validation"]["median_ratio"]
    label = gm.LABEL.format(min_km=min_km, anpr=anpr_val, anpr_area=raw["anpr"]["labels"]["validation_area"],
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
            for by in ("tdiff", "diff", "ratio"):
                gm.top_pairs(g, v, by).to_csv(out / f"gapmap_top50_{by}_{v}.csv", index=False)
        z = gpd.read_file(geo / "lsoa21_bgc_internal.geojson")[["LSOA21CD", "geometry"]]
        gz = z.merge(orig, left_on="LSOA21CD", right_on="o_zone", how="left").drop(columns="o_zone")
        gz.to_file(out / "gapmap_origin.geojson", driver="GeoJSON")
        fig, ax = plt.subplots(3, 2, figsize=(16, 15.5))
        panels = (("tdiff", "PT − car door-to-door time (real minutes)", 0, 60, "flow-weighted mean, minutes"),
                  ("diff", "PT − car GC (generalised minutes)", 0, 120, "flow-weighted mean, generalised minutes"),
                  ("ratio", "PT ÷ car GC (generalised minutes)", 1, 5, "Σ PT GC ÷ Σ car GC"))
        for i, (var, title, vmin, vmax, lab) in enumerate(panels):
            for jx, (v, vt) in enumerate((("high", "car parking/access: high"),
                                          ("low", "car parking/access: low (none)"))):
                a = ax[i, jx]
                gz.plot(column=f"{var}_{v}", ax=a, cmap="RdYlGn_r", vmin=vmin, vmax=vmax,
                        legend=True, missing_kwds={"color": "lightgrey"}, linewidth=0,
                        legend_kwds={"label": lab, "shrink": 0.7})
                a.set_title(f"{title} — {vt}", fontsize=10)
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
    tcols = ["band", "trips", "mean_pt_time_min", "mean_car_time_min_high", "mean_time_diff_min_high",
             "median_time_diff_min_high", "mean_time_diff_min_low", "median_time_diff_min_low"]
    click.echo(f"door-to-door time difference by band:\n{new[tcols].round(1).to_string(index=False)}")
    click.echo("by destination area type (time difference): " + by_area[
        ["area_type", "mean_time_diff_min_high", "mean_time_diff_min_low"]].round(1).to_string(index=False))
    click.echo(f"by destination area type:\n{by_area.round(3).to_string(index=False)}")
    click.echo(f"robustness: {rec['result']['robustness_low_vs_high']}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@click.command("spotchecks")
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
    from r5py import ElevationCostFunction
    from .. import network
    from ..supply import dem
    nw = network.settings(cfg, raw, ps)
    if walk_kmh is None:
        ps["routing.walk_speed_kmh"] = nw["walk_kmh"]
    gtfs = [str(cfg.root / raw["bus"]["out"]), str(cfg.root / raw["rail"]["out"])]
    net = (TransportNetwork(str(cfg.root / raw["osm"]["clip"]), gtfs) if not nw["elevation"] else
           TransportNetwork(str(cfg.root / raw["osm"]["clip"]), gtfs,
                            elevation_model=[dem.for_function(nw["tif"], nw["elevation"])],
                            elevation_cost_function=ElevationCostFunction(nw["elevation"])))
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
                     "network_version": nw["version"],
                     "walk_speed_overridden": walk_kmh is not None}
    click.echo(out_df[["id", "slot", "origin", "destination", "model_best_min", "model_p50_min"]]
               .to_string(index=False))
    click.echo(f"wrote {out}")
    runrecord.finish(cfg, rec, "ok")


@click.command("spotchecks-eval")
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
    # every failing AM / IP row carries a recorded class and reason (config)
    cl = pd.read_csv(cfg.root / "config" / "spotchecks_pt_failures.csv")
    bad = set(cl["class"]) - {"model_error", "comparison_artefact"}
    if bad:
        raise click.ClickException(f"unknown failure class {sorted(bad)}")
    x = x.merge(cl.rename(columns={"class": "failure_class", "reason": "failure_reason"}),
                on=["id", "slot"], how="left")
    x.loc[x["pass"], ["failure_class", "failure_reason"]] = None
    missing = x[~x["pass"] & (x["slot"] != "EVE") & x["failure_class"].isna()]
    if len(missing):
        raise click.ClickException(
            "failing rows without a classification in config/spotchecks_pt_failures.csv: "
            f"{missing[['id', 'slot']].to_dict('records')}")
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
                         ["id", "slot", "origin", "destination", "failure_class",
                          "failure_reason"]].to_dict("records"),
                     "failures_by_class": x[~x["pass"] & (x["slot"] != "EVE")]
                     .groupby(["slot", "failure_class"]).size().unstack(fill_value=0)
                     .reset_index().to_dict("records"),
                     "stated_duration_differs_from_clock_times":
                         x[x["duration_mismatch"]][["id", "slot", "observed_elapsed_min"]]
                         .to_dict("records")}
    click.echo(summ.to_string())
    click.echo(f"acceptance (>= 16 of 20): {acc}")
    click.echo(f"failures by class: {rec['result']['failures_by_class']}")
    click.echo(f"wrote {out}")
    runrecord.finish(cfg, rec, "ok")


@click.command("spotchecks-car")
@click.option("--n", "n_links", default=20, show_default=True)
def spotchecks_car(n_links: int) -> None:
    """Car spot checks from held-out ANPR routes (validation half): the n links with
    observations whose routed length best matches the ANPR path. Modelled time = OSRM
    route on the calibrated period speeds (what the car skims use, turn penalties
    included) vs observed, IP and the AM peak hour. Pass: ≥ 16 of 20 within ±15%."""
    import pandas as pd
    import yaml
    from ..congestion import validate as va
    from ..supply import osrm
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
    rows, allv = [], []
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
            # diagnostic: every validation link with observations and a matching route
            a = m[m["link_id"].isin(set(val["link_id"]))
                  & ((m["route_m"] / m["link_m"] - 1).abs() <= 0.2)].merge(
                obs[obs["period"] == per][["link_id", "obs_s", "area"]], on="link_id")
            allv.append(a.assign(period=per))
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
    # does link-level error fall with link length? (validation half, OSRM routes)
    av = pd.concat(allv)
    av["ratio"] = av["mod_s"] / av["obs_s"]
    av["km_band"] = pd.cut(av["link_m"] / 1000, [0, 1, 2, 4, 100],
                           labels=["< 1 km", "1–2 km", "2–4 km", "> 4 km"])
    bands = av.groupby(["period", "km_band"], observed=True).agg(
        links=("ratio", "size"), median_ratio=("ratio", "median"),
        median_abs_err=("ratio", lambda r: (r - 1).abs().median()),
        within_15pct=("ratio", lambda r: ((r - 1).abs() <= 0.15).mean())).round(3).reset_index()
    av.to_csv(out.parent / "car_anpr_validation_links.csv", index=False)
    click.echo("validation half by ANPR link length:")
    click.echo(bands.to_string(index=False))
    rec["result"] = {"csv": str(out), "links": len(pick),
                     "by_link_length": bands.to_dict("records"),
                     "summary": summ.round(3).reset_index().to_dict("records"),
                     "pass_rule": ">= 16 of 20 within ±15%",
                     "passes": bool((summ["passing"] >= 16).all())}
    click.echo(summ.round(3).to_string())
    click.echo(f"wrote {out}")
    runrecord.finish(cfg, rec, "ok")


@click.command("params")
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


@click.command("network-change")
@click.argument("version_a")
@click.argument("version_b")
def network_change(version_a: str, version_b: str) -> None:
    """Change report between two network versions of the baseline: skims pair by pair."""
    import json
    import yaml
    from .. import network
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    root = cfg.root / raw["baseline"]["skims"] / raw["baseline"]["scenario"]
    for v in (version_a, version_b):
        if not (root / v).is_dir():
            raise click.ClickException(f"no skims for network version {v} under {root}")
    rec = runrecord.build(cfg, command="network-change", inputs=[
        {"name": "network_version_a", "version": version_a},
        {"name": "network_version_b", "version": version_b}])
    runrecord.write(cfg, rec)
    rec["result"] = network.skim_change(root / version_a, root / version_b)
    click.echo(json.dumps(rec["result"], indent=1))
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")
