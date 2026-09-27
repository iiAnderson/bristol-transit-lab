"""
Run records (CLAUDE.md rule 7).

Every run writes ``runs/<run_id>/run.json`` holding what is needed to reproduce it:
the lab's git SHA, scenario hash, demand version, parameter file hashes, input dataset
versions, the upstream pin, and the software environment. ``validate`` checks the shape
and fails loudly; a record that does not validate is not written.
"""
from __future__ import annotations

import datetime as dt
import importlib.metadata as md
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

from . import __version__, params, upstream
from .config import LabConfig

SCHEMA_VERSION = 1
STATUSES = {"running", "ok", "failed"}


class RunRecordError(ValueError):
    pass


def _git_state(root: Path) -> dict:
    def git(*a):
        r = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    sha = git("rev-parse", "HEAD")          # None before the first commit
    status = git("status", "--porcelain")
    return {"sha": sha, "dirty": True if sha is None else bool(status)}


def _java() -> dict | None:
    """The JVM r5py will use (via JAVA_HOME), read without starting it."""
    java_home = os.environ.get("JAVA_HOME")
    if not java_home:
        return None      # recorded, not fatal, for a no-op; P2's skims will require it
    try:
        r = subprocess.run([str(Path(java_home) / "bin" / "java"), "-version"],
                           capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return {"java_home": java_home, "version": None}
    return {"java_home": java_home, "version": r.stderr.splitlines()[0]}


def _env() -> dict:
    pkgs = {}
    for p in ("r5py", "duckdb", "geopandas", "pandas", "numpy", "pyarrow", "shapely"):
        try:
            pkgs[p] = md.version(p)
        except md.PackageNotFoundError:
            pkgs[p] = None
    return {
        "lab_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": pkgs,
        "java": _java(),
    }


def new_run_id(label: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{label}-{uuid.uuid4().hex[:6]}"


def build(cfg: LabConfig, *, command: str, scenario: dict | None = None,
          demand_version: str | None = None, inputs: list[dict] | None = None) -> dict:
    param_files = sorted((cfg.root / "params").glob("*.yaml"))
    if not param_files:
        raise RunRecordError("no params/*.yaml found")
    param_info = {}
    for f in param_files:
        ps = params.load(f)            # validates tags; raises on an untagged value
        param_info[f.name] = {
            "sha256": params.file_hash(f),
            "n_params": len(ps),
            "n_placeholder": len(params.placeholders(ps)),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": new_run_id(scenario["id"] if scenario else command),
        "command": command,
        "status": "running",
        "started_at": dt.datetime.now().astimezone().isoformat(),
        "finished_at": None,
        "git": _git_state(cfg.root),
        "scenario": scenario,               # {"id", "spec_hash"} once P3 exists
        "demand_version": demand_version,
        "params": param_info,
        "inputs": inputs or [],             # [{"name", "version", "sha256"|"url"}]
        "upstream": upstream.pin(cfg),
        "env": _env(),
    }


def _req(rec: dict, key: str, types, path: str = "") -> None:
    if key not in rec:
        raise RunRecordError(f"run.json missing `{path}{key}`")
    if not isinstance(rec[key], types):
        raise RunRecordError(f"run.json `{path}{key}` is {type(rec[key]).__name__}, "
                             f"expected {types}")


def validate(rec: dict) -> None:
    for k, t in [("schema_version", int), ("run_id", str), ("command", str),
                 ("status", str), ("started_at", str), ("git", dict),
                 ("params", dict), ("inputs", list), ("upstream", dict), ("env", dict)]:
        _req(rec, k, t)
    if rec["status"] not in STATUSES:
        raise RunRecordError(f"status {rec['status']!r} not in {sorted(STATUSES)}")
    if rec["status"] != "running":
        _req(rec, "finished_at", str)
    g = rec["git"]
    _req(g, "dirty", bool, "git.")
    if g.get("sha") is None and not g["dirty"]:
        raise RunRecordError("git.sha is null but the tree is marked clean")
    up = rec["upstream"]
    for k, t in [("db_path", str), ("db_sha256", str), ("package", str),
                 ("stage_log", list)]:
        _req(up, k, t, "upstream.")
    if len(up["db_sha256"]) != 64:
        raise RunRecordError("upstream.db_sha256 is not a sha256 hex digest")
    if not up["stage_log"]:
        raise RunRecordError("upstream.stage_log is empty")
    for name, p in rec["params"].items():
        _req(p, "sha256", str, f"params.{name}.")
    if rec["scenario"] is not None:
        _req(rec["scenario"], "id", str, "scenario.")
        _req(rec["scenario"], "spec_hash", str, "scenario.")


def write(cfg: LabConfig, rec: dict) -> Path:
    validate(rec)
    out = cfg.runs_dir / rec["run_id"]
    out.mkdir(parents=True, exist_ok=False)
    path = out / "run.json"
    path.write_text(json.dumps(rec, indent=2, sort_keys=False) + "\n")
    return path


def finish(cfg: LabConfig, rec: dict, status: str = "ok") -> Path:
    rec["status"] = status
    rec["finished_at"] = dt.datetime.now().astimezone().isoformat()
    validate(rec)
    path = cfg.runs_dir / rec["run_id"] / "run.json"
    path.write_text(json.dumps(rec, indent=2) + "\n")
    return path
