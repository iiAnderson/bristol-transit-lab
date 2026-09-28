"""
Bristol City Council ANPR journey times ("Journey Counts" + "Journey Links"), for
held-out validation of car speeds in the centre (plans/P2.md §8: 2023–24 for absolute
levels, older years for relative patterns only).

Rows are hourly link speeds (mph) and journey times (s) with the number of plate
matches. The service takes native SQL date literals (``DATE_TIME >= 'YYYY-MM-DD'``).
Fetched in pages at ≤ 1 request/s, one Parquet per year.
"""
from __future__ import annotations

import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

BASE = "https://maps2.bristol.gov.uk/server2/rest/services/ext/Traffic/MapServer"


def fetch_year(year: int, out: Path, ua: str, page: int = 2000, pause: float = 1.0) -> int:
    if out.is_file():
        return pq.read_metadata(out).num_rows
    s = requests.Session()
    s.headers["User-Agent"] = ua
    where = f"DATE_TIME >= '{year}-01-01' AND DATE_TIME < '{year + 1}-01-01'"
    rows = {k: [] for k in ("link_id", "t_ms", "matches", "journey_s", "mph")}
    off = 0
    while True:
        r = s.get(f"{BASE}/3/query", params={
            "where": where, "outFields": "JOURNEY_LINK_ID,DATE_TIME,TOTAL_MATCHES,JOURNEY_TIME,SPEED",
            "orderByFields": "OBJECTID", "resultOffset": off, "resultRecordCount": page,
            "returnGeometry": "false", "f": "json"}, timeout=120)
        r.raise_for_status()
        feats = r.json().get("features", [])
        for f in feats:
            a = f["attributes"]
            rows["link_id"].append(a["JOURNEY_LINK_ID"]); rows["t_ms"].append(a["DATE_TIME"])
            rows["matches"].append(a["TOTAL_MATCHES"]); rows["journey_s"].append(a["JOURNEY_TIME"])
            rows["mph"].append(a["SPEED"])
        if len(feats) < page:
            break
        off += page
        time.sleep(pause)
    t = pa.table(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, out, compression="zstd")
    return t.num_rows


def fetch_links(out: Path, ua: str) -> int:
    r = requests.get(f"{BASE}/2/query", params={"where": "1=1", "outFields": "*",
                                                 "outSR": 4326, "f": "geojson"},
                     headers={"User-Agent": ua}, timeout=120)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(r.text)
    return len(r.json()["features"])
