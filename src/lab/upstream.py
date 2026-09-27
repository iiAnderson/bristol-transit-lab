"""
Read-only access to the upstream `ons_to_subwaybuilder` database, and its pin.

Two rules live here rather than in comments elsewhere:

- The upstream DuckDB is only ever opened with ``read_only=True``. Nothing in this
  repo writes to it (CLAUDE.md rule 10).
- Every run records exactly which upstream it read: the DB file's hash, the package
  version and commit, whether the upstream tree was dirty, and a snapshot of
  ``stage_log`` — so a run can be tied to the upstream build that fed it.

Game-adjusted tables (`od_msoa_adj`, `base_flows`, and everything downstream of the
edge fold and external clamp) are refused by name: see GAME_TABLES.
"""
from __future__ import annotations

import hashlib
import subprocess
import tomllib
from pathlib import Path

import duckdb

from .config import LabConfig

# Tables that carry Subway Builder adjustments (edge fold, external clamp, pop-budget
# merge). CLAUDE.md rule 9: none of these may feed the lab.
GAME_TABLES = frozenset({
    "od_msoa", "od_msoa_adj", "base_flows", "pops_s1", "pops_final", "od_merged",
    "kept", "ranked", "reassigned", "ext_clamp", "ext_near", "edge_fold", "oa_map",
    "dest_share_msoa", "dest_cum", "oa_points", "pt_fixed", "water_snap",
})


class UpstreamError(RuntimeError):
    pass


def connect(cfg: LabConfig) -> duckdb.DuckDBPyConnection:
    if not cfg.upstream_db.is_file():
        raise UpstreamError(f"upstream DB not found: {cfg.upstream_db}")
    return duckdb.connect(str(cfg.upstream_db), read_only=True)


def attach(con: duckdb.DuckDBPyConnection, cfg: LabConfig, alias: str = "up") -> None:
    """Attach the upstream DB to a lab connection, read-only, as `alias`."""
    if not cfg.upstream_db.is_file():
        raise UpstreamError(f"upstream DB not found: {cfg.upstream_db}")
    con.execute(f"ATTACH '{cfg.upstream_db}' AS {alias} (READ_ONLY)")


def import_package(cfg: LabConfig):
    """Import the upstream package from the pinned repo (not from site-packages).

    The lab reuses upstream code — `covid.correct` — rather than re-implementing it,
    and the run record pins the commit, so the import must come from that checkout.
    """
    import importlib
    import sys
    repo = str(cfg.upstream_repo)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    pkg = importlib.import_module(cfg.upstream_package)
    if not Path(pkg.__file__).resolve().is_relative_to(cfg.upstream_repo.resolve()):
        raise UpstreamError(f"{cfg.upstream_package} imported from {pkg.__file__}, "
                            f"not the pinned repo {repo}")
    return pkg


def read_table(con: duckdb.DuckDBPyConnection, table: str):
    """Read an upstream table, refusing any that carries game adjustments."""
    if table in GAME_TABLES:
        raise UpstreamError(
            f"{table} carries Subway Builder adjustments (fold/clamp/merge) and must "
            "not feed the lab — build from the raw `flows` table instead")
    return con.table(table)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def _git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def pin(cfg: LabConfig) -> dict:
    """Everything needed to tie a run to the exact upstream build it read."""
    db = cfg.upstream_db
    if not db.is_file():
        raise UpstreamError(f"upstream DB not found: {db}")
    pyproject = cfg.upstream_repo / "pyproject.toml"
    version = None
    if pyproject.is_file():
        version = tomllib.loads(pyproject.read_text())["project"]["version"]
    status = _git(cfg.upstream_repo, "status", "--porcelain")

    con = connect(cfg)
    try:
        has_log = con.execute("SELECT count(*) FROM duckdb_tables() "
                              "WHERE table_name='stage_log'").fetchone()[0]
        if not has_log:
            raise UpstreamError(f"{db} has no stage_log — is this the pre-package build?")
        stage_log = [
            {"stage": s, "ran_at": t.isoformat(), "seq": int(q)}
            for s, t, q in con.execute(
                "SELECT stage, ran_at, seq FROM stage_log ORDER BY seq").fetchall()
        ]
    finally:
        con.close()

    return {
        "repo": str(cfg.upstream_repo),
        "db_path": str(db),
        "db_sha256": sha256_file(db),
        "db_bytes": db.stat().st_size,
        "package": cfg.upstream_package,
        "package_version": version,
        "commit": _git(cfg.upstream_repo, "rev-parse", "HEAD"),
        "dirty": None if status is None else bool(status),
        "stage_log": stage_log,
    }
