"""
One self-contained HTML map for one or more projects (README: `lab map`).

A project is a scenario compared with a baseline: a `lab compare` run directory
(`change_oa.parquet`, `compare.json`), the attribution and reach tables made from its skims
(`attribution.py`), and the scenario files for its lines and stops. The map adds the baseline
gap map (`gapmap_od.parquet`, `gapmap_origin.geojson`). Every number is from a recorded run.

Python prepares compact JSON; one small script (`APP_JS`) draws it with Leaflet, so a sidebar
can switch projects on and off without rebuilding anything. Folium supplies only the map and
the basemap. The page needs an internet connection for the tiles; the data is inline.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
import yaml

from . import FIDELITY_DISCLAIMER

# Diverging colours: gain (blue) and loss (red) read the same for the colour-blind.
GAIN, LOSS, NEUTRAL, OTHER = "#2166ac", "#b2182b", "#999999", "#e08214"
CLICKABLE = 800          # [MODELLED] areas offered in the reach view per project: the page size grows with it
MIN_SHOWN_MIN = 1.0      # [MODELLED] changes under a minute are within the noise of a network rebuild; not drawn
GAP_LINES = 300          # [MODELLED] display cap: enough to show the pattern, light to open
LIMIT_MIN = 45.0         # the reach view's cut-off, minutes
BANDS = [(1, 2, "1–2 min"), (2, 5, "2–5 min"), (5, 10, "5–10 min"), (10, 1e9, "over 10 min")]


@dataclass
class Project:
    id: str                      # the scenario id
    title: str                   # shown in the sidebar
    against_title: str
    colour: str                  # the scheme's lines and stops
    compare_dir: Path
    c: dict                      # compare.json
    g: gpd.GeoDataFrame          # change_oa.parquet
    attr: pd.DataFrame           # attribution.compute
    reach: pd.DataFrame          # attribution.reach, for the areas that can be clicked
    files: list[Path]            # the scenario's own alignment and stop files
    data_hash: str = ""


def _colour(value: float, scale: float, good_is_positive: bool = True) -> str:
    """Blue for good, red for bad, fading to grey near zero. `scale` is the value that saturates."""
    if not value:
        return NEUTRAL
    good = (value > 0) == good_is_positive
    t = min(abs(value) / scale, 1.0) ** 0.5
    lo = (0x99, 0x99, 0x99)
    hi = tuple(int(GAIN[i:i + 2], 16) for i in (1, 3, 5)) if good else tuple(
        int(LOSS[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(lo, hi))


def _scale(s: pd.Series, q: float = 0.98) -> float:
    s = s.dropna().abs()
    s = s[s > 0]
    return float(s.quantile(q)) if len(s) else 1.0


# ---------------------------------------------------------------- scenario files

def _chain_ops(scenarios_dir: Path, scenario_id: str, against: str):
    """Yield each op of the scenario and its ancestors, stopping at `against`."""
    sid: str | None = scenario_id
    while sid and sid != against:
        spec = yaml.safe_load((scenarios_dir / f"{sid}.yaml").read_text())
        yield from spec.get("ops") or []
        sid = spec.get("parent")


def _walk(node, key_pred):
    if isinstance(node, dict):
        for k, v in node.items():
            if key_pred(k, v):
                yield k, v
            else:
                yield from _walk(v, key_pred)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key_pred)


def scheme_files(scenarios_dir: Path, scenario_id: str, against: str) -> list[Path]:
    """Alignment and stop files of the scenario's own ops, up to (not including) `against`."""
    out: list[Path] = []
    for op in _chain_ops(scenarios_dir, scenario_id, against):
        for _, v in _walk(op, lambda k, v: k in ("alignment", "stops") and isinstance(v, str)
                          and v.endswith(".geojson")):
            if scenarios_dir / v not in out:
                out.append(scenarios_dir / v)
    return out


def scheme_routes(scenarios_dir: Path, scenario_id: str, against: str) -> list[str]:
    """Route ids the scenario's own ops add or change."""
    out: list[str] = []
    for op in _chain_ops(scenarios_dir, scenario_id, against):
        for _, v in _walk(op, lambda k, v: k == "route_id" and isinstance(v, str)):
            if v not in out:
                out.append(v)
    return out


