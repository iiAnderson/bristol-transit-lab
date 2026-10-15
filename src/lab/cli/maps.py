"""`lab map`: one HTML map for a compare run."""
from __future__ import annotations

from pathlib import Path

import click

from .. import params, runrecord
from ..config import LabConfig


def _latest(runs: Path, pattern: str, needs: str) -> Path:
    found = sorted(p for p in runs.glob(pattern) if (p / needs).exists())
    if not found:
        raise click.ClickException(f"no run with {needs} under {runs}")
    return found[-1]


@click.command("map")
@click.argument("compare_run", required=False)
@click.option("--gapmap", "gap_run", default=None, help="A `lab gapmap` run id (default: the latest).")
@click.option("--show-gap", is_flag=True, help="Show the gap map layer when the page opens.")
@click.option("--open", "open_it", is_flag=True, help="Open the page in the browser.")
def map_cmd(compare_run: str | None, gap_run: str | None, show_gap: bool, open_it: bool) -> None:
    """Write one self-contained HTML map for a `lab compare` run (default: the latest)."""
    from .. import mapview
    cfg = LabConfig.load()
    cdir = cfg.runs_dir / compare_run if compare_run else _latest(cfg.runs_dir, "*", "change_oa.parquet")
    if not (cdir / "change_oa.parquet").exists():
        raise click.ClickException(f"{cdir} is not a `lab compare` run (no change_oa.parquet)")
    gdir = cfg.runs_dir / gap_run if gap_run else _latest(cfg.runs_dir, "*-gapmap-*", "gapmap_od.parquet")
    data_hash = params.file_hash(cdir / "change_oa.parquet")[:12]
    rec = runrecord.build(cfg, command="map", inputs=[
        {"name": f"{cdir.name}/change_oa.parquet", "sha256": params.file_hash(cdir / "change_oa.parquet")},
        {"name": f"{cdir.name}/compare.json", "sha256": params.file_hash(cdir / "compare.json")},
        {"name": f"{gdir.name}/gapmap_od.parquet", "sha256": params.file_hash(gdir / "gapmap_od.parquet")}])
    out_dir = runrecord.write(cfg, rec).parent
    try:
        page = mapview.build(cdir, gdir, cfg.root / "scenarios", data_hash, cfg.extent, show_gap)
        (out_dir / "map.html").write_text(page)
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"map": str(out_dir / "map.html"), "compare_run": cdir.name, "gapmap_run": gdir.name}
    runrecord.finish(cfg, rec, "ok")
    click.echo(f"wrote {out_dir / 'map.html'} ({(out_dir / 'map.html').stat().st_size / 1e6:.1f} MB)")
    if open_it:
        import webbrowser
        webbrowser.open(out_dir.joinpath("map.html").as_uri())
