"""`lab run`."""
from __future__ import annotations


import click

from .. import runrecord
from ..config import LabConfig
from .scenario import _run_scenario

@click.command()
@click.argument("scenario_id", required=False)
@click.option("--demand", default=None, help="Demand version.")
@click.option("--periods", default="AM,IP", show_default=True)
@click.option("--noop", is_flag=True, help="Write a run record and do nothing else.")
def run(scenario_id: str | None, demand: str | None, periods: str, noop: bool) -> None:
    """Run a scenario through the model."""
    if not noop:
        if scenario_id is None:
            raise click.UsageError("give a SCENARIO_ID, or --noop")
        _run_scenario(scenario_id, [p for p in periods.split(",") if p])
        return
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
