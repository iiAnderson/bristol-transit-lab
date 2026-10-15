"""
`lab` — the command line. One module per command group.

    lab scenario validate|build|capacity|cost|draw-rail|new
    lab run <scenario> [--periods AM,IP] | --noop
    lab compare <scenario> <against>
    lab access | gapmap | compare-dft | spotchecks* | params | network-change
    lab demand | landuse | supply | skims | coverage | congestion (frozen) | spike

Commands not yet built fail with a non-zero exit naming the phase that builds them.
"""
from __future__ import annotations

import sys

import click

from .. import FIDELITY_DISCLAIMER, __version__
from ..config import LabConfig  # noqa: F401  (tests patch `lab.cli.LabConfig`)
from ..coverage_cli import coverage
from . import congestion, demand, maps, reports, run, scenario, skims, spikes, supply


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


# group objects and standalone commands, by the names click gives them
cli.add_command(scenario.scenario)
cli.add_command(run.run)
for _g in (demand.demand, demand.landuse, supply.supply, congestion.congestion,
           spikes.spike, skims.skims, coverage):
    cli.add_command(_g)
for _mod in (reports, maps):
    for _c in vars(_mod).values():
        if isinstance(_c, click.Command):
            cli.add_command(_c)


def main() -> None:
    try:
        cli(standalone_mode=True)
    except Exception as exc:  # noqa: BLE001 — fail loudly with the real message
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
