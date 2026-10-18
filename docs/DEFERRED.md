# Deferred from v1

Sections cut from `SPEC.md` at the v1 scope review (2026-10-15), kept verbatim. v1 ends
at P3: scenario engine, accessibility and frequent-service connectivity, and costs.
Nothing here is to be built until v1 is frozen and a scenario needs it. The tag
`archive/pre-v1-scope` holds the repo as it was before the cut.

---

## 11a/11b Visualisation and external gateways

## 11a. Visualisation (Phase 7)

*Amended 2026-09-27: replaces the Subway Builder bridge, which moves to §12. Reasoning
in `sources.md`, P0 decisions.*

### Stack

All open source, with licences recorded in `sources.md`; versions pinned.

- **MapLibre GL JS** for rendering.
- **PMTiles** for all tiled data: lab outputs via tippecanoe (already in the upstream
  toolchain), and a self-hosted Protomaps basemap extract for the map extent. No paid
  tile APIs. OSM attribution shown on every map.
- **deck.gl** for layers MapLibre handles badly: OD desire lines and line-load
  bandwidths.
- **Static files only; no server.** Hosting is deferred to P7. Candidates are
  Cloudflare R2/Pages, GitHub Pages, and S3 + CloudFront. Whichever is chosen must pass
  a test that HTTP range requests work for `.pmtiles`.

### Provenance rule

Every visual is generated from a recorded run and carries its `run_id` and data hash
(in the viewer UI, and in embed metadata plus a visible footer). Numbers in visuals are
never hand-edited.

### Viewer (exploration tool)

- **Scenario picker:** any run, or any pair of runs.
- **Compare modes:** swipe, and difference (scenario − parent).
- **Layers:**
  - PT accessibility (absolute and change) by OA;
  - the gap map (PT GC ÷ car GC, weighted by flow);
  - demand desire lines (filterable by purpose, period and minimum flow);
  - line loads as bandwidths, with load-factor colouring;
  - frequent-service coverage (§8.2 as amended at P3);
  - IMD decile overlay.
- **Scorecard panel:** headline §8 metrics for the run and the difference against the
  comparison run.
- **Honesty:** the fidelity disclaimer is always visible, and the panel lists the
  `[PLACEHOLDER]` / `[MODELLED]` values that affected the run.
- **URL state:** layers, runs, map view and compare mode are all encoded in the URL, so
  any view is a shareable permalink.
- **Stretch (P7b), a drawing tool:** draw a line and stops, then export
  `lines/*.geojson`, `stops/*.geojson` and a scenario YAML stub for §5.

### Blog components (for readers)

Each component makes one point.

- Must work at 360 px width, use colour-blind-safe palettes, have keyboard-accessible
  controls, and include a text alternative.
- Self-contained embed with no runtime dependencies outside the author's own host.
- Includes a static PNG fallback for feeds and email.
- Payload budget of about 1.5 MB per embed, excluding basemap tiles [MODELLED].

The v1 set:

1. `swipe-choropleth`: before/after of any OA-level metric (default: jobs within 45 min
   by PT, AM).
2. `gap-map`: desire lines coloured by PT ÷ car GC ratio, weighted by flow.
3. `line-loads`: bandwidth map for one scenario, with a peak-hour load-factor legend.
4. `scorecard-bars`: scenario vs parent on the headline metrics.

Also: CSV export of any metric table for Datawrapper.

---

## 11b. v1.1 — External gateways (committed next scope)

> **Rail part built 2026-10-18** (SPEC §6.1 amendment; `sources.md` "External rail gateways"), sized from the
> ORR matrix with the census as pattern. The Road, Bus/coach and Park and ride items below remain deferred.

Distinct from §12: this is committed, not speculative. It builds on the P1 demand
table's `external_in` and `external_out` rows without rebuilding P1. Gateways work in
**both directions**: inbound commuters arrive at them, and outbound residents leave
through them — residents commuting out by rail generate access trips to Temple Meads,
Parkway and the other stations, which load the internal network like any other trip.

- **Fixed mode.** External demand is fixed-mode; schemes cannot change an external
  trip's main mode. State this as a v1.1 limitation in any report that includes
  externals.
- **Mode shares by external origin** come from Census 2011 WU03EW (external MSOA →
  internal MSOA by mode), scaled to current totals.
