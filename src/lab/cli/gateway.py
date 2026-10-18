"""`lab gateway`: PT skims for the external rail legs (src/lab/gateway.py)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import click

from .. import params
from ..config import LabConfig

DEFAULT_VERSION = "x1-central"


def _setup():
    import yaml
    from .. import network
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    return cfg, raw, ps, day, network.settings(cfg, raw, ps)


def demand_points(cfg, version: str):
    """Gateways and zones for ``version``, and their key; None if the table is not built."""
    import duckdb
    from .. import gateway as gw
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        try:
            g, z = gw.points(con, version)
        except duckdb.CatalogException:
            return None
    return g, z, gw.inputs_key(g, z)


def demand_trips(cfg, version: str):
    import duckdb
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        return con.execute("""SELECT dir, gateway, zone, trips FROM demand_gateway
                              WHERE demand_version=?""", [version]).df()


def gateway_files(cfg, raw, ps, base, b, per: str, key: str, root_id: str) -> list[Path]:
    """The gateway-leg skim files of one scenario (``b`` from `_build_scenario`) or of the
    baseline (``b`` None) for one period: one per timetable offset, or the baseline's one."""
    from .. import run as rn
    if b is None:
        return [base["skims"] / "gateway" / f"{per}.{key}.parquet"]
    from .scenario import _scenario_skim_dir
    d = _scenario_skim_dir(cfg, raw, b, base["version"]) / "gateway"
    out = []
    for k in b["report"]["by_offset"]:
        gtfs = {f.stem: f for f in sorted((b["dir"] / f"offset_{k}").glob("*.zip"))}
        out.append(d / f"{per}.offset_{k}.{rn.offset_network(cfg, raw, ps, gtfs)['version']}.{key}.parquet")
    return out


def build_skims(cfg, raw, ps, base, day, scenario_id: str | None, periods: list[str], version: str,
                log=click.echo) -> list[Path]:
    """Build the gateway-leg skims that are missing, for the baseline (``scenario_id``
    None) or a scenario, on each timetable offset's own network."""
    from .. import gateway as gw, run as rn
    from .scenario import _build_scenario
    pts = demand_points(cfg, version)
    if pts is None:
        raise click.ClickException(f"no gateway demand ({version}): run `lab demand external` first")
    g, z, key = pts
    root_id = raw["baseline"]["scenario"]
    b = None if scenario_id in (None, root_id) else _build_scenario(scenario_id, lambda m: None)
    label = root_id if b is None else b["scenario"].id
    if b is None:
        gtfs = {Path(raw[x]["out"]).stem: cfg.root / raw[x]["out"] for x in ("bus", "rail")}
        nets = [("", base | {"gtfs": gtfs})]
        work_root = base["skims"] / "_work"
    else:
        from .scenario import _scenario_skim_dir
        nets = []
        for k in b["report"]["by_offset"]:
            gt = {f.stem: f for f in sorted((b["dir"] / f"offset_{k}").glob("*.zip"))}
            nets.append((f"_offset_{k}", rn.offset_network(cfg, raw, ps, gt)))
        work_root = _scenario_skim_dir(cfg, raw, b, base["version"]) / "_work"
    built = []
    for per in periods:
        files = gateway_files(cfg, raw, ps, base, b, per, key, root_id)
        for (suffix, nw), f in zip(nets, files):
            if f.is_file():
                log(f"  {label} {per}{suffix}: gateway skim already built")
                continue
            log(f"  {label} {per}{suffix}: gateway-leg PT skim ({len(g)} gateways, {len(z)} zones) on {nw['version']}")
            s = gw.leg_skim(cfg, raw, ps, nw, per, work_root / f"gw_{per}{suffix}_{key}", day,
                            lambda m: click.echo(f"  {m}", err=True), g, z)
            f.parent.mkdir(parents=True, exist_ok=True)
            s.to_parquet(f, compression="zstd")
            built.append(f)
    return built


@click.group()
def gateway() -> None:
    """External rail gateways: skims for the legs between gateway stations and zones."""


@gateway.command("skims")
@click.argument("scenario_id", required=False)
@click.option("--periods", default="AM,IP", show_default=True)
@click.option("--version", default=DEFAULT_VERSION, show_default=True, help="gateway demand version")
def gateway_skims(scenario_id: str | None, periods: str, version: str) -> None:
    """Build the gateway-leg PT skims for a scenario (or, without one, the baseline).

    `lab run` does this for every new run; use this for scenarios run before. Skims already
    built are kept. `lab compare` needs them for both sides of a comparison."""
    cfg, raw, ps, day, base = _setup()
    built = build_skims(cfg, raw, ps, base, day, scenario_id, [p for p in periods.split(",") if p], version)
    click.echo(f"built {len(built)} skim file(s)")
