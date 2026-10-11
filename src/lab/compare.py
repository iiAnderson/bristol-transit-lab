"""
The comparison report (SPEC §8; plans/P3.md P3c-4): a scenario against another scenario
or the baseline, as a dict (``compare.json``) and as Markdown. The numbers are made in
``run.py`` and ``costs.py``; this module only lays them out, and says what they rest on.
"""
from __future__ import annotations

# parameter namespaces a comparison reads, directly or through the skims and coverage
NAMESPACES = ("routing", "skims", "periods", "scenario", "coverage", "connectivity", "car", "congestion")


def params_behind(base_params: list, cost_used: dict | None) -> dict:
    """Tagged values behind a comparison: every [PLACEHOLDER] and [MODELLED] parameter in
    the namespaces the comparison reads, and the unit costs its cost lines used."""
    out: dict[str, list[str]] = {}
    for p in base_params:
        if p.path.split(".")[0] in NAMESPACES and p.tag in ("PLACEHOLDER", "MODELLED", "CALIBRATED"):
            out.setdefault(p.tag, []).append(f"base.yaml: {p.path}")
    for tag, paths in (cost_used or {}).items():
        if tag in ("PLACEHOLDER", "MODELLED"):
            out.setdefault(tag, []).extend(f"costs.yaml: {x}" for x in paths)
    return {k: sorted(v) for k, v in sorted(out.items())}


def _n(v, unit: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and abs(v) < 100:
        return f"{v:,.2f}{unit}"
    return f"{v:,.0f}{unit}"


def _signed(v, unit: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:+,.2f}{unit}" if isinstance(v, float) and abs(v) < 100 else f"{v:+,.0f}{unit}"


def _money(v) -> str:
    return "—" if v is None else (f"£{v / 1e6:,.1f}m" if abs(v) >= 1e6 else f"£{v / 1e3:,.0f}k")


def markdown(c: dict) -> str:
    a, b = c["scenario"], c["against"]
    L = [f"# {a} against {b}", "",
         "Sketch-planning model: indicative and comparative, not for a business case. "
         f"Modelled day {c['modelled_date']}; walking at {c['walk_speed_kmh']:g} km/h on flat ground, {c['gradient']}; "
         "PT times are the median over the departure window, averaged over the scenario's timetable offsets.", ""]
    for who, cav in c["caveats"].items():
        for x in cav:
            L.append(f"> **{who}:** {x}")
    if any(c["caveats"].values()):
        L.append("")
    L += ["## Who gains and who loses", "",
          f"Residents whose time changes by {c['threshold_min']:g} minutes or more. Pairs are not people: these are "
          "residents, counted at their home output area.", "",
          "| Period | To | Better off | Worse off | Range across offsets (better) | Range (worse) |", "|---|---|---|---|---|---|"]
    for per, p in c["people"].items():
        rg = p.get("range_across_offsets") or {}
        for key, label in (("to_the_centre", f"the centre ({p['centre_zone']})"), ("to_jobs_weighted_mean_time", "jobs (weighted mean time)")):
            r1 = (rg.get(key) or {}).get("residents_faster_by_threshold")
            r2 = (rg.get(key) or {}).get("residents_slower_by_threshold")
            L.append(f"| {per} | {label} | {_n(p[key]['residents_faster_by_threshold'])} | {_n(p[key]['residents_slower_by_threshold'])} | "
                     + (f"{_n(r1['min'])} to {_n(r1['max'])}" if r1 else "one network") + " | "
                     + (f"{_n(r2['min'])} to {_n(r2['max'])}" if r2 else "one network") + " |")
    L += ["", "Worse-off residents are reported beside better-off ones in every comparison: replacing or "
          "re-timing a timetabled service moves its connections, and some of both columns is that and not the scheme.", "",
          "## Job accessibility by public transport", "",
          f"| Period | Measure | {a} | {b} | Difference |", "|---|---|---|---|---|"]
    for per, x in c["connectivity"].items():
        for key, m in x["scenario"].items():
            for stat, v in m.items():
                if stat not in ("resident_weighted_mean", "median_core_oa"):
                    continue
                d = x["difference"].get(key, {}).get(stat)
                L.append(f"| {per} | {key.replace('_', ' ')}, {stat.replace('_', ' ')} | {_n(v)} | {_n(x['against'][key][stat])} | {_signed(d)} |")
    L += ["", f"Decay-weighted jobs use β = {c['decay_beta']:g} per minute, a `[PLACEHOLDER]`: for comparing, not quoting. "
          f"The PT ÷ car ratio sets PT door to door against car in-vehicle time ({c['car_period']}) with no parking or walk, "
          "on car times that failed their P2 check: it overstates the car.", "",
          "## Frequent-service coverage", "",
          f"| Measure | {a} | {b} | Difference |", "|---|---|---|---|"]
    cov = c["coverage"]
    for key in cov["scenario"]:
        v, w = cov["scenario"][key], cov["against"][key]
        if isinstance(v, dict):
            for k2 in v:
                L.append(f"| {key.replace('_', ' ')} min, {k2} | {_n(v[k2])} | {_n(w[k2])} | {_signed(v[k2] - w[k2])} |")
        else:
            L.append(f"| {key.replace('_', ' ')} | {_n(v)} | {_n(w)} | {_signed(v - w)} |")
    L += ["", "Worse of the AM and inter-peak; a stop serves an output area inside the walking cut-off.", "", "## Cost", ""]
    for who, k in c["cost"].items():
        if k is None:
            L += [f"**{who}:** the baseline; nothing to cost.", ""]
            continue
        r, o, y = k["capex"]["raw"], k["capex"]["with_optimism_bias"], k["opex_per_year"]
        L += [f"**{who}** against {k['against']}, {k['price_base_year']} prices, low – central – high:", "",
              f"- capital: {_money(r['low'])} – {_money(r['central'])} – {_money(r['high'])}; with optimism bias (TAG stage "
              f"{k['optimism_bias_stage']}): {_money(o['low'])} – {_money(o['central'])} – {_money(o['high'])}",
              f"- operating, a year: {_money(y['low'])} – {_money(y['central'])} – {_money(y['high'])}"]
        if not k["complete"]:
            L.append(f"- **incomplete:** {len(k['not_costed'])} items have no unit cost:")
            L += [f"  - {x}" for x in k["not_costed"]]
        L += [f"- {x}" for x in k["notes"]] + ["", k["caveat"], ""]
    if c.get("cost_note"):
        L += [c["cost_note"], ""]
    L += ["## What these figures rest on", ""]
    for x in c["inherited"]:
        L.append(f"- {x}")
    for who, items in c["not_yet_modelled"].items():
        L += [f"- {who}: {x}" for x in items]
    L.append("")
    pb = c["params_behind"]
    L.append(f"Tagged values behind the comparison: {', '.join(f'{len(v)} [{k}]' for k, v in pb.items())} "
             "(full list in compare.json).")
    if pb.get("PLACEHOLDER"):
        L += ["", "`[PLACEHOLDER]` values — none of this may be published until each is resolved:", ""]
        L += [f"- {x}" for x in pb["PLACEHOLDER"]]
    L += ["", "## Files", "", "- `compare.json`: every figure above, the pair-level changes and the full parameter list",
          "- `change_oa.parquet` (GeoParquet, EPSG:4326): per output area, residents, the change in time to the centre and "
          "to jobs, jobs reachable, decay-weighted jobs and frequent-service status, by period",
          "", f"Runs: {a} skims {c['skims']['scenario']}; {b} skims {c['skims']['against']}."]
    return "\n".join(L) + "\n"
