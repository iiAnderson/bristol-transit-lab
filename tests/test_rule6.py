"""CLAUDE.md rule 6: no place names, line names, route IDs, station codes, zone codes or
coordinates inside ``src/``. They live in ``scenarios/``, ``landuse/`` and config.

The scan covers code, comments and docstrings of every ``.py`` and ``.R`` file under
``src/lab``. The project's own name is exempt. Findings that predate the test (P2 code)
are allowed only through ``tests/rule6_known.txt``: one ``path|term|justification`` per
line, comments and docstrings only (Robbie, 2026-10-10). The test fails on anything not
listed, on an entry without a justification, and on any entry that no longer occurs.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lab"
PROJECT_NAME = "Bristol Transit Lab"

PATTERNS = {
    "ONS zone code": re.compile(r"\b[EW]\d{8}\b"),
    "GTFS route or trip id": re.compile(r"\b(?:south_west|wales):\w+"),
    "NaPTAN / ATCO code": re.compile(r"\b0[1-9]\d{2}[A-Z]{3}\d{3,}\b|\b9100[A-Z]{3,7}\b"),
    # a longitude or latitude inside the modelled extent, to 3+ decimal places
    "coordinate": re.compile(r"(?<![\w.])-?(?:[23]\.\d{3,}|5[12]\.\d{3,})(?![\d]*\s*(?:%|km|min))"),
}


def _terms() -> tuple[list[str], list[str]]:
    section, out = None, {"names": [], "station_codes": []}
    for line in (ROOT / "tests" / "rule6_terms.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
        else:
            out[section].append(line)
    return out["names"], out["station_codes"]


def scan(files: list[Path], root: Path = ROOT) -> set[tuple[str, str]]:
    names, codes = _terms()
    name_re = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len,
                                                   reverse=True)) + r")\b")
    code_re = re.compile(r"""["'](%s)["']""" % "|".join(map(re.escape, codes)))
    found = set()
    for f in files:
        rel = f.relative_to(root).as_posix()
        for line in f.read_text().replace(PROJECT_NAME, "").splitlines():
            for pat in [*PATTERNS.values(), name_re]:
                found |= {(rel, m.group(0)) for m in pat.finditer(line)}
            found |= {(rel, m.group(1)) for m in code_re.finditer(line)}
    return found


def _src_files() -> list[Path]:
    return sorted(f for f in SRC.rglob("*") if f.suffix in (".py", ".R"))


def _known() -> set[tuple[str, str]]:
    rows = (ROOT / "tests" / "rule6_known.txt").read_text().splitlines()
    out = set()
    for r in rows:
        if r and not r.startswith("#"):
            path, term, why = r.split("|", 2)
            assert why.strip(), f"rule 6 allowlist entry without a justification: {path}|{term}"
            out.add((path, term))
    return out


def test_no_new_place_data_in_src():
    new = scan(_src_files()) - _known()
    assert not new, "rule 6: place data in src/ — move it to config or scenarios/:\n" + \
        "\n".join(f"  {f}: {t}" for f, t in sorted(new))


def test_known_list_only_shrinks():
    gone = _known() - scan(_src_files())
    assert not gone, "rule 6: remove fixed entries from tests/rule6_known.txt:\n" + \
        "\n".join(f"  {f}|{t}" for f, t in sorted(gone))


def test_scan_catches_each_kind(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text('x = "E01033903"\nr = "south_west:3144"\ns = "0100BRP90986"\n'
                 'lon = -2.5878\n# near Temple Meads\nstn = "BRI"\n'
                 'share = 0.874\nlimit = 2.5\n')
    got = {t for _, t in scan([f], tmp_path)}
    assert got == {"E01033903", "south_west:3144", "0100BRP90986", "-2.5878",
                   "Temple Meads", "BRI"}
