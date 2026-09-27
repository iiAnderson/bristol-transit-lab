"""ONS points and boundaries for internal zones (OA centroids for D3; LSOA areas for D4).

Upstream's ``pwc`` holds only OAs whose own centroid lies inside the extent; the lab's
internal zones follow the LSOA-centroid rule (P1), which adds a few OAs whose own
centroid is outside. So the points come from ONS directly.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests


def fetch(service: str, codes: list[str], out: Path, ua: str, batch: int = 200,
          field: str = "OA21CD", out_fields: str | None = None) -> dict:
    feats = []
    s = requests.Session()
    s.headers["User-Agent"] = ua
    for i in range(0, len(codes), batch):
        where = f"{field} IN (" + ",".join(f"'{c}'" for c in codes[i:i + batch]) + ")"
        r = s.post(f"{service}/query", data={"where": where, "outFields": out_fields or field,
                                             "outSR": 4326, "f": "geojson"}, timeout=120)
        r.raise_for_status()
        feats += r.json()["features"]
        time.sleep(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    got = {f["properties"][field] for f in feats}
    return {"requested": len(codes), "returned": len(feats),
            "missing": sorted(set(codes) - got)}
