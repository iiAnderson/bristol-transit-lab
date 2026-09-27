"""Repo-level configuration: where the lab finds upstream, its own DB and its runs."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(RuntimeError):
    pass


def repo_root() -> Path:
    """The lab repo root: LAB_ROOT if set, else the nearest parent holding config/lab.yaml."""
    if env := os.environ.get("LAB_ROOT"):
        return Path(env)
    here = Path(__file__).resolve()
    for p in [Path.cwd(), *Path.cwd().parents, *here.parents]:
        if (p / "config" / "lab.yaml").is_file():
            return p
    raise ConfigError("cannot find config/lab.yaml; run from the repo or set LAB_ROOT")


@dataclass(frozen=True)
class LabConfig:
    root: Path
    upstream_repo: Path
    upstream_db: Path
    upstream_package: str
    extent: tuple[float, float, float, float]
    lab_db: Path
    runs_dir: Path

    @classmethod
    def load(cls, root: Path | None = None) -> "LabConfig":
        root = root or repo_root()
        raw = yaml.safe_load((root / "config" / "lab.yaml").read_text())
        up = raw["upstream"]
        repo = Path(os.environ.get("LAB_UPSTREAM_REPO", up["repo"])).expanduser()
        extent = tuple(float(x) for x in raw["extent"])
        if len(extent) != 4 or not (extent[0] < extent[2] and extent[1] < extent[3]):
            raise ConfigError(f"extent {extent} is not [min_lon, min_lat, max_lon, max_lat]")
        return cls(
            root=root,
            upstream_repo=repo,
            upstream_db=repo / up["db"],
            upstream_package=up["package"],
            extent=extent,  # type: ignore[arg-type]
            lab_db=root / raw["paths"]["lab_db"],
            runs_dir=root / raw["paths"]["runs"],
        )