# ---------------------------------------------------------------- data for the page

def saved_bands(g: gpd.GeoDataFrame, col: str = "jobs_time_change_min_AM") -> tuple[list[tuple[str, int]], int, int]:
    """Residents by minutes saved on average to jobs; (bands, unchanged under 1 min, slower by 1+)."""
    saved = -g[col].fillna(0)
    res = g.residents
    bands = [(lab, int(res[(saved >= lo) & (saved < hi)].sum())) for lo, hi, lab in BANDS]
    return bands, int(res[saved.abs() < 1].sum()), int(res[saved <= -1].sum())


def project_rows(p: Project) -> tuple[list[list], dict]:
    """One row per output area that any layer draws, and each layer's saturation scale.

    Row: [oa, lat, lon, residents, saved (minutes faster on average to jobs; + good),
    jobs in zones faster by the threshold, of which via a scheme route, jobs newly within
    45 minutes by a real saving, change in jobs within 45 minutes (all tips), change in time to
    the centre (minutes; - good), change in 15-minute service]."""
    g = p.g.merge(p.attr, on="OA21CD", how="left").fillna(
        {"jobs_faster": 0, "jobs_faster_via_scheme": 0, "jobs_new_within": 0})
    saved = -g.jobs_time_change_min_AM.fillna(0)
    ctr = g.centre_change_min_AM.fillna(0)
    d45 = g.pt_jobs_45_AM_change.fillna(0)
    f15 = g.frequent_15_change.fillna(0)
    keep = (saved >= MIN_SHOWN_MIN) | (g.jobs_new_within > 0) | (d45 != 0) | (ctr.abs() >= MIN_SHOWN_MIN) | (f15 != 0)
    k = g[keep]
    rows = [[r.OA21CD, round(r.geometry.y, 5), round(r.geometry.x, 5), int(r.residents), round(float(s), 1),
             int(r.jobs_faster), int(r.jobs_faster_via_scheme), int(r.jobs_new_within), int(dd), round(float(cc), 1),
             int(ff)]
            for r, s, dd, cc, ff in zip(k.itertuples(), saved[keep], d45[keep], ctr[keep], f15[keep])]
    scales = {"saved": _scale(saved[saved >= MIN_SHOWN_MIN]), "new": _scale(g.jobs_new_within),
              "d45": _scale(d45), "ctr": _scale(ctr[ctr.abs() >= MIN_SHOWN_MIN])}
    return rows, scales


def project_reach(p: Project, zone_index: dict[str, int], rows: list[list]) -> tuple[dict, list]:
    """Clickable areas and, for each, [zone index, minutes before, minutes after] (null = unreachable)."""
    reach: dict[str, list] = {}
    for r in p.reach.itertuples():
        if r.zone in zone_index:
            reach.setdefault(r.oa, []).append([zone_index[r.zone], None if pd.isna(r.t0) else float(r.t0),
                                               None if pd.isna(r.t1) else float(r.t1)])
    oas = [[r[0], r[1], r[2], r[3]] for r in rows if r[0] in reach]
    return reach, oas


def scheme_features(files: list[Path]) -> list[dict]:
    """The scenario's lines and stops, trimmed to what the page draws."""
    out = []
    for f in files:
        for ft in json.loads(f.read_text())["features"]:
            props = ft.get("properties") or {}
            geom = ft["geometry"]
            if geom["type"] == "Point":
                out.append({"t": "s", "c": [round(v, 5) for v in geom["coordinates"][:2]],
                            "n": str(props.get("name", props.get("stop_id", ""))),
                            "new": not props.get("existing", False)})
            elif geom["type"] == "LineString":
                out.append({"t": "l", "c": [[round(x, 5), round(y, 5)] for x, y in geom["coordinates"]],
                            "n": f.stem})
    return out


