# Bristol Transit Lab — specification

**Audience:** Claude Code, picking this up cold. Read this whole file before planning
anything. Then read the upstream project's `sources.md` and `GENERALISATION.md`
(`~/Documents/Projects/subwaybuilder-bristol/`) — this project reuses that pipeline
and inherits its measurements, its provenance discipline and its known open issues.

**Upstream, precisely:** the repo is `subwaybuilder-bristol` at
`~/Documents/Projects/subwaybuilder-bristol`, packaged as `ons_to_subwaybuilder`
(CLI `otsb`). Its Bristol database is `data/interim/BRS/census.duckdb`. The older
`data/interim/census.duckdb` is the pre-package build and is **not** used. Earlier
drafts of this spec called the upstream "bristol-sb-map"; that name is retired.

**Goal:** a reproducible, scenario-driven model of how people move around the
Bristol–Bath city region, used to iterate on public transport ideas (new lines,
frequency changes, bus priority, fares, land-use growth) and compare them on three
axes: **effectiveness**, **connectivity** and **cost**.

**Fidelity target — be honest about this everywhere:** this is a *sketch-planning*
model. It sits between an accessibility tool (no behaviour) and a full DfT TAG-compliant
variable demand model. Its outputs are indicative and comparative — good for ranking
ideas and finding gaps, not for a business case. Every report it produces must say so.

---

## 0. Working rules for Claude Code

Copied into `CLAUDE.md` at the repo root.

1. **British English** in all prose, comments and reports.
2. **Provenance is not optional.** Every dataset goes in `sources.md` with URL, access
   date, licence and version. Every numeric parameter carries a tag:
   - `[SOURCED]` — taken from a named publication (cite it next to the value)
   - `[CALIBRATED]` — fitted in this repo against a named target (record the fit)
   - `[MODELLED]` — an assumption; write the reasoning in a comment
   - `[PLACEHOLDER]` — not yet sourced; must be resolved before any published output
3. **Never copy a URL from this spec into code without navigating to it first** and
   confirming the current download link — as the upstream build did.
4. **Measure before deciding.** Where this spec proposes a default, and the data says
   otherwise, the data wins — record the finding in `sources.md` under the phase that
   found it, in the same style as upstream.
5. **Fail loudly.** Coverage gaps, unreconciled totals, GTFS dates outside the service
   calendar, and pipeline-order violations raise errors, not warnings.
6. **Scenarios are data, not code.** No place names, line names or coordinates inside
   `src/`. They live in `scenarios/`, `landuse/` and config.
7. **Every run is reproducible:** a run record stores git SHA, scenario hash, demand
   version, parameter file hash, input dataset versions, and the upstream pin (upstream
   DB file hash, `ons_to_subwaybuilder` version and commit, `stage_log` snapshot).
8. **Do not refactor upstream in place** unless a phase below says so. Consume its
   outputs; if a change is needed upstream, make it there as its own commit — and
   propose it to the user before making it.
9. **Keep game concerns out of analysis.** Anything that exists to fit Subway Builder's
   pop budget (destination merging, nearest-point clamping, edge folding) must not feed
   the lab.
10. **Upstream is read-only.** Open the upstream DuckDB with `read_only=True`; never
    write to it from this repo.

---

## 1. Scope

**In:** the upstream extent `[-3.02, 51.32, -2.28, 51.60]` (Bristol, Bath,
Weston-super-Mare, Newport, Keynsham, Yate, Portishead, Clevedon, Nailsea,
Filton/Bradley Stoke). Weekday travel. Modes: walk, cycle, bus, rail, new
fixed-guideway (light rail / tram / BRT / metro as a generic "mass transit" type), car
driver, car passenger.

**Out (v1):** freight, weekends, tourism beyond the airport, road-network capacity
restraint, land-use feedback, **external commuters** (built in P1, excluded from v1
behaviour and metrics — see §6.1 and §11b). See §12 for the future list.

