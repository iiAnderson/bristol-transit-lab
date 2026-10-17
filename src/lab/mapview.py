"""
One self-contained HTML map for a `lab compare` run (README: `lab map`).

Reads a compare run directory (`change_oa.parquet`, `compare.json`), the baseline gap map
run (`gapmap_od.parquet`, `gapmap_origin.geojson`) and the scenario files. Writes nothing
the model did not already produce: every number is from a recorded run.

Folium (Leaflet) is the renderer: one HTML file, no server. The basemap tiles need an
internet connection when the page is opened; the data is inline.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
import yaml

from . import FIDELITY_DISCLAIMER

# Diverging colours: gain (blue) and loss (red) read the same for the colour-blind.
GAIN, LOSS, NEUTRAL = "#2166ac", "#b2182b", "#999999"
MIN_SHOWN_MIN = 1.0      # [MODELLED] changes under a minute are within the noise of a network rebuild; not drawn
GAP_LINES = 300          # [MODELLED] display cap: enough to show the pattern, light to open


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


def _dots(g: gpd.GeoDataFrame, name: str, show: bool, size: pd.Series, colour, tip) -> folium.FeatureGroup:
    """One dot per output area in `g`; size 0..1 from `size`, colour and tooltip from the row."""
    fg = folium.FeatureGroup(name=name, show=show)
    for r in g.itertuples():
        folium.CircleMarker([r.geometry.y, r.geometry.x], radius=3 + 6 * float(size[r.Index]) ** 0.5,
                            color=None, weight=0, fill=True, fill_opacity=0.85,
                            fill_color=colour(r), tooltip=tip(r)).add_to(fg)
    return fg


def _norm(s: pd.Series) -> pd.Series:
    return (s.abs() / _scale(s)).clip(upper=1.0)



REACH_JS = """
window.addEventListener("load", function () {
  const Z = %(zones)s, R = %(reach)s, J = %(jobs)s, OAS = %(oas)s, THR = %(thr)s, LIM = %(lim)s;
  const grp = %(grp)s, map = %(map)s;
  const base = {weight: 0.4, color: "#888", fillOpacity: 0, opacity: 0.35};
  const layers = [];
  const zl = L.geoJSON(Z, {style: () => base, onEachFeature: (f, l) => { layers[f.properties.i] = l; }});
  grp.addLayer(zl);
  const info = document.getElementById("reach-info");
  function colour(t0, t1) {
    const in0 = t0 !== null && t0 <= LIM, in1 = t1 !== null && t1 <= LIM;
    const faster = t0 !== null && t1 !== null && t0 - t1 >= THR;
    if (in1 && (!in0 || t0 === null) && (t0 === null || t0 - t1 >= THR)) return ["%(gain)s", 0.75, "newly within"];
    if (in1 && !in0) return ["%(neutral)s", 0.35, "tipped by under the threshold"];
    if (in0 && !in1) return ["%(loss)s", 0.6, "no longer within"];
    if (in0 && in1 && faster) return ["#6baed6", 0.5, "within, faster"];
    if (in0 && in1) return ["#c6dbef", 0.3, "within, unchanged"];
    return null;
  }
  function select(oa, name) {
    layers.forEach(l => l.setStyle(base));
    let before = 0, after = 0, gained = 0;
    (R[oa] || []).forEach(([zi, t0, t1]) => {
      const c = colour(t0, t1), j = J[zi];
      if (t0 !== null && t0 <= LIM) before += j;
      if (t1 !== null && t1 <= LIM) after += j;
      if (c) { if (c[2] === "newly within") gained += j;
        layers[zi].setStyle({fillColor: c[0], fillOpacity: c[1], color: c[0], weight: 0.8, opacity: 0.9}); }
      layers[zi].bindTooltip(Z.features[zi].properties.n + ": " + (t0 === null ? "n/a" : t0) + " -> " + (t1 === null ? "n/a" : t1) + " min");
    });
    info.innerHTML = "<b>" + name + "</b><br>Jobs within " + LIM + " min by PT: " + before.toLocaleString() + " before, " +
      after.toLocaleString() + " after (" + (after - before >= 0 ? "+" : "") + (after - before).toLocaleString() + ").<br>" +
      "Dark blue: newly within " + LIM + " min by a real saving (" + gained.toLocaleString() + " jobs). Mid blue: within before and after, " +
      THR + "+ min faster. Pale: within, unchanged. Grey: tipped inside by under " + THR + " min (not counted). Red: no longer within.";
  }
  OAS.forEach(o => {
    const m = L.circleMarker([o[1], o[2]], {radius: 5, color: "#000", weight: 1, fillColor: "#fff", fillOpacity: 1})
      .bindTooltip(o[0] + " (" + o[3].toLocaleString() + " residents): click for its reach");
    m.on("click", () => select(o[0], o[0] + ", " + o[3].toLocaleString() + " residents"));
    grp.addLayer(m);
  });
  map.on("overlayremove", e => { if (e.layer === grp) layers.forEach(l => l.setStyle(base)); });
});
"""


def _reach_layer(m: folium.Map, sel: gpd.GeoDataFrame, zones: gpd.GeoDataFrame, reach: pd.DataFrame,
                 jobs: pd.DataFrame, thr: float, limit: float = 45.0) -> None:
    """Click an area marker: shade the zones reachable within `limit` minutes before and after."""
    zones = zones.reset_index(drop=True)
    idx = {z: i for i, z in enumerate(zones.LSOA21CD)}
    zg = zones.copy()
    zg["geometry"] = zg.geometry.simplify(0.0004)
    feats = [{"type": "Feature", "geometry": json.loads(gpd.GeoSeries([r.geometry]).to_json())["features"][0]["geometry"],
              "properties": {"i": i, "n": r.LSOA21NM}} for i, r in enumerate(zg.itertuples())]
    jb = dict(zip(jobs.zone, jobs.jobs))
    jobs_by_idx = [int(jb.get(z, 0)) for z in zones.LSOA21CD]
    data: dict[str, list] = {}
    for r in reach.itertuples():
        if r.zone in idx:
            data.setdefault(r.oa, []).append([idx[r.zone], None if pd.isna(r.t0) else float(r.t0),
                                              None if pd.isna(r.t1) else float(r.t1)])
    oas = [[r.OA21CD, round(r.geometry.y, 5), round(r.geometry.x, 5), int(r.residents)]
           for r in sel.itertuples() if r.OA21CD in data]
    fg = folium.FeatureGroup(name=f"Click an area: {limit:g}-minute reach before and after", show=False)
    fg.add_to(m)
    js = REACH_JS % {"zones": json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")),
                     "reach": json.dumps(data, separators=(",", ":")), "jobs": json.dumps(jobs_by_idx),
                     "oas": json.dumps(oas, separators=(",", ":")), "thr": thr, "lim": limit,
                     "grp": fg.get_name(), "map": m.get_name(), "gain": GAIN, "neutral": NEUTRAL, "loss": LOSS}
    m.get_root().script.add_child(folium.Element(js))


def _gap_layer(gap_dir: Path, show: bool) -> tuple[folium.FeatureGroup, dict]:
    od = pd.read_parquet(gap_dir / "gapmap_od.parquet")
    od = od[od.mapped & ~od.intrazonal & ~od.no_pt].copy()
    od["weight"] = od.trips * od.tdiff_high.clip(lower=0)
    top = od.nlargest(GAP_LINES, "weight")
    org = gpd.read_file(gap_dir / "gapmap_origin.geojson")
    pts = org.set_index("LSOA21CD").geometry.representative_point()
    fg = folium.FeatureGroup(name="Gap map: PT slower than car (baseline B2026, indicative)", show=show)
    tmin, tmax = float(top.tdiff_high.min()), float(top.tdiff_high.max())
    wmax = float(top.trips.max())
    for r in top.itertuples():
        a, b = pts.get(r.o_zone), pts.get(r.d_zone)
        if a is None or b is None:
            continue
        folium.PolyLine(
            [[a.y, a.x], [b.y, b.x]], weight=1 + 5 * (r.trips / wmax) ** 0.5, opacity=0.55,
            color=_colour((r.tdiff_high - tmin) / max(tmax - tmin, 1e-9) + 0.05, 1.05, good_is_positive=False),
            tooltip=f"{r.o_zone} → {r.d_zone}: {r.trips:,.0f} commuters, PT {r.tdiff_high:.0f} min "
                    f"slower door to door").add_to(fg)
    meta = {"lines": len(top), "of": int(len(od)), "commuters_shown": float(top.trips.sum()),
            "commuters_all": float(od.trips.sum())}
    return fg, meta


def _scheme_layer(files: list[Path]) -> folium.FeatureGroup:
    fg = folium.FeatureGroup(name="Scheme: new lines and stops", show=True)
    for p in files:
        for f in json.loads(p.read_text())["features"]:
            geom, props = f["geometry"], f.get("properties", {})
            if geom["type"] == "Point":
                x, y = geom["coordinates"][:2]
                new = not props.get("existing", False)
                folium.CircleMarker([y, x], radius=6 if new else 4, color="#000", weight=1.5,
                                    fill=True, fill_color="#fdb515" if new else "#fff", fill_opacity=1,
                                    tooltip=html.escape(str(props.get("name", props.get("stop_id", ""))))
                                    + (" (new)" if new else "")).add_to(fg)
            elif geom["type"] in ("LineString", "MultiLineString"):
                folium.GeoJson(f, style_function=lambda _: {"color": "#000", "weight": 4, "opacity": 0.9},
                               tooltip=p.stem).add_to(fg)
    return fg


def _m(x: float | None) -> str:
    return "n/a" if x is None else f"£{x / 1e6:,.0f}m"


def _range(d: dict) -> str:
    return f"{d['mean']:,.0f} ({d['min']:,.0f}–{d['max']:,.0f})"


BANDS = [(1, 2, "1–2 min"), (2, 5, "2–5 min"), (5, 10, "5–10 min"), (10, 1e9, "over 10 min")]


def saved_bands(g: gpd.GeoDataFrame, col: str = "jobs_time_change_min_AM") -> tuple[list[tuple[str, int]], int, int]:
    """Residents by minutes saved on average to jobs; (bands, unchanged under 1 min, slower by 1+)."""
    saved = -g[col].fillna(0)
    res = g.residents
    bands = [(lab, int(res[(saved >= lo) & (saved < hi)].sum())) for lo, hi, lab in BANDS]
    return bands, int(res[saved.abs() < 1].sum()), int(res[saved <= -1].sum())


def _chart(bands, unchanged: int, slower: int) -> str:
    top = max([n for _, n in bands] + [1])
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0"><span style="width:78px">{lab}</span>'
        f'<span style="background:{GAIN};height:11px;width:{max(1, 170 * n / top):.0f}px;display:inline-block"></span>'
        f'<span>{n:,}</span></div>' for lab, n in bands)
    return (f"<div style='margin:6px 0'><b>Residents by average minutes saved to jobs (AM)</b>{rows}"
            f"<div style='color:#555'>{unchanged:,} residents change by under a minute; {slower:,} are slower by a minute or more.</div></div>")


def panel(c: dict, run_label: str, data_hash: str, gap_meta: dict, gap_run: str, chart: str = "") -> str:
    sid, ag = c["scenario"], c["against"]
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
         f"{_m(opx.get('low'))} / {_m(opx.get('central'))} / {_m(opx.get('high'))}"),
    ]
    trs = "".join(f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>" for a, b in rows)
    inherited = "".join(f"<li>{html.escape(x)}</li>" for x in c.get("inherited", []))
    caveats = "".join(f"<li>{html.escape(x)}</li>" for k, v in c.get("caveats", {}).items() for x in v)
    cost_note = html.escape(cost.get("caveat", "")) + (" Incomplete: some items not costed." if cost and not cost.get("complete", True) else "")
    return f"""