def gap_data(gap_dir: Path) -> tuple[list[list], dict]:
    """The top flows where PT is slowest against car: [lat1, lon1, lat2, lon2, weight, colour, text]."""
    od = pd.read_parquet(gap_dir / "gapmap_od.parquet")
    od = od[od.mapped & ~od.intrazonal & ~od.no_pt].copy()
    od["weight"] = od.trips * od.tdiff_high.clip(lower=0)
    top = od.nlargest(GAP_LINES, "weight")
    org = gpd.read_file(gap_dir / "gapmap_origin.geojson")
    pts = org.set_index("LSOA21CD").geometry.representative_point()
    tmin, tmax = float(top.tdiff_high.min()), float(top.tdiff_high.max())
    wmax = float(top.trips.max())
    lines = []
    for r in top.itertuples():
        a, b = pts.get(r.o_zone), pts.get(r.d_zone)
        if a is None or b is None:
            continue
        lines.append([round(a.y, 5), round(a.x, 5), round(b.y, 5), round(b.x, 5),
                      round(1 + 5 * (r.trips / wmax) ** 0.5, 1),
                      _colour((r.tdiff_high - tmin) / max(tmax - tmin, 1e-9) + 0.05, 1.05, good_is_positive=False),
                      f"{r.o_zone} → {r.d_zone}: {r.trips:,.0f} commuters, PT {r.tdiff_high:.0f} min slower door to door"])
    return lines, {"lines": len(top), "of": int(len(od)), "commuters_shown": float(top.trips.sum()),
                   "commuters_all": float(od.trips.sum()), "run": gap_dir.name}


# ---------------------------------------------------------------- html

def _m(x: float | None) -> str:
    return "n/a" if x is None else f"£{x / 1e6:,.0f}m"


def _range(d: dict) -> str:
    return f"{d['mean']:,.0f} ({d['min']:,.0f}–{d['max']:,.0f})"


def _chart(bands, unchanged: int, slower: int, colour: str = GAIN) -> str:
    top = max([n for _, n in bands] + [1])
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0"><span style="width:78px">{lab}</span>'
        f'<span style="background:{colour};height:11px;width:{max(1, 170 * n / top):.0f}px;display:inline-block"></span>'
        f'<span>{n:,}</span></div>' for lab, n in bands)
    return (f"<div style='margin:6px 0'><b>Residents by average minutes saved to jobs (AM)</b>{rows}"
            f"<div style='color:#555'>{unchanged:,} residents change by under a minute; {slower:,} are slower by a minute or more.</div></div>")


def card(p: Project) -> str:
    """One project's scorecard: the headline figures, the chart, costs, caveats."""
    c = p.c
    sid = c["scenario"]
    pe = c["people"]["AM"]
    cost = c["cost"].get(sid, {})
    cap, opx = cost.get("capex", {}), cost.get("opex_per_year", {})
    ob = cap.get("with_optimism_bias", {})
    cov = c["coverage"]["difference"]
    rows = [
        ("Residents ≥ 2 min faster to the centre (AM)", _range(pe["to_the_centre"]["residents_faster_by_threshold"])),
        ("Residents ≥ 2 min faster to jobs (AM)", _range(pe["to_jobs_weighted_mean_time"]["residents_faster_by_threshold"])),
        ("Residents worse off, to centre / to jobs",
         f"{pe['to_the_centre']['residents_slower_by_threshold']['mean']:,.0f} / "
         f"{pe['to_jobs_weighted_mean_time']['residents_slower_by_threshold']['mean']:,.0f}"),
        ("Change in residents with a 15-min service", f"{cov['frequent_15']['residents']:+,}"),
        ("Capex, 2025 prices, with optimism bias (low / central / high)",
         f"{_m(ob.get('low'))} / {_m(ob.get('central'))} / {_m(ob.get('high'))}"),
        ("Opex a year (low / central / high)",
         f"{_m(opx.get('low'))} / {_m(opx.get('central'))} / {_m(opx.get('high'))}" if cost.get("complete", True)
         else "not costed: a rate is a placeholder"),
    ]
    trs = "".join(f"<tr><td style='padding:1px 4px 1px 0'>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>"
                  for a, b in rows)
    inherited = "".join(f"<li>{html.escape(x)}</li>" for x in c.get("inherited", []))
    caveats = "".join(f"<li>{html.escape(x)}</li>" for v in c.get("caveats", {}).values() for x in v)
    notyet = "".join(f"<li>{html.escape(x)}</li>" for v in c.get("not_yet_modelled", {}).values() for x in v)
    cost_note = html.escape(cost.get("caveat", "")) + (
        " Incomplete: some items are not costed (" + html.escape("; ".join(cost.get("not_costed", []))) + ")."
        if cost and not cost.get("complete", True) else "")
    bands, unchanged, slower = saved_bands(p.g)
    return f"""
<section data-project="{html.escape(p.id)}" style="border-top:3px solid {p.colour};margin-top:8px;padding-top:4px">
<b style="font-size:13px">{html.escape(p.title)}</b>
<div style="color:#555">{html.escape(sid)} against {html.escape(p.against_title)}</div>
<table style="border-collapse:collapse;width:100%;margin-top:4px">{trs}</table>
{_chart(bands, unchanged, slower)}
<p style="margin:4px 0">Mean (min–max) over {pe['offsets']} timetable offsets, AM 08:00–09:00, {html.escape(str(c['modelled_date']))}. {cost_note}</p>
<details><summary>Caveats</summary><ul>{caveats}{notyet}{inherited}</ul></details>
<div style="color:#777">compare run {html.escape(p.compare_dir.name)} · data {p.data_hash}</div>
</section>"""


