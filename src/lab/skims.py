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


def chunk_key(cfg: dict, inputs: list[str]) -> str:
    """Identity of a PT skim build: every setting the R script reads plus the input
    dataset hashes. Chunks written under another key must not be resumed."""
    import hashlib
    return hashlib.sha256(json.dumps([cfg, inputs], sort_keys=True).encode()).hexdigest()


def reset_stale_chunks(out_dir: Path, key: str, log) -> bool:
    """The R script skips chunks that already exist (resume after an interruption). Clear
    them when they were built with different settings or inputs. Returns True if cleared."""
    out_dir.mkdir(parents=True, exist_ok=True)
    kf = out_dir / "_key.txt"
    old = list(out_dir.glob("chunk_*.parquet"))
    stale = bool(old) and (not kf.is_file() or kf.read_text().strip() != key)
    if stale:
        log(f"settings or inputs changed: clearing {len(old)} stale chunks")
        for f in [*old, *out_dir.glob("minutes_*.parquet")]:
            f.unlink()
    kf.write_text(key)
    return stale


def reset_stale_network(net_dir: Path, feed_hashes: list[str], log) -> bool:
    """r5r reuses ``network.dat`` in the network folder whatever the OSM and GTFS files
    beside it now contain. Delete the built network when the input hashes differ from
    those it was built with. Returns True if deleted."""
    kf = net_dir / "_inputs.txt"
    key = "\n".join(feed_hashes)
    built = (net_dir / "network.dat").is_file()
    stale = built and (not kf.is_file() or kf.read_text() != key)
    if stale:
        log("network inputs changed: rebuilding the r5r network")
        for f in [net_dir / "network.dat", *net_dir.glob("*.mapdb*")]:
            f.unlink(missing_ok=True)
    kf.write_text(key)
    return stale


def run_pt(script: Path, cfg: dict, cfg_path: Path, log, inputs: list[str]) -> None:
    reset_stale_network(Path(cfg["net_dir"]), inputs[:3], log)
    reset_stale_chunks(Path(cfg["out_dir"]), chunk_key(cfg, inputs), log)
    cfg_path.write_text(json.dumps(cfg))
    p = subprocess.Popen([str(R_ENV / "bin" / "Rscript"), str(script), str(cfg_path)],
                         env=r_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True)
    for line in p.stdout:
        if line.startswith("chunk") or "Error" in line or "error" in line:
            log(line.rstrip())
    if p.wait() != 0:
        raise RuntimeError(f"pt_skims.R failed ({p.returncode})")


def ride_only(df: pd.DataFrame) -> pd.DataFrame:
    """The ride-minute means (r_ columns) under the component names the GC functions
    read, for the PT alternative with at least one ride."""
    return pd.DataFrame({"access_min": df["r_access_min"], "wait_min": df["r_wait_min"],
                         "ride_min": df["r_ride_min"], "transfer_min": df["r_transfer_min"],
                         "egress_min": df["r_egress_min"], "n_transfers": df["r_n_transfers"],
                         "best_min": df["r_best_min"]}, index=df.index)


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


def first_wait(effective_headway_min, curve: list[list[float]]) -> np.ndarray:
    """Perceived first wait (min) from a wait curve [[headway, wait], ...] (TAG M3.2
    Fig. 2 shape): linear interpolation, continued beyond the last point at the last
    segment's slope."""
    h = np.asarray(effective_headway_min, dtype=float)
    xs = np.array([p[0] for p in curve], dtype=float)
    ys = np.array([p[1] for p in curve], dtype=float)
    out = np.interp(h, xs, ys)
    slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
    return np.where(h > xs[-1], ys[-1] + slope * (h - xs[-1]), out)


def gc_tag(df: pd.DataFrame, w: dict, curve: list[list[float]]) -> pd.Series:
    """PT GC with the TAG M3.2 treatment of waiting: the first wait from the wait curve
    applied to the effective headway (2 × initial wait, where the initial wait is the
    mean total over the window minus the best-departure total, bounded by the mean
    wait), transfer waits as modelled, both weighted by w_wait."""
    total = df["access_min"] + df["wait_min"] + df["ride_min"] + df["transfer_min"] + df["egress_min"]
    initial = (total - df["best_min"]).clip(lower=0)
    initial = np.minimum(initial, df["wait_min"])
    transfer_wait = (df["wait_min"] - initial).clip(lower=0)
    walk = df["access_min"] + df["egress_min"] + df["transfer_min"]
    fw = first_wait(2 * initial, curve)
    return (df["ride_min"] + w["w_walk"] * walk + w["w_wait"] * (transfer_wait + fw)
            + w["p_interchange"] * df["n_transfers"])