- **Rail.** Inbound gateways are the *alighting* stations inside the extent (Temple Meads,
  Parkway, Bath Spa, Filton Abbey Wood etc.), not boundary crossings. Split across
  stations using ORR's station-to-station origin–destination data; confirm the latest
  release exists and its licence before use. The onward station → workplace leg is
  modelled normally. Outbound: the *boarding* stations inside the extent, split the same
  way; the home → station access leg is modelled normally.
- **Road.** Gateways are the points where each external origin's fastest OSM car route
  crosses the boundary, computed once, not assigned by hand. Expected crossings include
  M4, M5, M32, M48/M49 and the Severn crossings, A4, A37, A38, A370, A420 and A432 —
  verify these against the data.
- **Bus/coach/other.** Measure the share first. If small, load onto road gateways as
  bus and log it.
- **Park and ride.** Car arrivals at road gateways get a P&R alternative in mode
  choice.
- **Validation.** DfT road traffic count points on gateway roads are a sanity check
  only (they count vehicles, not commuters).
- **External factors: artefact or lockdown.** In v1, an external workplace whose
  national factor (BRES × (1 − d) ÷ 2021 census arrivals) exceeds the factor check falls
  back to the in-extent factor. v1.1 replaces that fallback with a test against Census
  2011 WU arrivals. A zone with a high BRES ÷ 2011-arrivals ratio is a head-office
  registration artefact (fall back). A zone that is high only against 2021 is a
  lockdown effect (keep its national factor). London offices were hit hardest in
  March 2021, so a blanket fallback undercounts London-bound rail commuters.

---

---

## P4–P8 and v1.1 phases

**P4 — Demand expansion.** Gravity models for non-commute purposes; time periods.
*Acceptance:* trip-length calibration within tolerance; purpose totals match NTS rates
× population.

**P5 — Mode choice and calibration.** Base shares, incremental logit, `lab calibrate`.
*Acceptance:* all §9 gates pass on `B2026`.

**P6 — Assignment and scorecard.** AON assignment, line loads, crowding, full
scorecard and report. Decide on AequilibraE.
*Acceptance:* `B2026` station usage gate passes; a full scenario report generates
end to end.

**P7a — Viewer skeleton + gap map.** Can start as soon as P2 produces its first gap
map; it does not wait for P3–P6.
- Basemap PMTiles for the extent.
- `lab export-viz`.
- Viewer showing the gap map for B2026.
- The `gap-map` component as a working embed.
- Hosting chosen and range-request test passing.

*Acceptance:* the gap-map embed renders from static hosting on a 360 px viewport; its
footer shows `run_id` and data hash.

**P7b — Full viewer + remaining components.** All viewer layers, compare modes, URL
state, the other three components, CSV export, and the drawing tool as a stretch.
*Acceptance:* swipe and difference comparison between B2028 and a test scenario from
P3; all four components pass the 360 px, PNG fallback and payload checks.

**P8 — Blog production.** Embeds and static fallbacks for the first post, built only
via `lab embed` from recorded runs.

**v1.1 — External gateways.** See §11b. Committed next scope after P8.

---

## 9 Calibration and validation gates (Phase 5)

## 9. Calibration and validation gates (Phase 5)

`lab calibrate` runs these against `B2026` and fails if any is outside tolerance.
Tolerances are starting points [MODELLED]; tighten once the model is stable.

| Check | Target | Tolerance |
|---|---|---|
| Commute mode share by LAD | Census 2011 WU03EW, cross-checked with TS061 | ±5 pp per mode |
| All-purpose mode share, region | NTS (area-type filtered) | ±5 pp |
| Mean trip length per purpose | NTS | ±10% |
| Station entries/exits | ORR estimates, top 20 stations | GEH < 10 on ≥ 80% |
| Bus trips | DfT BUS01 per LA, scaled to a weekday | ±20% |
| OA PT accessibility | DfT Connectivity Metric | Spearman ρ ≥ 0.8 |
| Implied bus fare / IVT elasticities | published ranges | inside range |
| Journey-time spot checks | 20 hand-picked OD pairs vs a public journey planner | ±15% |

Station entries/exits and similar observed totals include external commuters, which v1
omits; the gate must compare like with like or report the external share it cannot
explain (see §11 item 13).

Also a **regression test**: commute totals must reconcile to upstream **in parts**
(see P1 acceptance) before any discount is applied.

**Choosing the BRES discount.** P5 runs the calibration with each of the three P1
demand versions (`p1-low`, `p1-central`, `p1-high`). Whichever `d` best fits the
station-usage, BUS01 and traffic-count gates becomes the default for later phases. The
fit for all three is logged in the calibration report, whichever wins. The central
value is only the default until then.

