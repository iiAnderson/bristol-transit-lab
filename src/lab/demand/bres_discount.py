"""
The BRES discount d (SPEC §6.1).

    d = 1 − (workers attending a fixed workplace on an average weekday ÷ BRES jobs)

It combines the fixed-workplace share with the average number of days attended. No
single publication gives it, so it is bracketed by two independent estimates whose
biases run in opposite directions:

**NTS (ceiling on d).** NTS0412 gives commuting trips per employed person per year
(England), and NTS0504b the share of commuting trips made on weekdays. Two trips per
attended day, over the year's non-holiday weekdays, gives attendance per worker. NTS
counts only home ↔ usual-workplace trips as commuting; a trip to work from anywhere
else (after a school drop-off, say) and every work trip by someone with no usual
workplace count as business instead. So NTS *under*-counts attendance, and 1 − that
attendance is an upper bound on d.

**ONS Opinions and Lifestyle Survey (floor on d).** Working adults' arrangements in the
past seven days (travel only / hybrid / home only / neither), split by full- and
part-time, weighted by the survey's own weighted counts. Days on site for hybrid
workers and days worked by part-timers are not published and are [MODELLED] inputs.
A travel-only worker who took one or two days off in the week still counts as having
travelled, so this *over*-counts attendance: 1 − it is a lower bound on d.

Central is the midpoint of the two attendance estimates [MODELLED]. BRES counts jobs,
not people; second jobs (a few per cent of employment) would push d slightly higher and
are not adjusted for. The rate is national (England / Great Britain): neither source is
published for the South West or by area type, so the Data Science Campus's
cluster-level variation cannot be reproduced from current data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

# England 2025: 261 weekdays less 8 bank holidays (all eight fell on weekdays).
WEEKDAYS_2025 = 253

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


@dataclass(frozen=True)
class Inputs:
    year: int = 2025
    # [MODELLED] hybrid workers' days on site per 5-day week. Indeed Hiring Lab
    # (Sept 2025): two days in 56% of UK hybrid postings, two or three in 81%.
    hybrid_days_central: float = 2.5
    hybrid_days_high: float = 3.0
    # [MODELLED] days worked per week by part-timers. Not published by OPN.
    pt_days_central: float = 3.0
    pt_days_high: float = 3.5


@dataclass(frozen=True)
class Result:
    nts_commute_trips_per_worker: float
    nts_weekday_share: float
    attendance_nts: float
    attendance_opn_central: float
    attendance_opn_high: float
    d_low: float
    d_central: float
    d_high: float
    nts_attendance_2019: float


def _nts0412(path: Path, year: int) -> float:
    df = pd.read_excel(path, engine="odf", sheet_name="NTS0412a_trips", header=5)
    row = df[(df["Year"] == year) & (df["Employment status"] == "All employed people")]
    if len(row) != 1:
        raise ValueError(f"NTS0412: no single 'All employed people' row for {year}")
    return float(row["All modes"].iloc[0])


def _nts0504_weekday_share(path: Path, year: int | str) -> float:
    """`year` is a year or, before 2020, a rolling-period label such as '2015 to 2019'."""
    df = pd.read_excel(path, engine="odf", sheet_name="NTS0504b_day_purpose", header=5)
    df = df[df.iloc[:, 0].astype(str) == str(year)]
    if len(df) != 7:
        raise ValueError(f"NTS0504b: expected 7 days for {year!r}, got {len(df)}")
    wk = df[df["Day of the week"].isin(WEEKDAYS)]["Commuting"].sum()
    return float(wk / df["Commuting"].sum())


def _opn(path: Path) -> dict[str, dict[str, float]]:
    t = pd.read_excel(path, sheet_name="Table_1", header=10)
    t = t[t["Breakdown category"] == "Working pattern"]
    out: dict[str, dict[str, float]] = {}
    for pattern in ("Full-time", "Part-time"):
        g = t[t["Breakdown"] == pattern]
        est = dict(zip(g["Response options"], g["Estimate (%)"] / 100.0))
        out[pattern] = {
            "travel_only": est["Didn't work from home and travelled to work"],
            "hybrid": est["Both worked from home and travelled to work"],
            "weight": float(g["Weighted count"].iloc[0]),
        }
    return out


def opn_attendance(opn: dict, hybrid_days: float, pt_days: float) -> float:
    ft, pt = opn["Full-time"], opn["Part-time"]
    a_ft = ft["travel_only"] * 1.0 + ft["hybrid"] * hybrid_days / 5
    a_pt = (pt["travel_only"] * pt_days / 5
            + pt["hybrid"] * min(hybrid_days, pt_days) / 5)
    return (a_ft * ft["weight"] + a_pt * pt["weight"]) / (ft["weight"] + pt["weight"])


def derive(raw: Path, inputs: Inputs = Inputs()) -> Result:
    nts, ons = raw / "nts2025", raw / "ons"
    trips = _nts0412(nts / "nts0412.ods", inputs.year)
    share = _nts0504_weekday_share(nts / "nts0504.ods", inputs.year)
    a_nts = trips * share / 2 / WEEKDAYS_2025
    # 2019 for context only: 261 weekdays less 8 bank holidays; NTS0504 publishes
    # pre-2020 day-of-week splits only as five-year rolling periods.
    a_2019 = (_nts0412(nts / "nts0412.ods", 2019)
              * _nts0504_weekday_share(nts / "nts0504.ods", "2015 to 2019") / 2 / 253)
    opn = _opn(ons / "hybridsupplementary8januaryto30march2025.xlsx")
    a_c = opn_attendance(opn, inputs.hybrid_days_central, inputs.pt_days_central)
    a_h = opn_attendance(opn, inputs.hybrid_days_high, inputs.pt_days_high)
    return Result(
        nts_commute_trips_per_worker=trips, nts_weekday_share=share,
        attendance_nts=a_nts, attendance_opn_central=a_c, attendance_opn_high=a_h,
        d_low=1 - a_h, d_central=1 - (a_c + a_nts) / 2, d_high=1 - a_nts,
        nts_attendance_2019=a_2019,
    )


if __name__ == "__main__":
    from lab.config import LabConfig
    r = derive(LabConfig.load().root / "data" / "raw")
    for k, v in asdict(r).items():
        print(f"{k:<30} {v:.4f}")
