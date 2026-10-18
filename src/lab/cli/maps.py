"""`lab map`: one HTML map for one or more projects."""
from __future__ import annotations

import json
from pathlib import Path

import click
import geopandas as gpd
import yaml

from .. import attribution, params, runrecord
from ..config import LabConfig


def _latest_run(runs: Path, pattern: str, needs: str) -> Path:
    found = sorted(p for p in runs.glob(pattern) if (p / needs).exists())
    if not found:
        raise click.ClickException(f"no run with {needs} under {runs}")
    return found[-1]


def _compare_run(runs: Path, scenario: str, against: str) -> Path:
    """The latest `lab compare` run of `scenario` against `against`."""
    hits = []
    for p in runs.glob("*/compare.json"):
        c = json.loads(p.read_text())
        if c.get("scenario") == scenario and c.get("against") == against:
            hits.append(p.parent)
    if not hits:
        raise click.ClickException(f"no compare run of {scenario} against {against}: run `lab compare {scenario} {against}`")
    return sorted(hits)[-1]


@click.command("map")
@click.argument("scenarios", nargs=-1)
@click.option("--gapmap", "gap_run", default=None, help="A `lab gapmap` run id (default: the latest).")
@click.option("--open", "open_it", is_flag=True, help="Open the page in the browser.")
def map_cmd(scenarios: tuple[str, ...], gap_run: str | None, open_it: bool) -> None:
    """One HTML map of the projects in config/lab.yaml `map.projects` (or just the named scenarios)."""
    from .. import mapview
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    configured = raw["map"]["projects"]
    chosen = [p for p in configured if not scenarios or p["scenario"] in scenarios]
    unknown = set(scenarios) - {p["scenario"] for p in configured}
    if unknown:
        raise click.ClickException(f"not in config map.projects: {sorted(unknown)}")
    gdir = cfg.runs_dir / gap_run if gap_run else _latest_run(cfg.runs_dir, "*-gapmap-*", "gapmap_od.parquet")
    scn = cfg.root / "scenarios"
    projects, inputs = [], []
    for spec in chosen:
        cdir = _compare_run(cfg.runs_dir, spec["scenario"], spec["against"])
        comp = json.loads((cdir / "compare.json").read_text())
        skims_s, skims_a = Path(comp["skims"]["scenario"]), Path(comp["skims"]["against"])
        routes = mapview.scheme_routes(scn, spec["scenario"], spec["against"])
        click.echo(f"{spec['title']}: {cdir.name}")
        attr = attribution.compute(cfg.lab_db, skims_s, skims_a, routes, float(comp["threshold_min"]))
        g = gpd.read_parquet(cdir / "change_oa.parquet")
        saved = -g.jobs_time_change_min_AM.fillna(0)
        sel = g[(saved >= mapview.MIN_SHOWN_MIN) | g.OA21CD.isin(attr.loc[attr.jobs_new_within > 0, "OA21CD"])]
        sel = sel.loc[saved[sel.index].sort_values(ascending=False).index[:mapview.CLICKABLE]]
        reach = attribution.reach(cfg.lab_db, skims_s, skims_a, list(sel.OA21CD), float(comp["threshold_min"]))
        h = params.file_hash(cdir / "change_oa.parquet")
        projects.append(mapview.Project(
            id=spec["scenario"], title=spec["title"], against_title=spec.get("against_title", spec["against"]),
            colour=spec["colour"], compare_dir=cdir, c=comp, g=g, attr=attr, reach=reach,
            files=mapview.scheme_files(scn, spec["scenario"], spec["against"]), data_hash=h[:12]))
        inputs += [{"name": f"{cdir.name}/change_oa.parquet", "sha256": h},
                   {"name": f"{cdir.name}/compare.json", "sha256": params.file_hash(cdir / "compare.json")}]
    inputs.append({"name": f"{gdir.name}/gapmap_od.parquet", "sha256": params.file_hash(gdir / "gapmap_od.parquet")})
    rec = runrecord.build(cfg, command="map", inputs=inputs)
    out_dir = runrecord.write(cfg, rec).parent
    try:
        zones = gpd.read_file(cfg.root / "data" / "raw" / "ons_geo" / "lsoa21_bgc_internal.geojson")
        page = mapview.build(projects, gdir, cfg.extent, zones, attribution.zone_jobs(cfg.lab_db))
        (out_dir / "map.html").write_text(page)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"map": str(out_dir / "map.html"), "projects": [p.id for p in projects], "gapmap_run": gdir.name}
    runrecord.finish(cfg, rec, "ok")
    click.echo(f"wrote {out_dir / 'map.html'} ({(out_dir / 'map.html').stat().st_size / 1e6:.1f} MB)")
    if open_it:
        import webbrowser
        webbrowser.open((out_dir / "map.html").as_uri())
