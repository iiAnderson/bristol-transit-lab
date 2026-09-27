"""
OSM for the baseline network (plans/P2.md A3): download the configured Geofabrik
extracts, verify each against its published MD5, merge, and clip to the clip box.

``-latest`` URLs redirect to dated files; the dated URL is what gets recorded. The OSM
data date is read from each file's replication timestamp and recorded beside the
modelled date (they may differ; that is accepted, plans/P2.md D1).
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import requests


class OsmError(RuntimeError):
    pass


def resolve(url: str, ua: str) -> str:
    r = requests.head(url, headers={"User-Agent": ua}, allow_redirects=False, timeout=60)
    if r.status_code in (301, 302, 303, 307, 308):
        return requests.compat.urljoin(url, r.headers["Location"])
    if r.status_code == 200:
        return url
    raise OsmError(f"{url}: HTTP {r.status_code}")


def download(url: str, dest: Path, ua: str) -> dict:
    """Fetch ``url`` (already resolved) to ``dest`` unless present; verify its MD5."""
    md5_txt = requests.get(url + ".md5", headers={"User-Agent": ua}, timeout=60)
    md5_txt.raise_for_status()
    want = md5_txt.text.split()[0]
    if not dest.is_file() or _md5(dest) != want:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with requests.get(url, headers={"User-Agent": ua}, stream=True, timeout=120) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        tmp.rename(dest)
    got = _md5(dest)
    if got != want:
        raise OsmError(f"{dest.name}: MD5 {got} does not match published {want}")
    return {"url": url, "path": dest, "md5": got,
            "replication_timestamp": replication_timestamp(dest)}


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _osmium(*args: str) -> str:
    p = subprocess.run(["osmium", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise OsmError(f"osmium {' '.join(args[:2])} failed: {p.stderr[-2000:]}")
    return p.stdout


def osmium_version() -> str:
    return _osmium("--version").splitlines()[0]


def replication_timestamp(p: Path) -> str:
    return _osmium("fileinfo", "-g", "header.option.osmosis_replication_timestamp",
                   str(p)).strip()


def merge_and_clip(inputs: list[Path], box: tuple[float, float, float, float],
                   merged: Path, out: Path) -> dict:
    merged.parent.mkdir(parents=True, exist_ok=True)
    _osmium("merge", *map(str, inputs), "-O", "-o", str(merged))
    bbox = ",".join(f"{v:.5f}" for v in box)
    _osmium("extract", "-b", bbox, "--strategy", "complete_ways", "-O", "-o", str(out),
            str(merged))
    info = _osmium("fileinfo", "-e", "-g", "data.count.nodes", str(out)).strip()
    ways = _osmium("fileinfo", "-e", "-g", "data.count.ways", str(out)).strip()
    return {"nodes": int(info), "ways": int(ways), "bbox": bbox}