APP_JS = r"""
window.addEventListener("load", function () {
  const D = __DATA__, map = __MAP__;
  const LIM = D.lim, THR = D.thr, MIN = D.minShown;
  const GAIN = D.gain, LOSS = D.loss, GREY = D.neutral, OTHER = D.other;
  const state = {enabled: new Set(D.projects.map(p => p.id)), metric: "saved", gap: false, scheme: true};
  const live = [];                       // layers currently on the map
  const cache = {};                      // layers built so far
  const el = id => document.getElementById(id);
  const fmt = n => Math.round(n).toLocaleString();
  const rad = (v, s) => 3 + 6 * Math.sqrt(Math.min(Math.abs(v) / (s || 1), 1));

  function dot(lat, lon, r, colour, tip) {
    return L.circleMarker([lat, lon], {radius: r, stroke: false, fill: true, fillColor: colour, fillOpacity: 0.85}).bindTooltip(tip);
  }
  const zl = [];                         // zone layers by index
  const zones = L.geoJSON(D.zones, {style: () => ({weight: 0.4, color: "#888", fillOpacity: 0, opacity: 0.35}),
                                    onEachFeature: (f, l) => { zl[f.properties.i] = l; }});

  // ---- per-project metric layers, built on first use
  const metrics = {
    saved: (p) => p.rows.filter(r => r[4] >= MIN).map(r => dot(r[1], r[2], rad(r[4], p.scales.saved), GAIN,
      r[0] + " (" + fmt(r[3]) + " residents): " + r[4].toFixed(1) + " min faster on average to jobs")),
    why: (p) => p.rows.filter(r => r[4] >= MIN).map(r => dot(r[1], r[2], rad(r[4], p.scales.saved),
      r[6] >= 0.5 * Math.max(r[5], 1) ? GAIN : OTHER,
      r[0] + ": " + r[4].toFixed(1) + " min faster; " + fmt(r[5]) + " jobs in zones " + THR + "+ min faster, " +
      Math.round(100 * r[6] / Math.max(r[5], 1)) + "% of them via a scheme route")),
    news: (p) => p.rows.filter(r => r[7] > 0).map(r => dot(r[1], r[2], rad(r[7], p.scales.new), GAIN,
      r[0] + " (" + fmt(r[3]) + " residents): +" + fmt(r[7]) + " jobs reachable in " + LIM + " min")),
    d45: (p) => p.rows.filter(r => r[8] !== 0).map(r => dot(r[1], r[2], rad(r[8], p.scales.d45), r[8] > 0 ? GAIN : LOSS,
      r[0] + " (" + fmt(r[3]) + " residents): " + (r[8] > 0 ? "+" : "") + fmt(r[8]) + " jobs reachable in " + LIM + " min")),
    ctr: (p) => p.rows.filter(r => Math.abs(r[9]) >= MIN).map(r => dot(r[1], r[2], rad(r[9], p.scales.ctr), r[9] < 0 ? GAIN : LOSS,
      r[0] + " (" + fmt(r[3]) + " residents): " + (r[9] > 0 ? "+" : "") + r[9].toFixed(1) + " min to the centre")),
    f15: (p) => p.rows.filter(r => r[10] !== 0).map(r => dot(r[1], r[2], 6, r[10] > 0 ? GAIN : LOSS, r[0] + ": " + (r[10] > 0 ? "+" : "") + r[10])),
    reach: (p) => p.reachOAs.map(o => {
      const m = L.circleMarker([o[1], o[2]], {radius: 5, color: "#000", weight: 1, fillColor: p.colour, fillOpacity: 1})
        .bindTooltip(o[0] + " (" + fmt(o[3]) + " residents): click for its reach");
      m.on("click", () => select(p, o[0], o[3]));
      return m;
    }),
  };
  function layerOf(p, key) {
    const k = p.id + "|" + key;
    if (!cache[k]) cache[k] = L.layerGroup(key === "scheme" ? schemeLayers(p) : metrics[key](p));
    return cache[k];
  }
  function schemeLayers(p) {
    return p.scheme.map(f => f.t === "l"
      ? L.polyline(f.c.map(c => [c[1], c[0]]), {color: p.colour, weight: 4, opacity: 0.9}).bindTooltip(p.title)
      : L.circleMarker([f.c[1], f.c[0]], {radius: f.new ? 6 : 4, color: "#000", weight: 1.5, fillColor: f.new ? "#fdb515" : "#fff", fillOpacity: 1})
          .bindTooltip(f.n + (f.new ? " (new)" : "")));
  }
  function gapLayer() {
    if (!cache.gap) cache.gap = L.layerGroup(D.gap.map(g => L.polyline([[g[0], g[1]], [g[2], g[3]]],
      {weight: g[4], color: g[5], opacity: 0.55}).bindTooltip(g[6])));
    return cache.gap;
  }

  // ---- click an area: before and after reach
  function paint(t0, t1) {
    const in0 = t0 !== null && t0 <= LIM, in1 = t1 !== null && t1 <= LIM;
    const faster = t0 !== null && t1 !== null && t0 - t1 >= THR;
    if (in1 && !in0 && (t0 === null || t0 - t1 >= THR)) return [GAIN, 0.75, "new"];
    if (in1 && !in0) return [GREY, 0.35, "tip"];
    if (in0 && !in1) return [LOSS, 0.6, "lost"];
    if (in0 && in1 && faster) return ["#6baed6", 0.5, "faster"];
    if (in0 && in1) return ["#c6dbef", 0.3, "same"];
    return null;
  }
  function select(p, oa, residents) {
    zl.forEach(l => l.setStyle({weight: 0.4, color: "#888", fillOpacity: 0, opacity: 0.35}));
    let before = 0, after = 0, gained = 0;
    (p.reach[oa] || []).forEach(([zi, t0, t1]) => {
      const c = paint(t0, t1), j = D.jobs[zi];
      if (t0 !== null && t0 <= LIM) before += j;
      if (t1 !== null && t1 <= LIM) after += j;
      if (c) { if (c[2] === "new") gained += j;
        zl[zi].setStyle({fillColor: c[0], fillOpacity: c[1], color: c[0], weight: 0.8, opacity: 0.9}); }
      zl[zi].unbindTooltip();
      zl[zi].bindTooltip(D.zoneNames[zi] + ": " + (t0 === null ? "n/a" : t0) + " → " + (t1 === null ? "n/a" : t1) + " min");
    });
    el("reach-info").innerHTML = "<b>" + p.title + "</b><br>" + oa + ", " + fmt(residents) + " residents<br>Jobs within " + LIM +
      " min by PT: " + fmt(before) + " before, " + fmt(after) + " after (" + (after >= before ? "+" : "") + fmt(after - before) + ").<br>" +
      "Dark blue: newly within " + LIM + " min by a real saving (" + fmt(gained) + " jobs). Mid blue: within before and after, " + THR +
      "+ min faster. Pale: within, unchanged. Grey: tipped inside by under " + THR + " min (not counted). Red: no longer within.";
  }

  // ---- the page
  function refresh() {
    live.forEach(l => map.removeLayer(l)); live.length = 0;
    const add = l => { l.addTo(map); live.push(l); };
    if (state.metric === "reach") add(zones);
    if (state.gap) add(gapLayer());
    D.projects.filter(p => state.enabled.has(p.id)).forEach(p => {
      if (!(state.metric === "f15" && !p.hasF15)) add(layerOf(p, state.metric));
      if (state.scheme) add(layerOf(p, "scheme"));
    });
    document.querySelectorAll("section[data-project]").forEach(s => { s.style.display = state.enabled.has(s.dataset.project) ? "" : "none"; });
    el("reach-box").style.display = state.metric === "reach" ? "" : "none";
    el("nothing").style.display = state.enabled.size ? "none" : "";
  }
  document.querySelectorAll("input[data-project]").forEach(i => i.addEventListener("change", () => {
    i.checked ? state.enabled.add(i.dataset.project) : state.enabled.delete(i.dataset.project); refresh(); }));
  document.querySelectorAll("input[name=metric]").forEach(i => i.addEventListener("change", () => { state.metric = i.value; refresh(); }));
  el("gap").addEventListener("change", e => { state.gap = e.target.checked; refresh(); });
  el("scheme").addEventListener("change", e => { state.scheme = e.target.checked; refresh(); });
  refresh();
});
"""

