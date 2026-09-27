"""
National Highways WebTRIS: 15-minute speed and flow at SRN sites (plans/P2.md A7, B3).

Every *active* site inside the clip box is fetched for a date window, one site per
request (paged if needed), at most one request per ``min_interval_s``, with backoff on
429/5xx/timeouts. One Parquet file per site; a site already on disk is skipped, so the
fetch resumes. All days are kept here; the neutral-day filter is applied in B3.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

BASE = "https://webtris.nationalhighways.co.uk/api/v1.0"
SCHEMA = pa.schema([
    ("site_id", pa.string()), ("site_name", pa.string()), ("date", pa.date32()),
    ("time_end", pa.string()), ("interval", pa.int16()),
    ("avg_mph", pa.float32()), ("volume", pa.int32()),
    ("len_0_520", pa.int32()), ("len_521_660", pa.int32()),
    ("len_661_1160", pa.int32()), ("len_1160_plus", pa.int32()),
])


class WebtrisError(RuntimeError):
    pass


def _int(v):
    return int(v) if v not in (None, "") else None


def _float(v):
    return float(v) if v not in (None, "") else None


class Client:
    def __init__(self, ua: str, min_interval_s: float = 1.0, sleep=time.sleep,
                 clock=time.monotonic):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = ua
        self.min_interval_s = min_interval_s
        self.sleep, self.clock = sleep, clock
        self.last = None
        self.requests = 0

    def get(self, path: str, params: dict | None = None) -> requests.Response | None:
        """GET with the rate floor and up to 6 backed-off retries; None on 204."""
        for attempt in range(7):
            if self.last is not None:
                self.sleep(max(0.0, self.last + self.min_interval_s - self.clock()))
            self.last = self.clock()
            self.requests += 1
            try:
                r = self.s.get(f"{BASE}{path}", params=params, timeout=120)
            except requests.RequestException:
                r = None
            if r is not None and r.status_code == 204:
                return None
            if r is not None and r.status_code == 200:
                return r
            if r is not None and r.status_code < 500 and r.status_code != 429:
                raise WebtrisError(f"{path} {params}: HTTP {r.status_code}")
            self.sleep(min(600, 15 * 2 ** attempt))
        raise WebtrisError(f"{path} {params}: gave up after retries")


def sites_in_box(client: Client, box: tuple, out: Path) -> list[dict]:
    r = client.get("/sites")
    sites = r.json()["sites"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sites))
    x0, y0, x1, y1 = box
    return [s for s in sites if x0 <= s["Longitude"] <= x1 and y0 <= s["Latitude"] <= y1]


def fetch_site(client: Client, site: dict, start: dt.date, end: dt.date,
               out_dir: Path, page_size: int = 40000) -> dict:
    out = out_dir / f"site={site['Id']}.parquet"
    if out.is_file():
        return {"site": site["Id"], "status": "skipped (on disk)"}
    rows, page = [], 1
    while True:
        r = client.get("/reports/Daily", {
            "sites": site["Id"], "start_date": f"{start:%d%m%Y}",
            "end_date": f"{end:%d%m%Y}", "page": page, "page_size": page_size})
        if r is None:
            break
        d = r.json()
        for x in d.get("Rows", []):
            rows.append({
                "site_id": site["Id"], "site_name": x["Site Name"],
                "date": dt.date.fromisoformat(x["Report Date"][:10]),
                "time_end": x["Time Period Ending"], "interval": _int(x["Time Interval"]),
                "avg_mph": _float(x["Avg mph"]), "volume": _int(x["Total Volume"]),
                "len_0_520": _int(x["0 - 520 cm"]), "len_521_660": _int(x["521 - 660 cm"]),
                "len_661_1160": _int(x["661 - 1160 cm"]), "len_1160_plus": _int(x["1160+ cm"]),
            })
        links = [link for link in d["Header"].get("links", []) if link.get("rel") == "nextPage"]
        if not links:
            break
        page += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
    tmp.rename(out)
    return {"site": site["Id"], "status": "ok", "rows": len(rows),
            "days": len({r["date"] for r in rows})}
