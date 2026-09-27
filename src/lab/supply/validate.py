"""MobilityData GTFS validator (plans/P2.md A4/A5): run it, summarise, fail on errors."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from collections import Counter
from pathlib import Path


class ValidatorError(RuntimeError):
    pass


def run(jar: Path, gtfs: Path, out: Path, day: dt.date, country: str) -> dict:
    if not jar.is_file():
        raise ValidatorError(f"GTFS validator not found at {jar}; see sources.md P2")
    java = Path(os.environ.get("JAVA_HOME", "")) / "bin" / "java"
    cmd = [str(java) if java.is_file() else "java", "-jar", str(jar), "-i", str(gtfs),
           "-o", str(out), "-c", country, "-d", day.isoformat()]
    p = subprocess.run(cmd, capture_output=True, text=True)
    report = out / "report.json"
    if p.returncode != 0 or not report.is_file():
        raise ValidatorError(f"validator failed ({p.returncode}): {p.stderr[-2000:]}")
    notices = json.loads(report.read_text())["notices"]
    by = Counter()
    codes = {}
    for n in notices:
        by[n["severity"]] += n["totalNotices"]
        codes[f"{n['severity']}:{n['code']}"] = n["totalNotices"]
    return {"errors": by.get("ERROR", 0), "warnings": by.get("WARNING", 0),
            "infos": by.get("INFO", 0), "codes": codes, "report": str(report),
            "jar": jar.name}
