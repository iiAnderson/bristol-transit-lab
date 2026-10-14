"""
Costs of a scenario (SPEC §8.3; plans/P3.md P3c-2) from the unit cost library.

Pure functions: quantities come from the generator's op reports and the feeds, unit
costs from ``params/costs.yaml``. Every line carries its unit cost at the price base, the
tag and source of that unit cost, and its optimism-bias category; totals are low,
central and high, raw and with optimism bias. A unit cost with no value is not an
error: its line is listed under ``not_costed`` and the result says it is incomplete.

Rules taken from TAG unit A1.2 (see the head of the library): optimism bias on the base
cost without risk or contingency, part by part; no contingency reported.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import params

# GTFS route_type → the mode whose operating rate applies
OPEX_MODE = {"0": "tram", "1": "tram", "2": "heavy_rail", "3": "bus"}
ALIAS = {"light_rail": "tram", "metro": "heavy_rail"}       # modes costed as another


class CostError(ValueError):
    pass


class Library:
    """The unit cost library, with rebasing to its price base."""

    def __init__(self, doc: dict, name: str = "costs.yaml"):
        self.doc, self.name = doc, name
        self.base = doc["_meta"]["price_base_year"]
        ix = doc["indices"]
        self.capex_ix = {int(k): float(v) for k, v in ix["construction_infrastructure"]["value"].items()}
        self.gdp = {int(k): float(v) for k, v in ix["gdp_deflator"]["value"].items()}
        self.real = float(ix["construction_real_growth_before_2014"]["value"])
        for series, what in ((self.capex_ix, "construction index"), (self.gdp, "GDP deflator")):
            if self.base not in series:
                raise CostError(f"{name}: the {what} has no value for the price base {self.base}")

    @classmethod
    def load(cls, path: Path) -> "Library":
        params.load(path)                                   # every value tagged, or an error
        return cls(yaml.safe_load(path.read_text()), path.name)

    def node(self, path: str) -> dict:
        n = self.doc
        for k in path.split("."):
            if not isinstance(n, dict) or k not in n:
                raise CostError(f"{self.name}: no entry {path}")
            n = n[k]
        if not isinstance(n, dict) or "value" not in n:
            raise CostError(f"{self.name}: {path} is not a value")
        return n

    def factor(self, price_year: int, index: str = "construction") -> float:
        """What a pound of ``price_year`` is at the price base. Capital costs follow the
        construction index, and before it starts the GDP deflator plus TAG's real
        construction growth; operating costs follow the GDP deflator."""
        if index == "gdp":
            if price_year not in self.gdp:
                raise CostError(f"{self.name}: no GDP deflator for {price_year}")
            return self.gdp[self.base] / self.gdp[price_year]
        first = min(self.capex_ix)
        if price_year >= first:
            if price_year not in self.capex_ix:
                raise CostError(f"{self.name}: no construction index for {price_year}")
            return self.capex_ix[self.base] / self.capex_ix[price_year]
        if price_year not in self.gdp:
            raise CostError(f"{self.name}: no GDP deflator for {price_year}")
        early = self.gdp[first] / self.gdp[price_year] * (1 + self.real) ** (first - price_year)
        return early * self.capex_ix[self.base] / self.capex_ix[first]

    def number(self, path: str) -> float:
        """A plain (non-money) value."""
        return self.node(path)["value"]

    def unit(self, path: str) -> dict:
        """A money entry at the price base: low, central, high (None where the library
        has no value), with its tag, source and optimism-bias category."""
        n = self.node(path)
        out = {"path": path, "tag": n["tag"], "unit": n.get("unit"), "ob_category": n.get("ob_category"), "bound": n.get("bound"),
               "source": " ".join(str(n.get("source") or n.get("reasoning") or n.get("note") or "").split()),
               "price_year": n.get("price_year"), "central": None, "low": None, "high": None}
        if n["value"] is None:
            return out
        nums = [n["value"], *(n.get("range") or [])]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nums):
            raise CostError(f"{self.name}: {path}: value and range must be numbers, got {nums!r} "
                            "(YAML reads 1.0e6 as text: write 1.0e+6)")
        f = self.factor(n["price_year"], n.get("index", "construction")) if n.get("price_year") else 1.0
        lo, hi = n.get("range") or (n["value"], n["value"])
        if not lo <= n["value"] <= hi:
            raise CostError(f"{self.name}: {path}: the value is outside its range")
        return out | {"central": n["value"] * f, "low": lo * f, "high": hi * f, "rebase_factor": round(f, 4)}

    def optimism_bias(self, category: str, stage: int | None = None) -> float:
        stage = stage or int(self.number("optimism_bias.default_stage"))
        v = self.node(f"optimism_bias.{category}")["value"]
        if not 1 <= stage <= len(v):
            raise CostError(f"{self.name}: optimism bias stage {stage} is not one of 1–{len(v)}")
        return float(v[stage - 1])


# ------------------------------------------------------------------ quantities

def tunnel_runs(legs: list[dict]) -> list[dict]:
    """Runs of tunnel along a line, from its legs in order: consecutive legs with any
    tunnel form one run of the tunnel's length. Each end of a run has a portal unless the
    run reaches the end of the line on a leg that is all tunnel (an underground
    terminus). Where inside a part-tunnel leg the tunnel lies is not known, nor needed."""
    def whole(lg):
        return lg["types_m"].get("tunnel", 0.0) >= 0.999 * sum(lg["types_m"].values())
    runs, i, n = [], 0, len(legs)
    while i < n:
        if legs[i]["types_m"].get("tunnel", 0.0) <= 0:
            i += 1
            continue
        j = i
        while j + 1 < n and legs[j + 1]["types_m"].get("tunnel", 0.0) > 0:
            j += 1
        portals = (0 if i == 0 and whole(legs[i]) else 1) + (0 if j == n - 1 and whole(legs[j]) else 1)
        runs.append({"km": sum(lg["types_m"]["tunnel"] for lg in legs[i:j + 1]) / 1000, "portals": portals})
        i = j + 1
    return runs


def service_quantities(rep: dict, mode: str, stops: list[dict], headways: dict | None) -> dict:
    """What one generated service or extension builds and runs. ``rep`` is the
    generator's report for it (``legs``, and ``end_to_end_min`` or ``extension_min``);
    ``stops`` the stop records in line order (``stop_id``, ``existing``, optional
    ``station_type``); ``headways`` minutes by period, or None where the op gives none."""
    legs = rep["legs"]
    km: dict[str, float] = {}
    for lg in legs:
        for t, m in lg["types_m"].items():
            km[t] = km.get(t, 0.0) + m / 1000
    under = set()
    for lg in legs:
        if lg["types_m"].get("tunnel", 0.0) > 0.5 * sum(lg["types_m"].values()):
            under |= {lg["from"], lg["to"]}
    new = []
    for s in stops:
        if s.get("existing"):
            continue
        new.append({"stop_id": s["stop_id"], "underground": s["stop_id"] in under, "station_type": s.get("station_type")})
    ends = rep.get("end_to_end_min") or {"fwd": rep["extension_min"]["out"], "rev": rep["extension_min"]["back"]}
    return {"mode": mode, "km_by_type": km, "tunnel_runs": tunnel_runs(legs), "new_stops": new,
            "one_way_min": ends, "peak_headway_min": min(headways.values()) if headways else None,
            "is_extension": "extension_min" in rep}


def fleet(q: dict, lib: Library) -> dict:
    """Vehicles (or units) a service needs in service: ceil(round trip ÷ peak headway).
    For an extension: the extra round-trip time over the headway, rounded up — an upper
    bound, since slack in today's diagram may absorb it. Spares are added once per mode
    over the whole scenario (``with_spares``), not service by service."""
    if not q["peak_headway_min"]:
        return {"in_service": None, "note": "no headway given: fleet not estimated"}
    lay = float(lib.number("fleet.layover_min"))
    run = q["one_way_min"]["fwd"] + q["one_way_min"]["rev"]
    rt = run if q["is_extension"] else run + 2 * lay
    n = math.ceil(rt / q["peak_headway_min"] - 1e-9)
    return {"round_trip_min": round(rt, 1), "peak_headway_min": q["peak_headway_min"], "in_service": n,
            "note": "extra units for the extension: an upper bound" if q["is_extension"] else
                    f"round trip includes {lay:g} min at each end"}


def with_spares(services: list[dict], lib: Library) -> dict[str, dict]:
    """Fleet by mode over the scenario: units in service, and with spares."""
    out: dict[str, dict] = {}
    for q in services:
        n = (q.get("fleet") or {}).get("in_service")
        if n:
            m = ALIAS.get(q["mode"], q["mode"])
            out.setdefault(m, {"in_service": 0})["in_service"] += n
    for v in out.values():
        v["with_spares"] = math.ceil(v["in_service"] * (1 + float(lib.number("fleet.spare_ratio"))) - 1e-9)
    return out


def feed_km(feeds: dict) -> pd.Series:
    """Weekday vehicle-km by GTFS route type, from stop-to-stop straight lines: a lower
    bound on distance run. One count per journey (``original_trip_id``)."""
    out: dict[str, float] = {}
    for f in feeds.values():
        if not len(f["trips"]):
            continue
        tr = f["trips"].merge(f["routes"][["route_id", "route_type"]], on="route_id")
        key = tr.original_trip_id if "original_trip_id" in tr else tr.trip_id
        tr = tr.assign(journey=key.fillna(tr.trip_id))
        st = f["stop_times"].merge(tr[["trip_id", "journey", "route_type"]], on="trip_id")
        s = f["stops"].drop_duplicates("stop_id").set_index("stop_id")
        st = st.assign(seq=st.stop_sequence.astype(int), lat=st.stop_id.map(s.stop_lat.astype(float)),
                       lon=st.stop_id.map(s.stop_lon.astype(float)))
        # a journey split into copies: each stop once, in time order
        st = st.sort_values(["journey", "departure_time", "seq"]).drop_duplicates(["journey", "stop_id", "departure_time"])
        la, lo = np.radians(st.lat.to_numpy()), np.radians(st.lon.to_numpy())
        same = st.journey.to_numpy()[1:] == st.journey.to_numpy()[:-1]
        h = np.sin((la[1:] - la[:-1]) / 2) ** 2 + np.cos(la[1:]) * np.cos(la[:-1]) * np.sin((lo[1:] - lo[:-1]) / 2) ** 2
        d = 2 * 6371.0088 * np.arcsin(np.sqrt(h)) * same
        for rt, v in pd.Series(d, index=st.route_type.to_numpy()[1:]).groupby(level=0).sum().items():
            out[rt] = out.get(rt, 0.0) + float(v)
    return pd.Series(out, dtype=float)


# ------------------------------------------------------------------ costs

def _line(item: str, qty: float, unit_name: str, u: dict, lib: Library, stage: int | None, per: str = "capex") -> dict:
    row = {"item": item, "quantity": round(qty, 3), "per": unit_name, "unit_cost_path": u["path"], "tag": u["tag"],
           "source": u["source"], "unit_cost": u["central"], "kind": per}
    if u["central"] is None:
        return row | {"cost": None}
    if u.get("bound") == "upper":
        # an upper bound only (plans/P3.md §8, P3c stop — decided, item 2): no low and no
        # central, so neither can be quoted for a scheme that includes it
        row |= {"upper_bound_only": True, "unit_cost": None, "low": None, "cost": None, "high": u["high"] * qty}
        if per == "capex":
            ob = lib.optimism_bias(u["ob_category"], stage) if u["ob_category"] else 0.0
            row |= {"ob_category": u["ob_category"], "ob": ob, "low_with_ob": None, "cost_with_ob": None,
                    "high_with_ob": row["high"] * (1 + ob)}
        return row
    row |= {"low": u["low"] * qty, "cost": u["central"] * qty, "high": u["high"] * qty}
    if per == "capex":
        ob = lib.optimism_bias(u["ob_category"], stage) if u["ob_category"] else 0.0
        row |= {"ob_category": u["ob_category"], "ob": ob, "low_with_ob": row["low"] * (1 + ob),
                "cost_with_ob": row["cost"] * (1 + ob), "high_with_ob": row["high"] * (1 + ob)}
    return row


def capex(services: list[dict], lib: Library, stage: int | None = None) -> tuple[list[dict], list[str]]:
    """Capital lines for the scenario's services (each ``service_quantities`` with its
    ``fleet`` under ``"fleet"`` and a ``name``), and notes on what is not in them."""
    lines, notes = [], []
    depot_modes = set()
    for q in services:
        mode = ALIAS.get(q["mode"], q["mode"])
        nm = q.get("name", mode)
        for t, k in sorted(q["km_by_type"].items()):
            if k <= 0:
                continue
            if t == "existing_rail":
                notes.append(f"{nm}: {k:.2f} km on existing railway: no works costed there (electrification, "
                             "doubling and junction works are not scenario ops yet)")
            elif t == "tunnel":
                continue
            else:
                lines.append(_line(f"{nm}: new {t.replace('_', ' ')} way", k, "route-km",
                                   lib.unit(f"capex_per_route_km.{mode}.{t}"), lib, stage))
        for i, r in enumerate(q["tunnel_runs"]):
            tn = f"{nm}: tunnel {i + 1}"
            wk = float(lib.number("tunnel.weeks_per_km"))
            for part, qty, per in (("twin_bore_construction", r["km"], "route-km"), ("spoil_disposal", r["km"], "route-km"),
                                   ("systems", r["km"], "route-km"), ("time_related_per_week", r["km"] * wk, "weeks"),
                                   ("fixed_per_drive", 1, "drive"), ("tbm_each", 2, "machines"),
                                   ("portal_each", r["portals"], "portals"),
                                   ("vent_shaft_each", math.floor(r["km"] / float(lib.number("tunnel.vent_shaft_spacing_km"))),
                                    "shafts")):
                if qty:
                    lines.append(_line(f"{tn}: {part.replace('_', ' ')}", qty, per, lib.unit(f"tunnel.{part}"), lib, stage))
            for part, what in (("track_per_track_km", "track"), ("electrification_per_track_km", "electrification")):
                lines.append(_line(f"{tn}: {what}", 2 * r["km"], "track-km", lib.unit(f"tunnel.{part}"), lib, stage))
        counts: dict[str, int] = {}
        for s in q["new_stops"]:
            if mode == "heavy_rail":
                kind = s["station_type"] or (lib.number("stations.default_underground") if s["underground"] else "surface")
            else:
                kind = s["station_type"] or "at_grade"
                if s["underground"] and not s["station_type"]:
                    notes.append(f"{nm}: stop {s['stop_id']} is under ground: no underground {mode} station cost in the library")
            counts[kind] = counts.get(kind, 0) + 1
        for kind, n in sorted(counts.items()):
            if mode == "bus":
                notes.append(f"{nm}: {n} new bus stops not costed")
                continue
            lines.append(_line(f"{nm}: {n} new stations ({kind.replace('_', ' ')})", n, "stations",
                               lib.unit(f"stations.{mode}.{kind}"), lib, stage))
            if mode == "heavy_rail" and kind == "surface":
                lines.append(_line(f"{nm}: access and client works for {n} new stations", n, "stations",
                                   lib.unit("stations.heavy_rail.access_and_client_works"), lib, stage))
        if mode in ("tram", "brt") and not q["is_extension"]:
            depot_modes.add(mode)
    for mode, fl in sorted(with_spares(services, lib).items()):
        if mode == "heavy_rail":
            notes.append(f"heavy rail: {fl['with_spares']} units leased ({fl['in_service']} in service), in operating "
                         "cost; no purchase costed")
        elif mode == "bus":
            notes.append(f"bus: {fl['with_spares']} buses: carried in the operating cost per km")
        else:
            lines.append(_line(f"{mode}: {fl['with_spares']} vehicles ({fl['in_service']} in service)", fl["with_spares"],
                               "vehicles", lib.unit(f"fleet.{mode}_vehicle"), lib, stage))
    for mode in sorted(depot_modes):
        lines.append(_line(f"depot ({mode})", 1, "depot", lib.unit("depot.any"), lib, stage))
    return lines, notes


def opex(km_parent: pd.Series, km_scenario: pd.Series, services: list[dict], lib: Library) -> tuple[list[dict], list[str]]:
    """Annual operating-cost lines: the change in weekday vehicle-km by mode at the
    mode's rate, and for heavy rail the units, crews, stations and tunnel added."""
    lines, notes = [], ["vehicle-km are stop-to-stop straight lines, a lower bound on distance run"]
    year = float(lib.number("opex.annualisation"))
    d = km_scenario.sub(km_parent, fill_value=0.0)
    by_mode: dict[str, float] = {}
    for rt, v in d.items():
        if abs(v) < 1e-6:
            continue
        m = OPEX_MODE.get(str(rt))
        if m is None:
            notes.append(f"route type {rt}: {v:+.0f} vehicle-km a weekday not costed (no mode for it)")
            continue
        by_mode[m] = by_mode.get(m, 0.0) + float(v)
    for m, v in sorted(by_mode.items()):
        if abs(v) < 1e-6:
            continue
        path = f"opex.{m}." + ("per_train_km" if m == "heavy_rail" else "per_vehicle_km")
        lines.append(_line(f"{m}: change in vehicle-km ({v:+,.0f} a weekday)", v * year, "vehicle-km a year",
                           lib.unit(path), lib, None, "opex"))
    rail = with_spares(services, lib).get("heavy_rail", {})
    units, crews = rail.get("with_spares", 0), rail.get("in_service", 0)
    stations = sum(len(q["new_stops"]) for q in services if ALIAS.get(q["mode"], q["mode"]) == "heavy_rail")
    tunnel = sum(r["km"] for q in services for r in q["tunnel_runs"])
    for name, qty, per, path in (("heavy rail: units leased", units, "units", "opex.heavy_rail.lease_per_unit_year"),
                                 ("heavy rail: train crew", crews, "units in service", "opex.heavy_rail.staff_per_unit_year"),
                                 ("heavy rail: new stations", stations, "stations", "opex.heavy_rail.station_per_year"),
                                 ("tunnel maintenance", tunnel, "km", "opex.tunnel_maintenance_per_km_year")):
        if qty:
            lines.append(_line(name, qty, per, lib.unit(path), lib, None, "opex"))
    return lines, notes