<div id="panel" style="position:fixed;top:10px;right:10px;z-index:9999;width:380px;max-height:92vh;overflow:auto;
 background:rgba(255,255,255,.96);font:12px/1.4 system-ui,sans-serif;padding:10px 12px;border:1px solid #888;border-radius:6px">
<b style="font-size:14px">{html.escape(sid)} against {html.escape(ag)}</b>
<div style="color:#b2182b;margin:4px 0">{html.escape(FIDELITY_DISCLAIMER)}</div>
<table style="border-collapse:collapse;width:100%">{trs}</table>
{chart}
<div id="reach-info" style="margin:6px 0;color:#333">Tick “Click an area” and click a white dot to see which zones come within 45 minutes.</div>
<p style="margin:6px 0">Mean (min–max) over {pe['offsets']} timetable offsets, AM 08:00–09:00, {html.escape(str(c['modelled_date']))}.
Blue = better, red = worse. Costs: {cost_note}</p>
<details><summary>Gap map (baseline, car times indicative)</summary>
<p>Top {gap_meta['lines']} of {gap_meta['of']:,} commute flows by commuters × door-to-door minutes PT is slower than car
(high car terminal times). They carry {gap_meta['commuters_shown']:,.0f} of {gap_meta['commuters_all']:,.0f} commuters.
Car times failed their own P2 spot check, so read the ranking, not the minutes. Run {html.escape(gap_run)}.</p></details>
<details><summary>Caveats and inherited fails</summary><ul>{caveats}{inherited}</ul></details>
<div style="color:#555;margin-top:6px">compare run {html.escape(run_label)} · data {data_hash}</div></div>"""


def build(compare_dir: Path, gap_dir: Path, scenarios_dir: Path, data_hash: str, extent: tuple,
          attr: pd.DataFrame, zones: gpd.GeoDataFrame, reach: pd.DataFrame, jobs: pd.DataFrame,
          show_gap: bool = False) -> str:
    c = json.loads((compare_dir / "compare.json").read_text())
    g = gpd.read_parquet(compare_dir / "change_oa.parquet").merge(attr, on="OA21CD", how="left").fillna(
        {"jobs_faster": 0, "jobs_faster_via_scheme": 0, "jobs_new_within": 0})
    thr = float(c["threshold_min"])
    x0, y0, x1, y1 = extent
    m = folium.Map(location=[(y0 + y1) / 2, (x0 + x1) / 2], tiles=None, control_scale=True)
    m.fit_bounds([[y0, x0], [y1, x1]])
    # OSM's own tile servers refuse pages opened from disk (no Referer: HTTP 403), so the
    # basemap is Esri's light grey canvas, which needs only attribution.
    esri = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
    attr_txt = "Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors"
    for n in ("World_Light_Gray_Base", "World_Light_Gray_Reference"):
        folium.TileLayer(esri.format(n), attr=attr_txt, control=False, max_zoom=16).add_to(m)

    saved = -g.jobs_time_change_min_AM.fillna(0)            # minutes saved on average to jobs; + = faster
    real = g[saved >= MIN_SHOWN_MIN]                          # sub-minute changes are noise: not drawn
    sv = saved[real.index]
    _dots(real, f"Minutes saved to jobs (average over all jobs, AM; under {MIN_SHOWN_MIN:g} min not drawn)",
          not show_gap, _norm(sv), lambda r: GAIN,
          lambda r: f"{r.OA21CD} ({r.residents:,} residents): {saved[r.Index]:.1f} min faster on average to jobs").add_to(m)
    mostly = real.jobs_faster_via_scheme >= 0.5 * real.jobs_faster.clip(lower=1)
    _dots(real, "Why: blue = saving is on a scheme route, orange = elsewhere (side effect or noise)", False,
          _norm(sv), lambda r: GAIN if mostly[r.Index] else "#e08214",
          lambda r: f"{r.OA21CD}: {saved[r.Index]:.1f} min faster; {r.jobs_faster:,.0f} jobs in zones "
                    f"≥{thr:g} min faster, {100 * r.jobs_faster_via_scheme / max(r.jobs_faster, 1):.0f}% of them via a "
                    f"scheme route").add_to(m)
    new = g[g.jobs_new_within > 0]
    _dots(new, f"Jobs newly within 45 min by PT, zones ≥{thr:g} min faster only", False,
          _norm(new.jobs_new_within), lambda r: GAIN,
          lambda r: f"{r.OA21CD} ({r.residents:,} residents): {r.jobs_new_within:+,.0f} jobs reachable in 45 min").add_to(m)
    allc = g[g.pt_jobs_45_AM_change.fillna(0) != 0]
    _dots(allc, "Jobs within 45 min, change, incl. tips under the threshold (misleading)", False,
          _norm(allc.pt_jobs_45_AM_change), lambda r: _colour(r.pt_jobs_45_AM_change, 1),
          lambda r: f"{r.OA21CD} ({r.residents:,} residents): {r.pt_jobs_45_AM_change:+,.0f} jobs reachable in 45 min").add_to(m)
    cc = g[g.centre_change_min_AM.fillna(0).abs() >= MIN_SHOWN_MIN]
    _dots(cc, "Time to the centre, change (AM)", False, _norm(cc.centre_change_min_AM),
          lambda r: _colour(r.centre_change_min_AM, 1, good_is_positive=False),
          lambda r: f"{r.OA21CD} ({r.residents:,} residents): {r.centre_change_min_AM:+.1f} min to the centre").add_to(m)
    if g["frequent_15_change"].fillna(0).ne(0).any():
        f15 = g[g.frequent_15_change.fillna(0) != 0]
        _dots(f15, "15-minute service, change", False, pd.Series(1.0, index=f15.index),
              lambda r: _colour(r.frequent_15_change, 1),
              lambda r: f"{r.OA21CD}: {r.frequent_15_change:+d}").add_to(m)
    _reach_layer(m, g, zones, reach, jobs, thr)
    gl, meta = _gap_layer(gap_dir, show_gap)
    gl.add_to(m)
    _scheme_layer(scheme_files(scenarios_dir, c["scenario"], c["against"])).add_to(m)
    folium.LayerControl(collapsed=False, position="topleft").add_to(m)
    bands, unchanged, slower = saved_bands(g)
    m.get_root().html.add_child(folium.Element(
        panel(c, compare_dir.name, data_hash, meta, gap_dir.name, _chart(bands, unchanged, slower))))
    m.get_root().header.add_child(folium.Element(f"<title>{html.escape(c['scenario'])} against {html.escape(c['against'])}</title>"))
    return m.get_root().render()