METRIC_CHOICES = [
    ("saved", "Minutes saved to jobs", "average over all jobs, AM; under a minute not drawn"),
    ("why", "Why it improved", "blue: the saving is on a scheme route; orange: elsewhere"),
    ("news", "Jobs newly within 45 min", "only zones that became 2+ min faster"),
    ("d45", "Jobs within 45 min, all changes", "includes tips under 2 min: misleading"),
    ("ctr", "Time to the centre", "minutes saved or lost"),
    ("f15", "15-minute service", "areas gaining or losing one"),
    ("reach", "Click an area: 45-minute reach", "before and after, by zone"),
]


def headline(p: Project) -> str:
    bands, _, _ = saved_bands(p.g)
    return f"{sum(v for _, v in bands):,} residents save a minute or more"


def sidebar(projects: list[Project], gap_meta: dict, has_f15: bool) -> str:
    boxes = "".join(
        f'<label style="display:block;margin:5px 0;line-height:1.3"><input type="checkbox" checked data-project="{html.escape(p.id)}">'
        f' <span style="display:inline-block;width:12px;height:12px;background:{p.colour};vertical-align:-1px;border:1px solid #000"></span>'
        f' <b>{html.escape(p.title)}</b><br><span style="color:#555;margin-left:26px">{headline(p)}</span></label>'
        for p in projects)
    radios = "".join(
        f'<label style="display:block;margin:4px 0;line-height:1.3"><input type="radio" name="metric" value="{k}"{" checked" if k == "saved" else ""}>'
        f' {html.escape(t)}<br><span style="color:#555;margin-left:22px">{html.escape(h)}</span></label>'
        for k, t, h in METRIC_CHOICES if k != "f15" or has_f15)
    return f"""
<div id="sidebar" style="position:fixed;top:10px;left:56px;z-index:9999;width:300px;max-height:92vh;overflow:auto;background:rgba(255,255,255,.96);
 font:12px/1.4 system-ui,sans-serif;padding:10px 12px;border:1px solid #888;border-radius:6px">
<b style="font-size:14px">Projects</b>
<div style="color:#555">Tick the schemes to show.</div>{boxes}
<hr style="margin:8px 0"><b style="font-size:14px">Map shows</b>{radios}
<hr style="margin:8px 0"><b style="font-size:14px">Background</b>
<label style="display:block;margin:4px 0"><input type="checkbox" id="scheme" checked> Lines and stops</label>
<label style="display:block;margin:4px 0"><input type="checkbox" id="gap"> Baseline gap map: where PT is slowest against car<br>
<span style="color:#555;margin-left:22px">top {gap_meta['lines']} of {gap_meta['of']:,} commute flows ({gap_meta['commuters_shown']:,.0f} of
{gap_meta['commuters_all']:,.0f} commuters), high car terminal times. Car times failed their P2 spot check: read the pattern, not the minutes.</span></label>
</div>"""