def summarise(capex_lines: list[dict], opex_lines: list[dict], notes: list[str], lib: Library, stage: int | None = None) -> dict:
    bounded = [x for x in capex_lines + opex_lines if x.get("upper_bound_only")]

    def total(lines, keys):
        out = {}
        for k in keys:
            done = [x[k] for x in lines if x.get(k) is not None]
            # a central figure is withheld when any line is an upper bound only; the low
            # then leaves those lines out and says so
            out[k] = None if (k.startswith("cost") and any(x.get("upper_bound_only") for x in lines)) else round(sum(done))
        return out
    not_costed = [f"{x['item']} ({x['quantity']:g} {x['per']}): no unit cost — {x['unit_cost_path']} [{x['tag']}]"
                  for x in capex_lines + opex_lines if x["cost"] is None and not x.get("upper_bound_only")]
    if bounded:
        notes = notes + [f"{x['item']}: an upper bound only (up to £{x['high'] / 1e6:,.0f}m); no central figure is given "
                         "for this scheme, and its low leaves these lines out" for x in bounded]
    used = {}
    for x in capex_lines + opex_lines:
        used.setdefault(x["tag"], set()).add(x["unit_cost_path"])
    for p in ("fleet.spare_ratio", "fleet.layover_min", "opex.annualisation", "optimism_bias.default_stage"):
        used.setdefault(lib.node(p)["tag"], set()).add(p)
    cap = total(capex_lines, ["low", "cost", "high", "low_with_ob", "cost_with_ob", "high_with_ob"])
    return {"price_base_year": lib.base, "optimism_bias_stage": stage or int(lib.number("optimism_bias.default_stage")),
            "capex": {"raw": {"low": cap["low"], "central": cap["cost"], "high": cap["high"]},
                      "with_optimism_bias": {"low": cap["low_with_ob"], "central": cap["cost_with_ob"], "high": cap["high_with_ob"]},
                      "renewals_over_60_years_central": (None if cap["cost"] is None else
                                                 round(cap["cost"] * float(lib.number("opex.renewals_share_of_capex")))),
                      "lines": capex_lines},
            "opex_per_year": {**{k if k != "cost" else "central": v for k, v in total(opex_lines, ["low", "cost", "high"]).items()},
                              "lines": opex_lines},
            "complete": not not_costed, "not_costed": not_costed, "notes": notes,
            "unit_costs_used_by_tag": {t: sorted(v) for t, v in sorted(used.items())},
            "caveat": "Sketch costs: unit rates from a handful of published schemes, rebased; capital costs are ±50% at "
                      "best. Optimism bias per TAG A1.2 on base costs without risk or contingency. No land, no "
                      "contingency, no works to existing railway."}
