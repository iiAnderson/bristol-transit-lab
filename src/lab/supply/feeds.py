"""
The feed registry (plans/P2.md A2): one row per input feed in ``lab.duckdb``.

Every feed the baseline network reads is registered with where it came from, its hash,
its validity window and its validator summary. ``require_covers`` is the rule-5 check:
a feed whose service calendar does not cover the modelled date is an error.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import duckdb

from ..config import LabConfig

DDL = """
CREATE TABLE IF NOT EXISTS feed (
    feed_id            VARCHAR PRIMARY KEY,
    kind               VARCHAR NOT NULL,    -- osm | bus_gtfs | rail_gtfs | rail_darwin | avl | ref
    source_url         VARCHAR NOT NULL,
    local_path         VARCHAR NOT NULL,
    downloaded_at      TIMESTAMPTZ NOT NULL,
    sha256             VARCHAR NOT NULL,
    licence            VARCHAR NOT NULL,
    valid_from         DATE,
    valid_to           DATE,
    validator_errors   INTEGER,
    validator_warnings INTEGER,
    notes              VARCHAR
)"""


class FeedError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def connect(cfg: LabConfig) -> duckdb.DuckDBPyConnection:
    cfg.lab_db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cfg.lab_db))
    con.execute(DDL)
    return con


def register(cfg: LabConfig, *, feed_id: str, kind: str, source_url: str, path: Path,
             downloaded_at: dt.datetime, licence: str, valid_from: dt.date | None = None,
             valid_to: dt.date | None = None, validator_errors: int | None = None,
             validator_warnings: int | None = None, notes: str | None = None) -> dict:
    """Insert or replace a feed row; the hash is taken from the file now."""
    if not path.is_file():
        raise FeedError(f"feed {feed_id}: {path} does not exist")
    if downloaded_at.tzinfo is None:
        raise FeedError(f"feed {feed_id}: downloaded_at needs a time zone")
    row = dict(feed_id=feed_id, kind=kind, source_url=source_url, local_path=str(path),
               downloaded_at=downloaded_at, sha256=sha256(path), licence=licence,
               valid_from=valid_from, valid_to=valid_to,
               validator_errors=validator_errors, validator_warnings=validator_warnings,
               notes=notes)
    with connect(cfg) as con:
        con.execute(f"INSERT OR REPLACE INTO feed VALUES ({', '.join('?' * len(row))})",
                    list(row.values()))
    return row


def get(cfg: LabConfig, feed_id: str) -> dict:
    with connect(cfg) as con:
        # TIMESTAMPTZ comes back as an ISO string (DuckDB would otherwise need pytz).
        cur = con.execute("SELECT * REPLACE (CAST(downloaded_at AS VARCHAR) AS downloaded_at) "
                          "FROM feed WHERE feed_id = ?", [feed_id])
        r = cur.fetchone()
        if r is None:
            raise FeedError(f"feed {feed_id} is not registered")
        return dict(zip([d[0] for d in cur.description], r))


def require_covers(row: dict, day: dt.date) -> None:
    """Fail loudly if the feed's validity window does not include ``day`` (rule 5)."""
    lo, hi = row["valid_from"], row["valid_to"]
    if lo is None or hi is None:
        raise FeedError(f"feed {row['feed_id']} has no validity window recorded")
    if not lo <= day <= hi:
        raise FeedError(f"feed {row['feed_id']} is valid {lo}..{hi}, which does not cover "
                        f"the modelled date {day}")


def check_file_unchanged(row: dict) -> None:
    """The registered hash still matches the file on disk."""
    now = sha256(Path(row["local_path"]))
    if now != row["sha256"]:
        raise FeedError(f"feed {row['feed_id']}: {row['local_path']} changed since "
                        f"registration ({row['sha256'][:12]} -> {now[:12]})")
