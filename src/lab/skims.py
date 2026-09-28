"""
P2c skims (plans/P2.md C1, C1a; SPEC §7.1).

* PT: r5r's expanded matrix, run by ``src/lab/r/pt_skims.R`` in the ``transit-lab-r``
  env, OA origins → clip-box LSOA destinations (internal + external buffer), aggregated
  in R to one row per pair; full per-minute output kept for the 1% sample only.
* Walk and cycle: r5py, OA → LSOA, same routing settings (``params`` ``routing.*``).
* GC is computed here from the stored mean components with the ``generalised_cost``
  weights (GC is linear, so the mean GC over minutes = GC of the mean components).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

R_ENV = Path("/opt/homebrew/Caskroom/miniforge/base/envs/transit-lab-r")


def r_env() -> dict:
    env = dict(os.environ)
    env["JAVA_HOME"] = str(R_ENV / "lib" / "jvm")
    env["PATH"] = f"{R_ENV / 'bin'}:{R_ENV / 'lib' / 'jvm' / 'bin'}:{env['PATH']}"
    return env


def run_pt(script: Path, cfg: dict, cfg_path: Path, log) -> None:
    cfg_path.write_text(json.dumps(cfg))
    p = subprocess.Popen([str(R_ENV / "bin" / "Rscript"), str(script), str(cfg_path)],
                         env=r_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True)
    for line in p.stdout:
        if line.startswith("chunk") or "Error" in line or "error" in line:
            log(line.rstrip())
    if p.wait() != 0:
        raise RuntimeError(f"pt_skims.R failed ({p.returncode})")


def gc_from_components(df: pd.DataFrame, w: dict) -> pd.Series:
    """Time-based GC (min) from mean components: IVT + w_walk·walk + w_wait·wait +
    P_interchange·transfers (mean of max(rides − 1, 0) per minute, stored by the R
    script). Walk = access + egress + transfer walking."""
    walk = df["access_min"] + df["egress_min"] + df["transfer_min"]
    return (df["ride_min"] + w["w_walk"] * walk + w["w_wait"] * df["wait_min"]
            + w["p_interchange"] * df["n_transfers"])


def combine(out_dir: Path) -> pd.DataFrame:
    return pd.concat([pd.read_parquet(f) for f in sorted(out_dir.glob("chunk_*.parquet"))],
                     ignore_index=True)
