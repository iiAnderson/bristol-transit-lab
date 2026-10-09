"""The identity of a routable network (SPEC §4, amended at P3): the inputs that decide
what R5 builds, by hash. Two outputs are comparable only if their versions match."""
from __future__ import annotations

import hashlib
import json


def version(input_hashes: dict[str, str], elevation: str | None = None) -> str:
    """``input_hashes``: name → sha256 for the OSM clip, any OSM patch file, the GTFS
    feeds and, with ``elevation`` (the slope cost function's name), the terrain raster."""
    doc = {"inputs": dict(sorted(input_hashes.items())), "elevation": elevation}
    h = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:10]
    return f"{'flat' if elevation is None else elevation.lower()}-{h}"
