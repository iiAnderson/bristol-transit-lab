"""Stream an ODS workbook's sheets to CSV without loading it (pandas' odf reader cannot
cope with a 1 GB content.xml)."""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
O = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
X = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
MAX_REPEAT = 1000        # trailing empty cells/rows are repeated thousands of times


def _cell_value(c: ET.Element) -> str:
    for attr in ("value", "date-value", "boolean-value", "time-value"):
        v = c.get(O + attr)
        if v is not None:
            return v
    return "\n".join("".join(p.itertext()) for p in c.findall(X + "p"))


def to_csv(ods: Path, out_dir: Path, sheets: list[str] | None = None) -> dict[str, int]:
    """Write ``<sheet>.csv`` for each sheet (or those named); return rows per sheet."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with zipfile.ZipFile(ods) as z, z.open("content.xml") as f:
        writer, fh, name = None, None, None
        for ev, el in ET.iterparse(f, events=("start", "end")):
            if ev == "start" and el.tag == T + "table":
                name = el.get(T + "name")
                if sheets is None or name in sheets:
                    fh = (out_dir / f"{name}.csv").open("w", newline="")
                    writer = csv.writer(fh)
                    counts[name] = 0
            elif ev == "end" and el.tag == T + "table-row":
                if writer is not None:
                    row: list[str] = []
                    for c in el:
                        if c.tag not in (T + "table-cell", T + "covered-table-cell"):
                            continue
                        n = min(int(c.get(T + "number-columns-repeated", "1")), MAX_REPEAT)
                        row += [_cell_value(c)] * n
                    while row and row[-1] == "":
                        row.pop()
                    if row:
                        reps = min(int(el.get(T + "number-rows-repeated", "1")), MAX_REPEAT)
                        for _ in range(reps):
                            writer.writerow(row)
                            counts[name] += 1
                el.clear()
            elif ev == "end" and el.tag == T + "table":
                if fh:
                    fh.close()
                writer, fh = None, None
                el.clear()
    return counts
