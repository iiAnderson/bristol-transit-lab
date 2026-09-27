"""
Darwin Push Port timetable → GTFS for one service date (plans/P2.md A5).

Input is a Darwin ``_v8`` timetable snapshot (schedules for about 48 hours from its
generation), its ``_ref`` file (TIPLOC → CRS and names, operators), and NaPTAN rail
access nodes (``9100<TIPLOC>`` → coordinates). Output is a GTFS zip with a single-date
``calendar_dates.txt``.

Rules (each exclusion is counted, never silent):

* one trip per passenger ``Journey`` whose ``ssd`` is the service date. Dropped: not a
  passenger service, deleted, charter, cancelled as a whole, run-as-required
  (``qtrain``), and road or water services — bus replacement (``trainCat`` BR), timetabled
  buses (BS, ``status`` B/5) and ships (SS, ``status`` S);
* stops are the public calls — ``OR``/``IP``/``DT`` with a public time. Passing points and
  operational calls (``PP``, ``OPOR``/``OPIP``/``OPDT``) and cancelled calls are dropped;
  a call with only a public arrival is set-down only, only a departure pick-up only;
* times past midnight run on past 24:00 on the service date;
* trips are kept if they call inside the clip box, and cut to their calls inside it;
* public times at a location with no CRS (a junction) are dropped, counted;
* a station TIPLOC with no NaPTAN coordinate fails the build if it sits next to a call inside the
  box on a kept trip (it could itself be inside); others are listed.

Nothing here names a place: the box, date, time zone and expected stations are inputs.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PUBLIC = {"OR", "IP", "DT"}
OPERATIONAL = {"PP", "OPOR", "OPIP", "OPDT"}
ROAD_CATS = {"BR", "BS"}
WATER_CATS = {"SS"}
ROAD_STATUS = {"B", "5"}
WATER_STATUS = {"S"}


class RailGtfsError(RuntimeError):
    pass


def _open(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class Ref:
    timetable_id: str
    crs: dict[str, str]              # tpl -> crs
    name: dict[str, str]             # tpl -> locname
    tocs: dict[str, tuple[str, str]]  # toc -> (name, url)


def read_ref(path: Path) -> Ref:
    crs, name, tocs, tid = {}, {}, {}, None
    for _, el in ET.iterparse(_open(path), events=("start", "end")):
        t = _local(el.tag)
        if t == "PportTimetableRef" and tid is None:
            tid = el.get("timetableId")
        if t == "LocationRef":
            tpl = el.get("tpl")
            if el.get("crs"):
                crs[tpl] = el.get("crs")
            name[tpl] = el.get("locname") or tpl
        elif t == "TocRef":
            tocs[el.get("toc")] = (el.get("tocname") or el.get("toc"), el.get("url") or "")
    return Ref(tid or "", crs, name, tocs)


def read_naptan(path: Path) -> dict[str, tuple[float, float, str, str]]:
    """TIPLOC -> (lon, lat, common name, status) for NaPTAN rail stations (RLY)."""
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["StopType"] != "RLY" or not r["ATCOCode"].startswith("9100"):
                continue
            if not r["Longitude"] or not r["Latitude"]:
                continue
            out[r["ATCOCode"][4:]] = (float(r["Longitude"]), float(r["Latitude"]),
                                      r["CommonName"], r["Status"])
    return out


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def _gtfs_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


@dataclass
class Call:
    tpl: str
    arr: int | None      # minutes after midnight on the service date
    dep: int | None


@dataclass
class Trip:
    rid: str
    toc: str
    train_id: str
    calls: list[Call]


@dataclass
class Result:
    timetable_id: str
    service_date: dt.date
    trips: list[Trip]
    counts: Counter = field(default_factory=Counter)
    unmapped: dict[str, int] = field(default_factory=dict)     # tpl -> calls, not fatal
    stops: dict[str, tuple] = field(default_factory=dict)       # stop_id -> (name, lon, lat)
    stop_of: dict[str, str] = field(default_factory=dict)       # tpl -> stop_id
    calls_by_tpl: Counter = field(default_factory=Counter)      # kept trips, in the box


def _exclusion(j: ET.Element) -> str | None:
    a = j.attrib
    if a.get("isPassengerSvc") == "false":
        return "not_passenger"
    if a.get("deleted") == "true":
        return "deleted"
    if a.get("isCharter") == "true":
        return "charter"
    if a.get("can") == "true":
        return "cancelled_journey"
    if a.get("qtrain") == "true":
        return "run_as_required"
    cat, status = a.get("trainCat"), a.get("status")
    if cat in ROAD_CATS or status in ROAD_STATUS:
        return f"road_{cat or 'status' + str(status)}"
    if cat in WATER_CATS or status in WATER_STATUS:
        return "water"
    return None


def _calls(j: ET.Element, counts: Counter) -> list[Call]:
    out: list[Call] = []
    last = None
    offset = 0
    for c in j:
        t = _local(c.tag)
        if t in OPERATIONAL:
            counts[f"call_dropped_{t}"] += 1
            continue
        if t not in PUBLIC:
            continue
        if c.get("can") == "true":
            counts["call_dropped_cancelled"] += 1
            continue
        pta, ptd = c.get("pta"), c.get("ptd")
        if not pta and not ptd:
            counts["call_dropped_no_public_time"] += 1
            continue
        arr = _minutes(pta) if pta else None
        dep = _minutes(ptd) if ptd else None
        # Roll past midnight: each time is at or after the previous one.
        vals = []
        for v in (arr, dep):
            if v is None:
                vals.append(None)
                continue
            v += offset
            if last is not None and v < last:
                offset += 1440
                v += 1440
            last = v
            vals.append(v)
        out.append(Call(c.get("tpl"), vals[0], vals[1]))
    return out


def convert(timetable: Path, ref: Ref, naptan: dict, service_date: dt.date,
            box: tuple[float, float, float, float]) -> Result:
    x0, y0, x1, y1 = box

    def inside(tpl: str) -> bool:
        p = naptan.get(tpl)
        return p is not None and x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    res = Result(timetable_id="", service_date=service_date, trips=[])
    c = res.counts
    day = service_date.isoformat()
    unmapped_adjacent: Counter = Counter()
    for ev, el in ET.iterparse(_open(timetable), events=("start", "end")):
        t = _local(el.tag)
        if ev == "start":
            if t == "PportTimetable":
                res.timetable_id = el.get("timetableID", "")
            continue
        if t != "Journey":
            continue
        attrs = dict(el.attrib)
        c["journeys_in_file"] += 1
        if attrs.get("ssd") != day:
            c["journeys_other_dates"] += 1
            el.clear()
            continue
        c["journeys_on_date"] += 1
        why = _exclusion(el)
        if why:
            c[f"excluded_{why}"] += 1
            el.clear()
            continue
        calls = _calls(el, c)
        el.clear()
        # Public times at a location with no CRS (a junction on a diversion) are not
        # station calls.
        n = len(calls)
        calls = [k for k in calls if k.tpl in ref.crs]
        c["call_dropped_no_crs"] += n - len(calls)
        if len(calls) < 2:
            c["excluded_fewer_than_2_public_calls"] += 1
            continue
        flags = [inside(k.tpl) for k in calls]
        if not any(flags):
            c["outside_box"] += 1
            continue
        for i, k in enumerate(calls):
            if k.tpl not in naptan:
                res.unmapped[k.tpl] = res.unmapped.get(k.tpl, 0) + 1
                near = flags[max(0, i - 1): i + 2]
                if any(near):
                    unmapped_adjacent[k.tpl] += 1
        kept = [k for k, f in zip(calls, flags) if f]
        c["calls_cut_outside_box"] += len(calls) - len(kept)
        if len(kept) < 2:
            c["excluded_one_call_in_box"] += 1
            continue
        res.trips.append(Trip(attrs.get("rid", ""), attrs.get("toc", ""),
                              attrs.get("trainId", ""), kept))
        c["trips_kept"] += 1
    if c["journeys_on_date"] == 0:
        raise RailGtfsError(f"{timetable.name} has no journeys on {day}: the snapshot does "
                            "not cover the service date")
    if unmapped_adjacent:
        raise RailGtfsError("TIPLOCs without a NaPTAN coordinate next to calls inside the "
                            f"box (could be inside it): {dict(unmapped_adjacent)}")
    _stops(res, ref, naptan)
    return res


def _stops(res: Result, ref: Ref, naptan: dict) -> None:
    """One stop per CRS (per TIPLOC if it has none); merge repeated consecutive stops."""
    for trip in res.trips:
        merged: list[Call] = []
        for k in trip.calls:
            sid = ref.crs.get(k.tpl, k.tpl)
            res.stop_of[k.tpl] = sid
            if sid not in res.stops:
                lon, lat, _, _ = naptan[k.tpl]
                res.stops[sid] = (ref.name.get(k.tpl, k.tpl), lon, lat)
            if merged and res.stop_of[merged[-1].tpl] == sid:
                merged[-1].dep = k.dep
                res.counts["calls_merged_same_station"] += 1
                continue
            merged.append(Call(k.tpl, k.arr, k.dep))
            res.calls_by_tpl[k.tpl] += 1
        trip.calls = merged
        res.counts["stop_times"] += len(merged)


def write_gtfs(res: Result, ref: Ref, out: Path, tz: str) -> Path:
    service = f"D{res.service_date:%Y%m%d}"
    files: dict[str, list[list]] = {
        "agency.txt": [["agency_id", "agency_name", "agency_url", "agency_timezone"]],
        "stops.txt": [["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type"]],
        "routes.txt": [["route_id", "agency_id", "route_short_name", "route_long_name",
                        "route_type"]],
        "trips.txt": [["route_id", "service_id", "trip_id", "trip_short_name"]],
        "stop_times.txt": [["trip_id", "arrival_time", "departure_time", "stop_id",
                            "stop_sequence", "pickup_type", "drop_off_type"]],
        "calendar_dates.txt": [["service_id", "date", "exception_type"],
                               [service, f"{res.service_date:%Y%m%d}", 1]],
        "feed_info.txt": [["feed_publisher_name", "feed_publisher_url", "feed_lang",
                           "feed_start_date", "feed_end_date", "feed_version"],
                          ["bristol-transit-lab (from Darwin Push Port)",
                           "https://www.nationalrail.co.uk/", "en",
                           f"{res.service_date:%Y%m%d}", f"{res.service_date:%Y%m%d}",
                           f"darwin-{res.timetable_id}"]],
    }
    for toc in sorted({t.toc for t in res.trips}):
        name, url = ref.tocs.get(toc, (toc, ""))
        files["agency.txt"].append([toc, name, url or "https://www.nationalrail.co.uk/", tz])
    for sid, (name, lon, lat) in sorted(res.stops.items()):
        files["stops.txt"].append([sid, name, f"{lat:.6f}", f"{lon:.6f}", 0])
    routes = {}
    for t in res.trips:
        o, d = res.stop_of[t.calls[0].tpl], res.stop_of[t.calls[-1].tpl]
        rid = f"{t.toc}:{o}-{d}"
        if rid not in routes:
            routes[rid] = [rid, t.toc, "", f"{res.stops[o][0]} – {res.stops[d][0]}", 2]
        files["trips.txt"].append([rid, service, t.rid, t.train_id])
        for i, k in enumerate(t.calls):
            arr = k.arr if k.arr is not None else k.dep
            dep = k.dep if k.dep is not None else k.arr
            # A trip cut at the box edge starts or ends mid-route, so only the public
            # times decide: no public departure = set down only, no arrival = pick up only.
            pickup = 1 if k.dep is None else 0
            drop = 1 if k.arr is None else 0
            files["stop_times.txt"].append([t.rid, _gtfs_time(arr), _gtfs_time(dep),
                                            res.stop_of[k.tpl], i + 1, pickup, drop])
    files["routes.txt"] += sorted(routes.values())
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in files.items():
            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerows(rows)
            z.writestr(name, buf.getvalue())
    return out


def presence(res: Result, naptan: dict, box: tuple, not_open: list[str]) -> dict:
    """Stations inside the box: every active NaPTAN station served, the not-open ones not."""
    x0, y0, x1, y1 = box
    active = {tpl for tpl, (lon, lat, _, st) in naptan.items()
              if st == "active" and x0 <= lon <= x1 and y0 <= lat <= y1}
    served = {tpl for tpl, n in res.calls_by_tpl.items() if n}
    return {
        "unserved_expected": sorted(active - served - set(not_open)),
        "served_but_not_open": sorted(set(not_open) & served),
        "served_not_in_naptan_active": sorted(served - active),
    }