**Base years:** `B2026` (current network) and `B2028` (current + committed schemes:
Portishead line with Portishead and Pill stations at an hourly service to Temple
Meads; confirm the Henbury-line stations' status at build time). All scenarios compare
against `B2028` by default.

---

## 2. Architecture

```
          landuse/            demand/                 scenarios/
      (residents, jobs,   (OD by purpose,         (parent + ops YAML)
       students, POIs)     period, segment)               │
             │                   │                         ▼
             │                   │              GTFS generator + OSM
             │                   │                         │
             │                   │                         ▼
             │                   │                  r5py skims (per mode, period)
             │                   │                         │
             │                   ▼                         ▼
             └──────────►  generalised cost  ──►  incremental mode choice
                                                           │
                                                           ▼
                                             PT assignment + crowding
                                                           │
                                                           ▼
                              scorecard.json + GeoParquet maps + report.md
                                                           │
                                           ┌───────────────┴──────────────┐
                                           ▼                              ▼
                                    blog / Datawrapper          Subway Builder bridge
```

### Stack

- Python, conda env `transit-lab` (mirrors upstream's `environment.yml` approach)
- DuckDB for tables; GeoParquet for spatial outputs; OMX optional for matrices
- `r5py` (Conveyal R5) for routing and travel-time matrices — needs a JDK ≥ 21. Java
  is pinned inside the conda env (conda-forge `openjdk=21`), not taken from Homebrew
  as upstream's `env.sh` does; see `sources.md` P0.
- `AequilibraE` for PT assignment in Phase 6 (optional — see §7.5)
- `UK2GTFS` (R) for rail CIF → GTFS, run once per timetable and cached

### Repo layout

```
bristol-transit-lab/
  CLAUDE.md
  SPEC.md                 this file
  sources.md              every dataset + every phase's findings
  params/
    base.yaml             all behavioural and cost parameters, each tagged
    costs.yaml            unit cost library, each with source and price year
  landuse/
    L2026.yaml            baseline (generated)
    deltas/*.yaml         growth sites, e.g. brabazon.yaml
  scenarios/
    B2026.yaml
    B2028.yaml
    S###-<slug>.yaml
    lines/*.geojson       alignments
    stops/*.geojson
  src/lab/
    zones.py
    landuse.py
    upstream.py           read-only access to the upstream DB + its pin
    demand/{commute,gravity,periods,segments}.py
    supply/{osm,gtfs_bods,gtfs_rail,generator,patch}.py
    skims.py
    cost.py               generalised cost
    choice.py             incremental logit
    assign.py
    metrics/{effectiveness,connectivity,cost}.py
    report.py
    runrecord.py
    cli.py
  bridge/subway-builder/  JS mod (Phase 7)
  data/{raw,interim,out}/
  runs/<run_id>/          scorecard.json, maps/*.parquet, report.md, run.json
  tests/
```

### CLI

```
lab build-baseline B2026|B2028
lab scenario new S015-a4-brt --from B2028
lab run S015-a4-brt [--demand D1] [--periods AM,IP]
lab compare S015-a4-brt B2028          # writes a diff report
lab calibrate                           # Phase 5 gates
lab export-game S015-a4-brt             # Phase 7
lab import-game <game_export.json>      # Phase 7
```

---

## 3. Data sources

Confirm every URL at build time (rule 3). All OGL v3 unless stated.

| Dataset | Use | Notes |
|---|---|---|
| upstream `data/interim/BRS/census.duckdb` (`subwaybuilder-bristol`) | raw commute flows, zones, PWC points, BRES jobs, TS058 | consume the raw OA table `flows`/`oa_flows` and census inputs, **read-only**. Do **not** consume `od_msoa_adj` or `base_flows` — both carry game adjustments (edge fold, external clamp) |
| Census 2021 ODWP14EW | commute by household car availability | Nomis census_2021_od |
| Census 2021 TS061 (method of travel to work) | residence-based mode shares | lockdown-distorted; use as a secondary target only |
| Census 2021 TS045 (car or van availability) | car-availability segmentation of non-commute demand | |
| Census 2011 WU03EW (OD by method of travel, MSOA) | pre-COVID OD mode shares for the pivot base; v1.1 external mode shares | 2011 MSOA → 2021 MSOA lookup required |
| National Travel Survey (NTS0403, NTS0303, trip-length tables) | trip rates by purpose, trip-length distributions, mode shares | national — filter by area type where tables allow |
| ONS Data Science Campus travel-to-work matrix method | NTS-based fixed-workplace / travel-frequency share | derives the BRES discount (§6.1) |
| BODS timetables (GTFS, England) | bus network | watch for superseded duplicate services |
| National Rail timetable (CIF) via UK2GTFS | rail network | |
| Darwin Push Port (own dataset, Athena) | observed rail punctuality/cancellations | v1: validation only; see §12 |
| BODS vehicle location (SIRI-VM / GTFS-RT) | observed bus speeds → car congestion factors | sample a representative week |
| OSM (Geofabrik — the same five extracts as upstream) | walk, cycle, car networks | |
| DfT Transport Connectivity Metric (OA/LSOA ODS) | baseline accessibility validation | experimental; not reproducible exactly |
| ORR Estimates of station usage | station entries/exits validation | |
| ORR station-to-station OD (v1.1) | splitting external rail commuters over alighting stations | confirm latest release and licence before use |
| DfT road traffic counts (v1.1) | gateway sanity check | vehicles, not commuters |
| DfT BUS01 series | bus patronage by local authority | |
| English Indices of Deprivation (latest) | equity splits | |
| TAG data book + TAG units M1–M3, A1 | values of time, weights, optimism bias, appraisal conventions | |
| WECA / council business-case appendices (Local Model Validation Reports) | extra calibration targets | published PDFs — extract by hand, tag `[SOURCED]` |

---

## 4. Data model

All tables in `data/interim/lab.duckdb` unless noted. Spatial tables as GeoParquet.

```sql
-- geography
zones(zone_id, level /* OA|LSOA|MSOA */, parent_id, lad_code, pwc_lon, pwc_lat, geom)

-- land use, versioned
landuse(landuse_version, zone_id, residents, workers, jobs, jobs_by_sector JSON,
        students, retail_floorspace_m2, source_tag)

-- demand, versioned
demand(demand_version, purpose /* HBW|HBE|HBS|HBO|NHB|EMP|SPECIAL */,
       period /* AM|IP|PM|OP */, segment /* CA|NCA */, o_zone, d_zone, trips,
       external /* NULL | 'external_in' | 'external_out': one end outside the extent;
                   excluded from v1 behaviour */,
       ext_dist_km /* distance to the external end, externals only */)

-- observed / base mode shares used as the pivot
base_mode_share(base_version, purpose, segment, o_zone, d_zone, mode, share, source)

-- scenarios
scenario(scenario_id, parent_id, description, landuse_version, created_at, spec_hash)
scenario_op(scenario_id, seq, op_type, params JSON)

-- skims, partitioned Parquet: data/interim/skims/<scenario>/<mode>/<period>.parquet
skim(o_zone, d_zone, ivt_min, walk_min, wait_min, n_transfers, fare_gbp,
     dist_km, gc_min, p25_total_min, p50_total_min, p75_total_min)

-- results
run(run_id, scenario_id, demand_version, params_hash, git_sha, started_at, status)
result(run_id, metric, geography_level, geography_id, period, value, unit)
line_load(run_id, route_id, from_stop, to_stop, period, pax, capacity, load_factor)
```

Zone level for demand and choice: **LSOA → LSOA** (or LSOA → MSOA for commute, where
the upstream noise finding applies — see §6.1 for the destination-grain decision).
Access/egress points for routing: **OA population-weighted centroids**, aggregated back
to LSOA with population weights.

---

## 5. Scenario specification

A scenario is a parent plus an ordered list of operations. The generator applies ops
to the parent's GTFS/OSM to produce a routable network, and to the parent's land use.

```yaml
id: S014-airport-lrt
parent: B2028
description: Light rail Temple Meads – Bedminster – Airport, 7.5 min peak
landuse: L2026              # or a delta chain: [L2026, deltas/brabazon.yaml]
ops:
  - add_line:
      route_id: LRT1
      name: Airport LRT
      mode: light_rail          # light_rail | tram | brt | metro | bus | heavy_rail
      alignment: lines/airport_lrt.geojson
      # each LineString segment carries a property:
      #   alignment_type: at_grade_street | at_grade_segregated | elevated | tunnel | existing_rail
      stops: stops/airport_lrt.geojson
      speed_profile: {max_kmh: 70, accel_ms2: 1.0, dwell_s: 30}
      headways_min: {AM: 7.5, IP: 10, PM: 7.5, OP: 15}
      span: "05:30-00:00"
      vehicle: {capacity: 250}
      fare: {type: flat, gbp: 2.00}
  - modify_route: {route_id: "<BODS route id for the Airport Flyer>", headways_min: {IP: 20}}
  - road_speed_factor:           # e.g. a bus lane: buses on these links get this factor
      links: lines/a38_bus_lane.geojson
      applies_to: [bus]
      factor: 1.25
  - remove_route: {route_id: "..."}
  - add_stop: {route_id: "...", after_stop: "...", stop: {name: "...", lon: 0, lat: 0}}
  - fare_change: {modes: [bus], type: flat, gbp: 2.00}
  - landuse_delta: {file: landuse/deltas/brabazon.yaml}
```

**Generator rules**

- Run times from geometry + speed profile (accel/decel + dwell), not guessed. For
  `mode: bus` on street, use period congested car speed × `bus_speed_ratio` [CALIBRATED]
  unless a `road_speed_factor` applies.
- Emit `frequencies.txt` for headway-based services; R5 handles these natively.
- Validate every op: stops within 50 m of the alignment, alignment within the extent,
  headways positive, `route_id` exists for modify/remove. Fail loudly.
- `spec_hash` = hash of the YAML plus referenced files, stored on `scenario`.

---

## 6. Demand (Phases 1 and 4)

### 6.1 Commute (HBW)

*Amended at P0 (2026-09-27). The earlier draft consumed `od_msoa_adj` and spread
external flows onto edge zones; both are withdrawn — see `sources.md` P0.*

**Source.** Build from the raw OA → OA table `flows` (265,473 pairs) in the upstream
DB, plus the census inputs (TS058, BRES, lookups, PWC). Do **not** consume
`od_msoa_adj` or `base_flows`: both are downstream of the edge fold and external clamp.
Aggregate to LSOA → MSOA (upstream's measured finest level above record-swapping noise;
re-measure and log).

**Lockdown correction — reuse, don't re-implement.** Preferred route: an upstream
change to `ons_to_subwaybuilder` exposing an analysis-grade stage that takes raw flows
at a chosen grain and returns the lockdown-corrected matrix (no-fixed-place
redistribution via TS058, then BRES rescale) **before** any point placement, folding or
clamping. The game path calls it and continues; the lab calls it and stops. It is a
separate upstream commit, the upstream Bristol regression test must still pass, and it
is proposed to the user before it is made. Fallback, if upstream is too tangled to split
cleanly: re-implement in the lab, with a test that runs both implementations on the
same input and compares.

**BRES discount — not the census 36.2%.** The census TS058 home-working share (36.2%)
is who worked mainly from home in March 2021; it is already removed on the census side
and must not be applied again. The BRES discount is the share of jobs whose holder does
not travel to the workplace on an average weekday *now* (hybrid working, part-time
days). Derive it with the ONS Data Science Campus / NTS approach and tag it `[SOURCED]`.
Order: **discount BRES → rescale → check whether the 4.0 cap still binds.** If it no
longer binds, drop it rather than carrying a game-era safety rail. Log the result
either way.

**Car availability.** After the correction, split each OD pair into CA/NCA using
ODWP14EW proportions, at the finest level that stays above record-swapping noise
(re-measure).

**Destination grain.** Decided (2026-09-27): each destination MSOA is split across its
LSOAs by BRES LSOA jobs — discounted jobs if the discount varies by area. Assumption,
logged: within an MSOA every origin gets the same destination pattern. Chosen because
LSOA → LSOA was measured too noisy upstream (21.5% of commuters in flows ≤ 2).

**BRES discount definition.** d = 1 − (workers attending a fixed workplace on an
average weekday ÷ BRES jobs): the fixed-workplace share and average days attended,
combined. Method from the ONS Data Science Campus travel-to-work matrix report; rate
from current NTS / ONS hybrid-working statistics. Where the cap does not bind,
destination totals are BRES × (1 − d), so d sets the level of the whole matrix: P1
outputs the matrix at low / central / high d; later phases use central, with the range
for sensitivity runs.

**Order.** `correct()` (no-fixed-place redistribution → BRES discount → rescale → cap
check) first, then the CA/NCA split. Assumption, logged: redistributed no-fixed-place
workers share their origin's car-availability split.

**External commuters.** P1 builds them uncut at their real external origin or
destination MSOA, with distance attached (no clamping, no spreading; the 30 km cut is a
v1.1 decision), tags them by direction — `external_in` (external origin → internal
workplace) or `external_out` (internal residence → external workplace) — in the demand
table, and stops. v1 excludes them from mode choice,
assignment and connectivity metrics. v1.1 adds them through gateways (§11b) on top of
the P1 table rather than rebuilding it.

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

## 7. Evaluation engine

### 7.1 Skims

- `r5py.TravelTimeMatrix` per scenario × mode × period, with a departure time window
  (default 60 min) and percentiles (25, 50, 75). Use the expanded/detailed output to get
  walk, wait, in-vehicle and transfer components for PT.
- Car: free-flow R5 times × `congestion_factor[period][road_class]`, calibrated against
  BODS vehicle-location speeds on shared links [CALIBRATED]. Parking time/cost by
  destination zone type [MODELLED].
- Check GTFS `calendar.txt` covers the chosen modelled date; fail otherwise.

### 7.2 Generalised cost (in minutes)

```
GC = IVT + w_walk·walk + w_wait·wait + n_transfers·P_interchange + fare / VoT_segment
```

- `w_walk`, `w_wait` default 2.0 — confirm against TAG M3.2 [SOURCED]. Keep a named
  parameter set `game_weights` (1.39 / 1.37, from Subway Builder's published weights)
  for sensitivity runs.
- `P_interchange` default 5–10 min [PLACEHOLDER — source from TAG/literature].
- `VoT` by purpose and segment from the TAG data book, in a stated price base [SOURCED].
- Car GC includes fuel (distance × VOC) and parking.
- Mode-specific constants (e.g. a rail/LRT "quality" bonus over bus) default to **0**.
  If introduced, tag `[MODELLED]` and always report a run without them — this is the
  single easiest way to flatter a scheme.

### 7.3 Incremental (pivot-point) mode choice

Per OD × purpose × segment × period, starting from base shares `P⁰`:

```
P¹_m = P⁰_m · exp(λ · ΔGC_m) / Σ_k P⁰_k · exp(λ · ΔGC_k),   ΔGC_m = −(GC¹_m − GC⁰_m)
```

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

### 7.5 Optional: AequilibraE

If AON assignment proves too crude (e.g. parallel bus corridors), switch PT assignment
to AequilibraE's optimal-strategies transit assignment. Decide at the end of Phase 6
on evidence, not up front.

---

## 8. Metrics — the scorecard

Every run writes `runs/<run_id>/scorecard.json` with the values below, each for the
scenario, its parent, and the difference.

### 8.1 Effectiveness

- Daily and peak-hour PT boardings, by line and by mode
- PT passenger-km
- Mode shares (all trips; commute only) — region and by LAD
- Car trips and car vehicle-km removed
- Person-hours of generalised cost saved (rule of half):
  `ΔCS = ½ · Σ (T⁰ + T¹) · (GC⁰ − GC¹)`
- Max load factor per line; list of links above threshold

### 8.2 Connectivity

- Cumulative accessibility: jobs reachable within 30 and 45 min by PT (AM, p50) per OA,
  and the same for hospitals, secondary schools, food stores
- Decay-weighted accessibility `A_i = Σ_j O_j · exp(−β·t_ij)`
- Population-weighted means; distribution by IMD decile; a Palma-style ratio (top 10% /
  bottom 40%) of PT accessibility
- PT/car accessibility ratio per OA
- Population and jobs within 400 m / 800 m of a stop with ≤ 10 min peak headway
- Change maps as GeoParquet

### 8.3 Cost

From `params/costs.yaml`. Every entry: `value`, `unit`, `price_year`, `source`, `tag`.

```
capex = Σ_segments length_km × unit_cost[mode][alignment_type]
      + n_stops × stop_cost[mode]
      + depot_cost[mode]
      + fleet_size × vehicle_cost[mode]
fleet_size = ceil(round_trip_min / peak_headway_min) × (1 + spare_ratio)
opex_year  = veh_hours × £/veh_hour + veh_km × £/veh_km   (annualised from a weekday × factor)
revenue    = Σ PT trips × average fare, minus abstraction from existing services
```

- Seed values: UK tram £20–30m per route-km as a ballpark including vehicles [SOURCED —
  Bath & Bristol Trams / D. Walmsley]; tunnel ×4–6 and elevated ×2–2.5 of at-grade
  [SOURCED — Halcrow Fox 2000, via the Flyvbjerg et al. comparison]; Coventry VLR claim
  of ~£7m/km as a low-cost variant [SOURCED, unproven]. Bus and BRT values
  [PLACEHOLDER].
- Apply optimism-bias uplift from TAG A1.2 by project stage [SOURCED]. Report both raw
  and uplifted costs.
- Headline ratios: £ per additional daily PT trip, £ per car trip removed, £ per
  person-hour saved per year, £ per unit of job-accessibility gained.
- **Indicative BCR**: PV(ΔCS × VoT + revenue − opex) / PV(capex), with discount rate
  and appraisal period from TAG. Label it everywhere as *indicative, not TAG-compliant*.

### 8.4 Report

`runs/<run_id>/report.md`: the fidelity disclaimer, headline scorecard table, top
changed OD pairs, maps (static PNG + GeoParquet), line loads, cost breakdown, list of
every `[PLACEHOLDER]` and `[MODELLED]` value that influenced the result.

---

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

---

## 10. Build phases

Each phase ends with its `sources.md` section and passing tests.

**P0 — Scaffold.** Repo, env, `CLAUDE.md`, `params/`, run records (with the upstream
pin), CLI skeleton, SPEC amendments.
*Acceptance:* `lab --help` works; a no-op run writes a valid `run.json`.

**P1 — Analysis-grade demand export.** Read the upstream DB read-only; rebuild
LSOA → MSOA from raw `flows`; lockdown correction via the upstream analysis stage (or
the tested fallback); BRES discount → rescale → cap check; CA/NCA split; destination
grain decision; external flows built and tagged, not spread.
*Acceptance* (reconcile in parts, not against one total — upstream's 356,286 includes
the 30 km cut and clamping, so a rebuild from raw flows should not match it):
- internal → internal flows match upstream exactly, before correction;
- external flows reconciled as a separate line;
- median correction factor close to upstream's 1.43, with any difference measured and
  explained in `sources.md`;
- the BRES discount is sourced, and the cap decision is logged.

**P2 — Baseline supply and skims.** OSM networks; BODS GTFS clipped and de-duplicated;
rail GTFS; `B2026` and `B2028`; AM and IP skims; baseline accessibility.
*Acceptance:* journey-time spot checks pass; accessibility vs DfT metric ρ ≥ 0.8.
*First publishable output:* the **gap map** — large commute flows where PT GC ÷ car GC
is highest. This needs no behavioural model.

**P3 — Scenario engine and costs.** YAML schema + validator, GTFS generator, patching,
`costs.yaml`, connectivity and cost metrics, `lab compare`.
*Acceptance:* a test scenario (e.g. one frequency change) round-trips; a new-line
scenario produces sensible run times, fleet size and capex.

**P4 — Demand expansion.** Gravity models for non-commute purposes; time periods.
*Acceptance:* trip-length calibration within tolerance; purpose totals match NTS rates
× population.

**P5 — Mode choice and calibration.** Base shares, incremental logit, `lab calibrate`.
*Acceptance:* all §9 gates pass on `B2026`.

**P6 — Assignment and scorecard.** AON assignment, line loads, crowding, full
scorecard and report. Decide on AequilibraE.
*Acceptance:* `B2026` station usage gate passes; a full scenario report generates
end to end.

**P7 — Subway Builder bridge.** See §11a.
*Acceptance:* one scenario exported to the game, built, run, ridership imported, and
a lab-vs-game comparison table produced.

**P8 — Reporting polish.** Blog-ready charts and maps (Datawrapper-friendly CSVs).

**v1.1 — External gateways.** See §11b. Committed next scope after P8.

---

## 11a. Subway Builder bridge (Phase 7)

The game is the **sketchpad**; the lab is the **judge**.

- **Game → lab:** a mod reads `SubwayBuilderAPI.gameState` (routes, stations, trains)
  and writes a scenario YAML (alignment from track geometry, stops from stations,
  headways from trains per route). `lab import-game` validates and registers it.
- **Lab → game:** `lab export-game` writes a JSON the mod consumes to place blueprint
  tracks (with elevation from `alignment_type`), build them, create routes (using the
  `light-rail` train type where appropriate), buy trains and add them to routes. On
  `onDayChange`, the mod records ridership and metrics to mod storage / an export file.
- **Comparison:** `lab compare-game <run_id> <game_export>` — ridership by line in both.
  Expect the game to over-predict relative to the lab where buses already serve the
  corridor, because the game's alternatives are only driving and walking. Report the
  gap; don't calibrate either side to the other.
- Pin the game's API version in the mod manifest and check it at load.

---

## 11b. v1.1 — External gateways (committed next scope)

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

---

## 11. Drawbacks of this approach (v1)

These are known limitations of what is being built. Reports must list the ones that
matter for the scenario being reported.

**Data**

1. **Census 2021 was taken in lockdown.** The commute matrix is structurally distorted
   (the BRES rescale and discount are corrections, not cures), and TS061 mode shares
   are unreliable. The 2011 pivot base is pre-COVID but 15 years old and pre-dates
   Metrobus, the MetroWest stations and post-pandemic hybrid working.
2. **No public 2021 OD-by-mode data.** Base mode shares are stitched from 2011 OD data,
   national NTS and LAD-level census — not observed Bristol OD mode shares.
3. **Non-commute demand is synthetic.** Gravity models calibrated to national NTS trip
   lengths reproduce averages, not Bristol's actual shopping, leisure and education
   patterns. These purposes are most of all trips, so they carry most of the uncertainty.
4. **BRES counts jobs at the registered workplace**, not where people actually work,
   which misplaces some employment (depots, head offices, agencies).
5. **Timetables are plans, not reality.** GTFS assumes buses run to time. Bristol bus
   reliability is poor, so PT travel times are optimistic; that bias favours the status
   quo bus network in comparisons.

**Method**

6. **Car times don't respond to the scenario.** Congestion factors are fixed per
   period, so removing cars doesn't speed up the remaining traffic or on-street buses.
   Decongestion benefits are therefore missing, and on-street bus schemes are
   under-credited.
7. **Only mode choice responds.** No trip generation, destination choice or time-of-day
   response. A major line that makes new destinations attractive will be under-predicted;
   induced demand is ignored.
8. **Land use is exogenous.** Transit-oriented growth only appears if a `landuse_delta`
   is supplied by hand.
9. **Incremental logit is only as good as its base.** Where base PT share is zero or
   tiny, pivoting produces little change regardless of the improvement.
10. **All-or-nothing assignment and no crowding feedback.** Parallel services split
    unrealistically; overcrowded lines don't shed riders.
11. **Zone aggregation.** LSOA-level demand hides walk-access differences within a zone;
    stop catchments are approximated from OA centroids.
12. **Single representative weekday** and AM/IP-centric modelling; PM as AM's transpose.
13. **v1 omits external commuters** (14.1% of retained commuters upstream, 53,521).
    Scenarios serving Temple Meads, Parkway, Filton, the airport or park and ride are
    under-credited; state this in any affected report.

**Cost and appraisal**

14. **Unit costs are ballparks with wide ranges.** UK light rail costs vary several-fold
    by utilities, structures and procurement. Treat capex as ±50% at best.
15. **Opex, revenue abstraction and fares are simplified.** No fare capping, concessions
    or operator economics.
16. **The BCR is indicative only.** No wider economic impacts, carbon, safety, health,
    reliability or agglomeration; not TAG-compliant; no uncertainty bands.

**Game bridge**

17. **The game's behavioural model is different and partly opaque.** Commute-only
    demand, driving and walking as the only alternatives, game-scale costs. Agreement
    between lab and game is not validation.

---

## 12. FUTURE improvements — not in v1 scope

Nothing in this section should be built until P0–P8 and v1.1 are complete and the
drawbacks above have been measured on real scenarios. Each item notes which drawback it
addresses.

- **FUTURE — Rail reliability from Darwin.** Use Robbie's Darwin Push Port dataset to
  add observed lateness and cancellation risk to rail GC (e.g. a reliability term or
  per-service p75 journey times). Addresses 5.
- **FUTURE — Bus reliability from BODS vehicle locations.** Replace timetabled bus
  running times with observed distributions; feed R5 percentiles. Addresses 5.
- **FUTURE — Road assignment with capacity restraint.** Highway assignment in
  AequilibraE with demand–supply iteration so mode shift relieves congestion and
  on-street bus speeds respond. Addresses 6.
- **FUTURE — Destination choice and trip frequency.** Logsum-based destination choice
  so new lines redistribute trips; a light trip-frequency response. Addresses 7.
- **FUTURE — Crowding in GC and capacity-constrained assignment.** Addresses 10.
- **FUTURE — Activity-based model.** Synthetic Population Catalyst + the Turing acbm
  pipeline → MATSim, for individual-level, household-aware, all-day simulation.
  Addresses 3, 7, 11, 12.
- **FUTURE — Better observed demand.** A commissioned 2021 OD-by-method-of-travel table
  from ONS for the extent; mobile network OD data if an affordable source appears; any
  WECA/G-BATS outputs obtainable on request. Addresses 1–3.
- **FUTURE — Land-use feedback.** Station-area growth as a function of accessibility
  change (the Subway Builder induced-demand mod does a toy version of this). Addresses 8.
- **FUTURE — Uncertainty.** Monte Carlo over `[MODELLED]` and `[PLACEHOLDER]`
  parameters and cost ranges; report scorecards as ranges. Addresses 14, 16.
- **FUTURE — Fuller appraisal.** Carbon, air quality, health (active travel), safety,
  reliability benefits, wider economic impacts. Addresses 16.
- **FUTURE — Network design search.** Automated generation and scoring of candidate
  networks (e.g. evolutionary search over corridors and headways) against a
  multi-objective score, with humans choosing from the Pareto front.
- **FUTURE — Cycling quality.** Level-of-traffic-stress routing so cycle
  infrastructure schemes can be tested like PT schemes.
- **FUTURE — Fares from BODS NeTEx.** Real fare structures, capping and concessions.
  Addresses 15.
- **FUTURE — More day types.** Weekend and evening models; event demand (Ashton Gate,
  the planned arena at Brabazon). Addresses 12.
- **FUTURE — External demand beyond commuting.** Non-commute external trips (shopping,
  leisure, airport passengers from outside the extent) through the v1.1 gateways.
  Addresses 13.
- **FUTURE — Web viewer.** An interactive scenario comparison map for publishing.
- **FUTURE — Generalisation.** Parameterise by `CityConfig` alongside the
  `ons_to_subwaybuilder` package so the lab runs for any English or Welsh city.
