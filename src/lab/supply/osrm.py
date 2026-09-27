"""
Native OSRM (Homebrew ``osrm-backend``) for car routing (plans/P2.md D2).

MLD pipeline: extract (once per OSM clip) → partition (once) → customize (once per set
of segment speeds). ``Server`` runs ``osrm-routed`` on a probed free port (never 5000:
macOS AirPlay owns it) and answers table queries in chunks.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

import numpy as np
import requests


class OsrmError(RuntimeError):
    pass


def _bin(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise OsrmError(f"{name} not found; `brew install osrm-backend` (sources.md P2, D2)")
    return p


def version() -> str:
    return subprocess.run([_bin("osrm-extract"), "--version"], capture_output=True,
                          text=True).stdout.strip()


def profile(name: str = "car") -> Path:
    p = Path(_bin("osrm-extract")).resolve().parents[1] / "share" / "osrm" / "profiles" / f"{name}.lua"
    if not p.is_file():
        raise OsrmError(f"OSRM profile {p} not found")
    return p


def _run(*args: str) -> None:
    r = subprocess.run(list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise OsrmError(f"{Path(args[0]).name} failed: {r.stderr[-2000:] or r.stdout[-2000:]}")


def prepare(pbf: Path, base: Path) -> Path:
    """Extract and partition ``pbf`` into ``<base>.osrm*`` (base: path without suffix)."""
    base.parent.mkdir(parents=True, exist_ok=True)
    src = base.with_suffix(".osm.pbf")
    if src.resolve() != pbf.resolve():
        shutil.copyfile(pbf, src)
    _run(_bin("osrm-extract"), "-p", str(profile()), str(src))
    _run(_bin("osrm-partition"), str(base))
    return base


def customize(base: Path, speed_file: Path | None = None) -> None:
    args = [_bin("osrm-customize"), str(base)]
    if speed_file is not None:
        args += ["--segment-speed-file", str(speed_file)]
    _run(*args)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    if port == 5000:
        return free_port()
    return port


class Server:
    def __init__(self, base: Path, max_table: int = 10000):
        self.base, self.max_table = base, max_table
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Server":
        self.proc = subprocess.Popen(
            [_bin("osrm-routed"), "--algorithm", "mld", "--max-table-size",
             str(self.max_table), "-i", "127.0.0.1", "-p", str(self.port), str(self.base)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                requests.get(f"http://127.0.0.1:{self.port}/nearest/v1/driving/0,0", timeout=1)
                return self
            except requests.ConnectionError:
                time.sleep(0.1)
        self.__exit__()
        raise OsrmError("osrm-routed did not start")

    def __exit__(self, *exc) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)

    def table(self, sources: list[tuple[float, float]], destinations: list[tuple[float, float]],
              chunk: int = 200) -> np.ndarray:
        """Durations in seconds, sources × destinations (NaN where unroutable)."""
        out = []
        for i in range(0, len(sources), chunk):
            ch = sources[i:i + chunk]
            pts = ch + destinations
            coords = ";".join(f"{x:.6f},{y:.6f}" for x, y in pts)
            r = requests.get(
                f"http://127.0.0.1:{self.port}/table/v1/driving/{coords}",
                params={"sources": ";".join(map(str, range(len(ch)))),
                        "destinations": ";".join(map(str, range(len(ch), len(pts)))),
                        "annotations": "duration"}, timeout=600)
            r.raise_for_status()
            j = r.json()
            if j.get("code") != "Ok":
                raise OsrmError(f"table: {j.get('code')} {j.get('message')}")
            out.append(np.array(j["durations"], dtype=float))
        return np.vstack(out)