---

---

## 8.1 Effectiveness

### 8.1 Effectiveness

- Daily and peak-hour PT boardings, by line and by mode
- PT passenger-km
- Mode shares (all trips; commute only) — region and by LAD
- Car trips and car vehicle-km removed
- Person-hours of generalised cost saved (rule of half):
  `ΔCS = ½ · Σ (T⁰ + T¹) · (GC⁰ − GC¹)`
- Max load factor per line; list of links above threshold

---

## 7.3–7.5 Mode choice, assignment, AequilibraE

### 7.3 Incremental (pivot-point) mode choice

Per OD × purpose × segment × period, starting from base shares `P⁰`:

```
P¹_m = P⁰_m · exp(λ · ΔGC_m) / Σ_k P⁰_k · exp(λ · ΔGC_k),   ΔGC_m = −(GC¹_m − GC⁰_m)
```

- *Added at P2 (D7):* the "PT" alternative means itineraries with **≥ 1 ride**;
  walk-only itineraries belong to the walk mode, not PT. PT skims store the share of
  departure minutes whose best option is walk-only, so the split is available.
- PT is **one** alternative whose GC is the best path (or a logsum over PT paths). A
  new line therefore changes PT's GC rather than creating a new mode with no base
  share — this is what lets the pivot approach test brand-new modes.
- NCA segment: car driver unavailable.
- `λ` per purpose [CALIBRATED] so that implied elasticities fall in published ranges —
  record the implied bus IVT and fare elasticities in every calibration report.
- Base shares `P⁰`: 2011 WU03EW for commute (pre-COVID, OD-specific), NTS mode share
  by distance band for other purposes, reconciled to TS061 and NTS at LAD level.
- v1: rows with `external` set (`external_in` / `external_out`) are excluded.

### 7.4 Assignment and crowding

- v1: all-or-nothing assignment of PT trips along the R5 best path; accumulate
  boardings by stop and load by stop-pair and period.
- `load_factor = pax_per_hour / (vehicles_per_hour × vehicle.capacity)`; flag > 0.8
  [MODELLED threshold].
- No crowding feedback into GC in v1 (see §11).
- *Added at the end of P2:* `vehicles_per_hour`, vehicle-hours, vehicle-km and line loads
  count vehicle journeys by `original_trip_id` (`gtfs_bods.trips_per_route`), not
  `trip_id`: the restriction copies of one trip are one vehicle, and boardings assigned
  to different copies of a trip are summed onto it.

### 7.5 Optional: AequilibraE

If AON assignment proves too crude (e.g. parallel bus corridors), switch PT assignment
to AequilibraE's optimal-strategies transit assignment. Decide at the end of Phase 6
on evidence, not up front.

---

## 6.2–6.3 Other purposes and time periods

### 6.2 Other purposes

Doubly-constrained gravity per purpose (HBE, HBS, HBO, NHB, EMP):

```
T_ij = A_i · O_i · B_j · D_j · f(c_ij),   f(c) = exp(-β · c)   (or c^α·exp(-β·c))
```

- `O_i` from residents × NTS trip rate by purpose [SOURCED]; `D_j` from attractor
  weights (jobs by sector, retail floorspace, students, hospitals, POIs) [MODELLED].
- `c_ij` = car-available generalised cost from the base skims (or a mode-logsum once
  Phase 5 exists).
- Calibrate `β` per purpose so modelled mean trip length matches the NTS distribution
  [CALIBRATED]; report the fitted mean and the target.
- The upstream special generators (universities, hospitals, airport, retail) map
  onto HBE / HBO / airport purposes — re-use their `[HESA]`/`[PUB]` figures.

### 6.3 Time periods

Split daily trips into AM (07–10), IP (10–16), PM (16–19), OP using NTS start-time
profiles by purpose [SOURCED]. Model AM and IP first; PM as the transpose of AM for
home-based purposes is an acceptable v1 simplification — say so in reports.

---

## 8.3 headline ratios and indicative BCR

- Headline ratios: £ per additional daily PT trip, £ per car trip removed, £ per
  person-hour saved per year, £ per unit of job-accessibility gained.
- **Indicative BCR**: PV(ΔCS × VoT + revenue − opex) / PV(capex), with discount rate
  and appraisal period from TAG. Label it everywhere as *indicative, not TAG-compliant*.

