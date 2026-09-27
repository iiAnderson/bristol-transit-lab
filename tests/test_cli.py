import json

from click.testing import CliRunner

from lab import cli as cli_mod
from lab.cli import cli


def test_help_works():
    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0
    for cmd in ["build-baseline", "scenario", "run", "compare", "calibrate",
                "export-game", "import-game", "params"]:
        assert cmd in r.output
    assert "sketch-planning" in r.output


def test_unbuilt_commands_fail_loudly():
    r = CliRunner().invoke(cli, ["calibrate"])
    assert r.exit_code != 0
    assert "P5" in r.output


def test_run_needs_a_scenario_or_noop():
    assert CliRunner().invoke(cli, ["run"]).exit_code != 0
    assert CliRunner().invoke(cli, ["run", "X", "--noop"]).exit_code != 0


def test_noop_run_writes_valid_run_json(tmp_cfg, monkeypatch):
    monkeypatch.setattr(cli_mod.LabConfig, "load", classmethod(lambda c, root=None: tmp_cfg))
    r = CliRunner().invoke(cli, ["run", "--noop"])
    assert r.exit_code == 0, r.output
    [path] = list(tmp_cfg.runs_dir.glob("*/run.json"))
    rec = json.loads(path.read_text())
    from lab import runrecord
    runrecord.validate(rec)
    assert rec["status"] == "ok" and rec["command"] == "noop"
    assert set(rec["params"]) == {"base.yaml", "costs.yaml"}