def right_panel(projects: list[Project]) -> str:
    cards = "".join(card(p) for p in projects)
    return f"""
<div id="panel" style="position:fixed;top:10px;right:10px;z-index:9999;width:390px;max-height:92vh;overflow:auto;background:rgba(255,255,255,.96);
 font:12px/1.4 system-ui,sans-serif;padding:10px 12px;border:1px solid #888;border-radius:6px">
<div style="color:#b2182b">{html.escape(FIDELITY_DISCLAIMER)}</div>
<div id="reach-box" style="display:none;margin:6px 0;padding:6px;background:#f3f6fa"><div id="reach-info">Click a dot (the areas with the biggest savings) to see which zones come within {LIMIT_MIN:g} minutes.</div></div>
<div id="nothing" style="display:none;margin:8px 0;color:#555">No project ticked.</div>
{cards}
<div style="color:#777;margin-top:8px">Blue = better, red = worse. The scorecards show the projects ticked on the left.</div>
</div>"""


def build(projects: list[Project], gap_dir: Path, extent: tuple, zones: gpd.GeoDataFrame,
          jobs: pd.DataFrame) -> str:
    zones = zones.reset_index(drop=True)
    zidx = {z: i for i, z in enumerate(zones.LSOA21CD)}
    zg = zones.copy()
    zg["geometry"] = zg.geometry.simplify(0.0004)
    feats = [{"type": "Feature", "geometry": json.loads(gpd.GeoSeries([r.geometry]).to_json())["features"][0]["geometry"],
              "properties": {"i": i}} for i, r in enumerate(zg.itertuples())]
    jb = dict(zip(jobs.zone, jobs.jobs))
    pdata = []
    for p in projects:
        rows, scales = project_rows(p)
        reach, oas = project_reach(p, zidx, rows)
        pdata.append({"id": p.id, "title": p.title, "colour": p.colour, "rows": rows, "scales": scales,
                      "reach": reach, "reachOAs": oas, "scheme": scheme_features(p.files),
                      "hasF15": any(r[10] != 0 for r in rows)})
    gap, gap_meta = gap_data(gap_dir)
    thr = float(projects[0].c["threshold_min"])
    data = {"projects": pdata, "zones": {"type": "FeatureCollection", "features": feats},
            "zoneNames": list(zones.LSOA21NM), "jobs": [int(jb.get(z, 0)) for z in zones.LSOA21CD],
            "gap": gap, "lim": LIMIT_MIN, "thr": thr, "minShown": MIN_SHOWN_MIN,
            "gain": GAIN, "loss": LOSS, "neutral": NEUTRAL, "other": OTHER}
    x0, y0, x1, y1 = extent
    m = folium.Map(location=[(y0 + y1) / 2, (x0 + x1) / 2], tiles=None, control_scale=True)
    m.fit_bounds([[y0, x0], [y1, x1]])
    # OSM's own tile servers refuse pages opened from disk (no Referer: HTTP 403), so the
    # basemap is Esri's light grey canvas, which needs only attribution.
    esri = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
    attr_txt = "Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors"
    for n in ("World_Light_Gray_Base", "World_Light_Gray_Reference"):
        folium.TileLayer(esri.format(n), attr=attr_txt, control=False, max_zoom=16).add_to(m)
    has_f15 = any(d["hasF15"] for d in pdata)
    m.get_root().html.add_child(folium.Element(sidebar(projects, gap_meta, has_f15) + right_panel(projects)))
    js = APP_JS.replace("__DATA__", json.dumps(data, separators=(",", ":"))).replace("__MAP__", m.get_name())
    m.get_root().script.add_child(folium.Element(js))
    names = " and ".join(p.title for p in projects)
    m.get_root().header.add_child(folium.Element(f"<title>{html.escape(names)}</title>"))
    return m.get_root().render()
