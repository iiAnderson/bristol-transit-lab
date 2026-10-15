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


def scheme_files(scenarios_dir: Path, scenario_id: str, against: str) -> list[Path]:
    """Alignment and stop files of the scenario's own ops, up to (not including) `against`."""
    out: list[Path] = []
    sid: str | None = scenario_id
    while sid and sid != against:
        spec = yaml.safe_load((scenarios_dir / f"{sid}.yaml").read_text())

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("alignment", "stops") and isinstance(v, str) and v.endswith(".geojson"):
                        p = scenarios_dir / v
                        if p not in out:
                            out.append(p)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(spec.get("ops") or [])
        sid = spec.get("parent")
    return out


def _oa_layer(g: gpd.GeoDataFrame, col: str, name: str, unit: str, good_is_positive: bool,
              show: bool, integer: bool = True) -> folium.FeatureGroup:
    fg = folium.FeatureGroup(name=name, show=show)
    d = g[g[col].fillna(0) != 0]
    scale = _scale(d[col])
    for r in d.itertuples():
        v = getattr(r, col)
        folium.CircleMarker(
            [r.geometry.y, r.geometry.x], radius=4 + 5 * min(abs(v) / scale, 1.0) ** 0.5,
            color=None, weight=0, fill=True, fill_opacity=0.85,
            fill_color=_colour(v, scale, good_is_positive),
            tooltip=f"{r.OA21CD}: {v:+,.0f} {unit}" if integer else f"{r.OA21CD}: {v:+.1f} {unit}"
                    f" ({r.residents:,} residents)").add_to(fg)
    return fg


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


def panel(c: dict, run_label: str, data_hash: str, gap_meta: dict, gap_run: str) -> str:
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
<p style="margin:6px 0">Mean (min–max) over {pe['offsets']} timetable offsets, AM 08:00–09:00, {html.escape(str(c['modelled_date']))}.
Blue = better, red = worse. Costs: {cost_note}</p>
<details><summary>Gap map (baseline, car times indicative)</summary>
<p>Top {gap_meta['lines']} of {gap_meta['of']:,} commute flows by commuters × door-to-door minutes PT is slower than car
(high car terminal times). They carry {gap_meta['commuters_shown']:,.0f} of {gap_meta['commuters_all']:,.0f} commuters.
Car times failed their own P2 spot check, so read the ranking, not the minutes. Run {html.escape(gap_run)}.</p></details>
<details><summary>Caveats and inherited fails</summary><ul>{caveats}{inherited}</ul></details>
<div style="color:#555;margin-top:6px">compare run {html.escape(run_label)} · data {data_hash}</div></div>"""


def build(compare_dir: Path, gap_dir: Path, scenarios_dir: Path, data_hash: str, extent: tuple, show_gap: bool = False) -> str:
    c = json.loads((compare_dir / "compare.json").read_text())
    g = gpd.read_parquet(compare_dir / "change_oa.parquet")
    x0, y0, x1, y1 = extent
    m = folium.Map(location=[(y0 + y1) / 2, (x0 + x1) / 2], tiles=None, control_scale=True)
    m.fit_bounds([[y0, x0], [y1, x1]])
    # OSM's own tile servers refuse pages opened from disk (no Referer: HTTP 403), so the
    # basemap is Esri's light grey canvas, which needs only attribution.
    esri = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
    attr = "Tiles © Esri — Esri, HERE, Garmin, © OpenStreetMap contributors"
    folium.TileLayer(esri.format("World_Light_Gray_Base"), attr=attr, control=False, max_zoom=16).add_to(m)
    folium.TileLayer(esri.format("World_Light_Gray_Reference"), attr=attr, control=False, max_zoom=16).add_to(m)
    _oa_layer(g, "pt_jobs_45_AM_change", "Jobs within 45 min by PT, change (AM)", "jobs", True, not show_gap).add_to(m)
    _oa_layer(g, "pt_jobs_30_AM_change", "Jobs within 30 min by PT, change (AM)", "jobs", True, False).add_to(m)
    _oa_layer(g, "centre_change_min_AM", "Time to the centre, change (AM; blue = faster)", "min", False, False,
              integer=False).add_to(m)
    if g["frequent_15_change"].fillna(0).ne(0).any():
        _oa_layer(g, "frequent_15_change", "15-minute service, change", "(1 = gained)", True, False).add_to(m)
    gl, meta = _gap_layer(gap_dir, show_gap)
    gl.add_to(m)
    _scheme_layer(scheme_files(scenarios_dir, c["scenario"], c["against"])).add_to(m)
    folium.LayerControl(collapsed=False, position="topleft").add_to(m)
    m.get_root().html.add_child(folium.Element(panel(c, compare_dir.name, data_hash, meta, gap_dir.name)))
    m.get_root().header.add_child(folium.Element(f"<title>{html.escape(c['scenario'])} against {html.escape(c['against'])}</title>"))
    return m.get_root().render()
