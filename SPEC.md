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

**Base years:** `B2026` (current network, built in P2) and `B2028` (built in P3 from
`B2026` by scenario ops: + the Portishead line with Portishead and Pill stations at an
hourly service to Temple Meads). *Amended at the P3b opening stop (2026-10-10):* `B2028`
also has the Henbury-line extension of the hourly Filton Abbey Wood service to North
Filton (Bristol Brabazon; opens November 2026) and Henbury (March 2028), on the West of
England Combined Authority's October 2025 business case; one sensitivity run omits
Henbury. This supersedes the P2a sentence below for `B2028`. All scenarios compare against `B2028` by default.
*Amended at P2a (2026-09-27):* the Henbury-line stations (Henbury, North Filton) are
not open — Darwin on 28 Sep 2026 has Henbury with no services and North Filton not in
the reference data — so they are in neither base year. `B2028` moves to P3 because it
is an `add_line` job for P3's generator.

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
                                viewer (exploration)      blog components + CSV
```

### Stack

- Python, conda env `transit-lab` (mirrors upstream's `environment.yml` approach)
- DuckDB for tables; GeoParquet for spatial outputs; OMX optional for matrices
- `r5py` (Conveyal R5) for routing and travel-time matrices — needs a JDK ≥ 21. Java
  is pinned inside the conda env (conda-forge `openjdk=21`), not taken from Homebrew
  as upstream's `env.sh` does; see `sources.md` P0.
- `AequilibraE` for PT assignment in Phase 6 (optional — see §7.5)
- Rail GTFS from the lab's own Darwin Push Port timetable → GTFS converter
  (`src/lab/supply/gtfs_rail.py`), run once per timetable snapshot and cached. *Amended
  at P2a:* the timetable arrives as Darwin XML, not CIF, so UK2GTFS and the R toolchain
  are not used.

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
  viewer/                 exploration app (MapLibre + deck.gl, static files)
  viz/components/         reusable blog graphics
  viz/embeds/             per-post built embeds
  data/{raw,interim,out}/
  runs/<run_id>/          scorecard.json, maps/*.parquet, report.md, run.json
  tests/
```

### CLI

```
lab demand commute                      # P1: HBW matrix at low / central / high d
lab build-baseline B2026|B2028
lab scenario new S015-a4-brt --from B2028
lab run S015-a4-brt [--demand D1] [--periods AM,IP]
lab compare S015-a4-brt B2028          # writes a diff report
lab calibrate                           # Phase 5 gates
lab export-viz <run_id> [--compare <run_id>]    # tiles + JSON for viewer and components
lab view                                         # local dev server for the viewer
lab embed <component> <run_id> [--compare <run_id>] --out viz/embeds/<slug>
                                                 # self-contained embed
```

---

## 3. Data sources

Confirm every URL at build time (rule 3). All OGL v3 unless stated.

| Dataset | Use | Notes |
|---|---|---|
| upstream `data/interim/BRS/census.duckdb` (`subwaybuilder-bristol`) | raw commute flows, zones, PWC points, BRES jobs, TS058 | consume the raw OA table `flows`/`oa_flows` and census inputs, **read-only**. Do **not** consume `od_msoa_adj` or `base_flows` — both carry game adjustments (edge fold, external clamp) |
| Census 2021 ODWP14EW | commute by household car availability | Nomis census_2021_od; MSOA → MSOA only |
| Census 2021 TS061 (method of travel to work) | residence-based mode shares | lockdown-distorted; use as a secondary target only |
| Census 2021 TS045 (car or van availability) | car-availability segmentation of non-commute demand | |
| Census 2011 WU03EW (OD by method of travel, MSOA) | pre-COVID OD mode shares for the pivot base; v1.1 external mode shares | 2011 MSOA → 2021 MSOA lookup required |
| National Travel Survey (NTS0403, NTS0303, trip-length tables) | trip rates by purpose, trip-length distributions, mode shares | national — filter by area type where tables allow |
| ONS Data Science Campus travel-to-work matrix method | NTS-based fixed-workplace share | method for the BRES discount (§6.1); its rates are 2018–21 and not used |
| NTS0412, NTS0504 (NTS 2025) | commuting trips per worker; weekday share | ceiling on the BRES discount |
| ONS OPN working-arrangement tables | travel / hybrid / home shares | floor on the BRES discount; occupation and region breakdowns |
| ONS LSOA 2021 population-weighted centroids | internal / external zone rule | ArcGIS `LSOA_PopCentroids_EW_2021_V4` |
| Census 2021 ODWP01EW (OA and MSOA, national) | base flows; external_out inflow | upstream's raw download, read-only |
| BODS timetables (GTFS, regional) | bus network | watch for superseded duplicate services. The modelled-date feed comes from the National Data Library BODS archive (daily regional GTFS; Open Innovations) |
| Darwin Push Port timetable (`_v8`) and reference (`_ref`) files | rail network (replaces CIF via UK2GTFS) | one snapshot covers ~48 h, so it must be the snapshot generated on the modelled date; National Rail open-data terms (not OGL) — confirm wording before use |
| NaPTAN | rail station coordinates (TIPLOC/CRS → point) | |
| TNDS (Welsh buses) | — | **not pursued in v1**; see §11 item 18 and plans/P2.md D8 |
| Darwin Push Port (own dataset, Athena) | observed rail punctuality/cancellations | v1: validation only; see §12 |
| BODS vehicle location (SIRI-VM) | observed bus speeds → shape of car congestion factors | *Amended at P2a:* nine neutral September days from the National Data Library archive (30 s snapshots) for calibration, plus three days of live 10 s polling to measure the spacing bias |
| DfT travel time measures, local A roads (by local highway authority) | calibration target for local A-road speeds | England only; grain (period, urban/rural) confirmed in P2a A7 |
| National Highways WebTRIS | SRN speeds by site, direction and 15 min | England only |
| DfT AADF by link and direction | flow-weighting modelled speeds like-for-like with DfT; **road class** | *Amended at P2b:* each car segment's class (SRN vs local A) comes from DfT's own count-point category (TM/TA → SRN, PM/PA → local A), because OSM's `trunk` marks many local A roads; between count points the class propagates along contiguous links with the same `ref`, and where a road changes class the boundary is taken from count-point positions (recorded); OSM is the fallback only where a road has no count point |
| ONS 2021 rural–urban classification | area types (D4) | confirm Wales coverage |
| Open Data Bristol "Historic journey times" | validation (relative pattern only if pre-2020) | existence and licence to confirm |
| Bristol City Council ATC speeds | validation (optional, re-run when received) | requested, not received |
| MobilityData GTFS validator | feed checks | Java jar |
| Google Maps Platform | — | **excluded from all inputs:** its terms prohibit caching and creating content from results |
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

*Amended at P3 (2026-10-09):* P2 wrote its skims as flat files
(`data/interim/skims/pt_AM_oa_lsoa.parquet` etc.). P3 moves them into
`data/interim/skims/<scenario>/<network_version>/<mode>/<period>.parquet`, with a test
that every P2 output reproduces unchanged. `network_version` identifies the routable
network a scenario was skimmed on (OSM, any OSM patch file, GTFS feeds and, if adopted,
the elevation model, by hash) and is stored in every run record.

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
  *Amended at P3 (2026-10-09):* P2 did not fit `bus_speed_ratio` (bus speeds shaped the
  car factors by period; no bus ÷ car ratio was stored). P3 fits it from the P2 bus
  traversals against the calibrated car speeds on the same links, by road class × area
  type × period, with a spatial hold-out, and reports it beside P2's held-out bus speed
  error (27% per cell), which it inherits. It applies to **new** on-street bus and BRT
  sections and to `road_speed_factor`; existing routes keep their timetabled times.
  Segregated busway sections of a new BRT route (`alignment_type: at_grade_segregated`,
  `elevated`, `tunnel`) use the speed-profile rule, not car speeds.
- *Added at P3:* `fare_change` is validated and recorded but has no effect until fares
  enter generalised cost (P5); a run reports it as "recorded, not yet modelled".
  `landuse` and `landuse_delta` act on a minimal `L2026` built in P3 (OA residents from
  Census 2021 TS001, LSOA jobs from BRES); students and floorspace arrive in P4.
- Emit `frequencies.txt` for headway-based services; R5 handles these natively.
  *Added at P2 (D7):* R5 routes frequency-based services by randomising schedules. When
  P3 starts, check how r5r's `expanded_travel_time_matrix()` handles them and set
  `draws_per_minute` deliberately (a tagged parameter), so scenario PT skims are
  comparable with the timetable-based baseline.
  *Decided at the P3a stop (2026-10-10), replacing the two sentences above:* generated
  services are written as **explicit trips** (`stop_times.txt`), not `frequencies.txt`.
  Measured on one existing 15-minute route rewritten by hand: frequency-based routing
  matched the timetable's median within 1 minute on only 37–43% of the pairs that use
  the route, whatever the number of draws, while explicit trips at the same offset
  reproduce it exactly. A 3-minute shift of the explicit trips moves results as much, so
  **every scenario with a generated service is run at 3 offsets; the mean and the range
  are reported and comparisons use the mean.** An offset is a shared phase fraction:
  offset k shifts every generated service by the same fraction of its own headway
  (0, ⅓, ⅔ by default), so a scenario yields 3 networks however many services it
  generates. The capacity check runs on each. Networks and skims are cached by
  `spec_hash` + offset + `network_version`.
- *Decided at the P3b opening stop (2026-10-10):* **ops beyond the list above** —
  `modify_route` also takes `stopping_pattern` (`add` / `remove`), `extend_to` and
  `truncate_at`; `replace_route` swaps a route (all day or in named periods) for a
  generated one, keeping the link; `reroute` moves a route onto a new alignment between
  two of its stops. `scenarios/<id>.yaml` is checked by `lab scenario validate`, one
  named rule per failure (`src/lab/scenario.py`).
- *Run times on existing track (D8, decided 2026-10-10):* for `existing_rail` segments
  the sectional time is the median working time between the two timing points over
  today's trains of the operator that call at both ends, and the dwell is that operator's
  median; the speed-profile rule applies only where no passenger train runs today. Times
  taken this way reflect today's diesel and bi-mode fleet (§11 item 23).
- *Added at the end of P2:* the baseline bus GTFS holds **copies** of trips that carry
  pick-up or set-down restrictions (976 source trips written as 4,727 copies; R5 ignores
  the GTFS flags, §7.1). `trips.txt` has an `original_trip_id` column. The generator and
  every scenario op must treat a source trip and its copies as one vehicle journey:
  modify, remove and re-time by `original_trip_id`, and never derive frequencies or
  headways from `trip_id` counts.
- Validate every op: stops within 50 m of the alignment, alignment within the extent,
  headways positive, `route_id` exists for modify/remove. Fail loudly.
- `spec_hash` = hash of the YAML plus referenced files, stored on `scenario`.

---

## 6. Demand (Phases 1 and 4)

### 6.1 Commute (HBW)

*Amended at P0 and again after P1 review (2026-09-27). The earlier drafts consumed
`od_msoa_adj` and spread external flows onto edge zones; both are withdrawn — see
`sources.md` P0 and P1.*

**What the matrix is.** People **present at a fixed workplace on an average weekday**,
not people by where they mainly work (the census definition). The BRES discount is
what turns one into the other.

**Zones.** An LSOA is internal only if its ONS population-weighted centroid is inside
the extent — the same rule upstream uses for demand points. Every other zone is
external: MSOA level, or country for Scotland and Northern Ireland. Destination MSOAs
for the correction are the internal part of each MSOA (its internal LSOAs).

**Source.** Build from the national Census 2021 ODWP01EW OA file (upstream's raw
download, read-only), plus TS058, BRES and the ONS lookups and centroids. Do **not**
consume `od_msoa_adj` or `base_flows`: both are downstream of the edge fold and
external clamp. Aggregate to LSOA → MSOA (upstream's measured finest level above
record-swapping noise). Under upstream's classification the national file must
reproduce upstream's raw `oa_flows` exactly; the change to the lab's classification is
reconciled as a listed reclassification.

**Lockdown correction — reuse, don't re-implement.** Upstream `covid.correct` (merged
to upstream `main`, first introduced in `0ea228d`) does the no-fixed-place
redistribution via TS058 and then the BRES rescale, with no point placement, folding or
clamping. The game path calls it and continues; the lab calls it and stops.

**BRES discount.** d = 1 − (workers attending a fixed workplace on an average weekday
÷ BRES jobs). Not the census TS058 36.2%, which is March 2021 lockdown home-working and
is already removed on the census side. Low / central / high = 0.429 / 0.539 / 0.604
[MODELLED], bracketed by ONS OPN (over-counts attendance: floor on d) and NTS
(under-counts: ceiling); central is the midpoint. National: no industry, regional-at-
the-right-grain or area-type breakdown usable with BRES exists (sources.md P1). Where
no factor is extreme, destination totals are BRES × (1 − d), so d sets the level of
the whole matrix: P1 outputs all three, later phases use central, and P5 chooses the
default (§9).

**No cap; a check.** The correction runs uncapped. Any in-extent destination factor
above `commute.destination_factor_check` (4.0 [MODELLED]) fails the build — a zoning or
data artefact to fix, not to clip.

**External_out on the same basis.** Each external workplace MSOA gets a national
factor: BRES × (1 − d) ÷ census fixed-workplace inflow from all England and Wales
origins, applied to the base flow. Where none can be computed (Scotland, Northern
Ireland), or where it exceeds `commute.destination_factor_check`, the in-extent overall
factor for that d [MODELLED]. External zones above the check are not capped and do not
fail the build; each is listed in the run log with its BRES jobs, census arrivals and
raw factor. v1.1 replaces this fallback (§11b).

**Order.** `correct()` (no-fixed-place redistribution → BRES discount → rescale →
factor check) first, then the CA/NCA split. Assumption: redistributed no-fixed-place
workers share their origin's car-availability split.

**Car availability.** ODWP14EW proportions (published at MSOA → MSOA only): the pair's
own split if it has ≥ `commute.ca_min_commuters` (10 [MODELLED]), then origin MSOA ×
destination LAD at the same threshold, then origin MSOA.

**Destination grain.** Each destination MSOA is split across its internal LSOAs by
BRES LSOA jobs. Assumption: within an MSOA every origin gets the same destination
pattern. Chosen because LSOA → LSOA was measured too noisy upstream (21.5% of commuters
in flows ≤ 2).

**External commuters.** Built uncut at their real external MSOA, with distance to the
nearest internal LSOA centroid attached, tagged `external_in` or `external_out`. No
clamping, no spreading; the 30 km cut is a v1.1 decision. External ↔ external flows
(both ends outside after reclassification) are not lab trips and are dropped, counted.
v1 excludes externals from mode choice, assignment and connectivity metrics; v1.1 adds
them through gateways (§11b).

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

*Amended at P2 (2026-09-28):* **skims represent the AM peak hour, 08:00–09:00,** for
both car and PT (PT departure window 08:00–09:00; car speeds for that hour). The demand
AM period stays 07:00–10:00; the 07:00–10:00 average car speeds are kept as a
sensitivity skim. IP skims: PT departs 12:00–13:00; car speeds are the 10:00–16:00
average.

## 7. Evaluation engine

### 7.1 Skims

- `r5py.TravelTimeMatrix` per scenario × mode × period, with a departure time window
  (default 60 min) and percentiles (25, 50, 75). *Amended at P2a:* r5py 1.1.7's matrix
  returns total times only — there is no expanded output with walk, wait, in-vehicle and
  transfer components. Components come from a `DetailedItineraries` sample instead
  (plans/P2.md D7). *Approved (2026-09-27):* PT totals **and** components come from
  r5r's `expanded_travel_time_matrix()` (r5r 2.4.0, R5 7.5.1, separate env
  `transit-lab-r`), one per-departure-minute output; r5py stays for walk and cycle.
  Per OA → LSOA pair and period: mean access/wait/ride/transfer/egress and n_rides over
  reachable minutes, p25/p50/p75 and best-minute totals, reachable share, walk-only
  share, top-3 routes. Walk-only minutes count their whole time as walk. A pair is
  unreachable below a reachable-minute share threshold [MODELLED]. GC is computed in
  Python from the stored means (GC is linear, so mean GC = GC of mean components);
  OA → LSOA → LSOA aggregation with population weights (§4). Both engines take every
  shared routing setting from `params/base.yaml`.
- Car: *amended at P2a* — link time = free-flow time ×
  `factor[period][direction][road_class][area_type]`, fitted to absolute speeds (DfT
  local-A-road measures by authority, WebTRIS on the SRN) with bus moving speeds
  shaping the pattern within each authority, and validated on held-out data
  (plans/P2.md §5; copied here when P2b is approved). Parking search and access walk by
  destination area type [PLACEHOLDER until sourced]. *Structure decided 2026-09-27:*
  a base of class × area × direction × period factors for every road, plus per-road
  multipliers (regularised towards 1) only on the DfT-measured roads; the headline
  accuracy for unmeasured roads is leave-one-road-out; road class from DfT count points
  (§3). Car routing: native OSRM with per-direction segment speeds (plans/P2.md D2).
- Check GTFS `calendar.txt` covers the chosen modelled date; fail otherwise.

**As built at the end of P2 (2026-10-09)** — where this differs from the bullets above,
this is what the code does (detail and run ids in plans/P2.md §9):

- *PT.* Walking 4.8 km/h and cycling 16 km/h, both [SOURCED] from DfT Journey Time
  Statistics; cycling uses no elevation model. Each pair also stores the same component
  means over ride minutes only (`r_` columns, `ride_share`) and `gc_ride_min`: the PT
  alternative proper has at least one ride (§7.3), and that is what the gap map uses.
  OA → LSOA weights are OA resident commuters (Census 2021 OA flows), not population.
- *Bus timetable.* R5 ignores GTFS `pickup_type` / `drop_off_type`, so `lab supply bus`
  writes each restricted trip as copies in which every journey is legal
  (`gtfs_bods.split_restricted`).
- *Caches.* PT skim chunks and the r5r network are keyed to the routing settings, the
  skim script and the input file hashes, and are rebuilt when any of them change.
- *Car, in order:* (1) free-flow speed per segment; (2) SRN factors from WebTRIS sites;
  (3) local period shape from bus moving speeds — AM, the 08:00–09:00 skim hour (AMPH)
  and IP from the nine archive days, PM from the three live days, national ratio as the
  fallback; slow legs are dwell only near stops, and zero-distance legs are dropped;
  (4) level fitted to DfT all-day speeds per road, with regularised per-road multipliers
  on the measured roads (kept by the pre-registered rule); (5) **the ANPR layer**: on
  centre and urban non-SRN segments of the Bristol built-up area only (config
  `anpr.layer_scope`: Bristol's LSOAs plus the fringe LSOAs inside the ONS 2022
  built-up areas touching Bristol's), speed × k[area type, period] and a delay per
  signalised approach d[area type], fitted on a spatial half of Bristol's ANPR links
  (hourly stamp = end of hour) and validated on the other half. Inside that scope the
  ANPR level takes priority over DfT's (Robbie, 2026-10-09) and the ±5% DfT acceptance
  is given up; outside it speeds stay on the DfT fit.
- *Network v2 (decided at the P3a stop, 2026-10-10; adopted subject to the PT spot-check
  re-score, plans/P3.md §9).* Walking and cycling use a terrain model: OS Terrain 50
  (50 m grid) with R5's Tobler slope cost, the same raster and function in r5py and r5r.
  R5 applies one function to both modes and never speeds anything up downhill, and the
  grid is noisy, so flat ground would be 3% slower: the base walk speed is therefore
  rescaled (`routing.walk_speed_terrain_factor`, × 1.0318 → 4.95 km/h) until the
  flattest fifth of OAs average the sourced 4.8 km/h. Cycle speed is not rescaled.
  Network v1 (flat) stays on record; `LAB_NETWORK=flat` reproduces it. A network is
  identified by `network_version` (§4). r5py caches built networks by input file hash
  only, so each slope function gets its own tagged copy of the raster.
- *Whole minutes.* r5py's travel time matrix truncates to the minute; r5r's expanded
  matrix (PT skims) reports tenths. Coverage adds half a minute to r5py walking times;
  the walk and cycle skims are still truncated (to settle when P5 uses them as modes).
- *Car GC.* In-vehicle time plus parking search and access walk by destination area
  type. Those terminal times cannot be sourced: they are a [MODELLED] range (low = none,
  high = the values in params) and outputs report both ends.
- *Car spot checks* use held-out ANPR links (Google is excluded and car times cannot be
  observed by hand); *PT spot checks* compare a journey planner with the model's best
  and percentile times.

### 7.2 Generalised cost (in minutes)

```
GC = IVT + w_walk·walk + w_wait·wait + n_transfers·P_interchange + fare / VoT_segment
```

- `w_walk`, `w_wait` default 2.0 — confirm against TAG M3.2 [SOURCED]. Keep a named
  parameter set `game_weights` (1.39 / 1.37, from Subway Builder's published weights)
  for sensitivity runs.
- `P_interchange` default 5–10 min [PLACEHOLDER — source from TAG/literature].
- *Amended at P2 (2026-09-28), TAG M3.2 (May 2024) §3.2:* PT waiting follows the
  standard wait-curve treatment. The **first** wait comes from TAG's illustrative wait
  curve (Figure 2; rises at a lower rate than headway for infrequent services, uncapped
  per §3.2.8) applied to the pair's effective headway (2 × the mean over the departure
  window minus the best departure); **transfer** waits are the timetable model's own.
  The curve is `[SOURCED]` from TAG M3.2 Figure 2 (illustrative wait curve, digitised).
  Walk 2.0, wait 2.0 and interchange 7.5 min sit within TAG Table 1's indicative ranges
  (1.5–2.0, 1.5–2.5, 2–10) and are calibrated later. Random-arrival GC (half-headway
  first wait) is stored alongside. Accessibility stays on plain travel time.
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
- **Frequent-service coverage** (*amended at P3, 2026-10-09; replaces "population and
  jobs within 400 m / 800 m of a stop with ≤ 10 min peak headway"*). Population and jobs
  by service-quality class, and the population with no frequent service:
  - walking time from each OA population-weighted centroid to stops **on the network**,
    not straight-line distance;
  - a distance-decay weight per mode class (bus, BRT, tram / light rail, heavy rail and
    metro, ferry), since people walk further to rail than to a bus stop; coaches are
    excluded and listed;
  - the service level at each stop cluster, counted in vehicle journeys
    (`original_trip_id`) at calls where boarding is allowed, per direction;
  - periods 07–10, 10–16, 16–19 and 19–22, plus the 08–09 skim hour. The headline is
    the **worse of AM and inter-peak**, so a peak-only service does not count as
    frequent; the evening is reported separately;
  - the "frequent" headway threshold is a parameter, reported at 10 and 15 min;
  - jobs at OA are BRES LSOA jobs split by Census 2021 workplace counts, labelled as
    such;
  - deprivation splits use the English Indices of Deprivation and the Welsh Index of
    Multiple Deprivation **separately, never pooled**;
  - a reduced-mobility variant (shorter decay) is reported alongside.
  *Settled at the P3a stop (2026-10-10):*
  - **headline: "frequent" = every 15 minutes or better** (the scheme design standard),
    with every 10 minutes or better shown as "high frequency"; on network v2 (terrain)
    with the half-minute truncation correction, the uncorrected figure alongside;
  - **served and decay are separate** (decided 2026-10-10): an OA is served if a stop
    cluster at that service level is within TfL's PTAL maximum walk — 8 minutes to bus
    and BRT, 12 minutes to rail and tram, at 4.8 km/h effective. The decay curves weight
    how many would walk that far and give the decay-weighted count beside it. Two
    variants are always reported: strict (the decay curves' 85th percentiles, 524 m and
    1,259 m) and loose (800 m and 1,610 m, the unverified WYG figures);
  - decay: a logistic curve per mode class fitted to the mean and 85th percentile of
    observed walks to stops, from El-Geneidy et al. (2014), Montréal — **not UK data**
    (bus 296 / 524 m, rail 818 / 1,259 m, metro 565 / 873 m); BRT and tram interpolated
    between bus and rail `[MODELLED]`. The UK figures first proposed (WYG 2015) could
    not be opened and are much longer (bus 85th percentile 800 m); sources.md P3;
  - service-quality classes after the Swiss ARE method (stop category from mode and
    interval, class from category and distance), with network walking distance in place
    of ARE's straight line;
  - service level = departures per hour in one direction: all departures at the
    cluster halved, except at termini and one-way stops (the ARE convention);
  - stop clusters: same name within 150 m or any name within 40 m, never joined across
    more than a 4-minute walk.
  Every figure names the modelled date, the walk speed and whether gradient is included.
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

**Choosing the BRES discount.** P5 runs the calibration with each of the three P1
demand versions (`p1-low`, `p1-central`, `p1-high`). Whichever `d` best fits the
station-usage, BUS01 and traffic-count gates becomes the default for later phases. The
fit for all three is logged in the calibration report, whichever wins. The central
value is only the default until then.

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
- under upstream's classification, internal → internal, external_in and external_out
  match upstream's raw flows exactly, before correction;
- the move to the LSOA-centroid zone rule is reconciled as a listed reclassification
  that accounts for every commuter;
- from upstream-equivalent inputs, upstream's covid-stage inputs are reproduced
  exactly and the median correction factor is close to upstream's 1.43, with each
  difference measured and explained in `sources.md`;
- the BRES discount is derived from recorded inputs at low / central / high, and no
  destination factor exceeds the check.

**P2 — Baseline supply and skims.** OSM networks; BODS GTFS clipped and de-duplicated;
rail GTFS from Darwin; `B2026`; calibrated car congestion; AM and IP skims; baseline
accessibility; draft gap map. Working plan: `plans/P2.md` (stops after P2a, P2b, P2c).
*Acceptance:* journey-time spot checks pass (car and PT, ≥ 16 of 20 within ±15%);
accessibility vs DfT metric ρ ≥ 0.8; congestion calibration within the plan's
tolerances. *Amended at P2a:* `B2028` moves to P3.
*First publishable output:* the **gap map** — large commute flows where PT GC ÷ car GC
is highest. This needs no behavioural model.

**P3 — Scenario engine and costs.** YAML schema + validator, GTFS generator, patching,
`costs.yaml`, connectivity and cost metrics, `lab compare`. *Amended at P3
(2026-10-09):* working plan `plans/P3.md`, three stops — P3a frequent-service coverage
on `B2026` (§8.2; uses no car times), P3b the scenario engine and `B2028`, P3c costs and
`lab compare`. P3 runs before P7a, so the viewer starts with two baseline maps.
*Acceptance:* a test scenario (e.g. one frequency change) round-trips; a new-line
scenario produces sensible run times, fleet size and capex; `B2028` is built from
`B2026` via scenario ops (moved from P2 at P2a).

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

**Visualisation**

17. **Maps imply more precision than the model has.** Demand-derived layers are shown
    no finer than LSOA; accessibility at OA; line loads rounded to a sensible precision.
    Every visual carries the fidelity disclaimer or links to it.

**Supply (added at P2a)**

18. **No Welsh bus timetables** (TNDS not pursued): Newport-area zones are masked from
    PT accessibility, the DfT comparison and the gap map, shown as "no bus data", not as
    low access. *Under review:* the BODS archive has a Wales region and Newport Bus
    appears in BODS vehicle locations; if A4 finds its coverage adequate, the mask is
    proposed for removal (plans/P2.md D8).
19. **No car-speed calibration targets in Wales.** DfT local-road measures and WebTRIS
    cover England only, so factors for the same road class, area type and direction are
    transferred from the English side, `[MODELLED]`. Newport Bus positions are in BODS
    and can check the pattern, but give no absolute target.

**Scenario engine (added at P3)**

20. **Generated services are headway-based; nothing proves a timetable works.** The
    track-capacity check (plans/P3.md D6) only catches sections loaded beyond a stated
    limit. Its base usage is passenger and empty-stock paths from Darwin, which holds
    **no freight**: freight is a `[MODELLED]` allowance per section where it is known
    to run, not a count. Every capacity table says so.

21. **Gradient is crude.** One slope function (Tobler) serves walking and cycling; it
    likely under-penalises cycling uphill, and R5 gives no downhill gain. The terrain
    grid is 50 m with 4 m RMSE, too coarse for a short steep street. Revisit the cycle
    skims in P5; a finer model (EA LIDAR composite, NRW LIDAR) is a lead.
22. **How far people walk to a stop comes from Montréal (2003 survey), not the UK.**
    No UK primary source could be opened. The British figures in circulation are
    longer, which would raise coverage. A UK lead: NTS stage-level microdata.

23. **Run times on existing track are today's.** They come from the working timetable
    of the present diesel and bi-mode fleet, so they are pessimistic for a new electric
    fleet with better acceleration. Stated, not adjusted.

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
- **FUTURE — Subway Builder bridge.** Formerly §11a. Game → lab: import routes and
  stations from `SubwayBuilderAPI.gameState` as scenario YAML. Lab → game: build a
  scenario via the Build API, with ridership exported via `gameState`. Game ridership
  comes from a different model (commute-only, driving and walking as the only
  alternatives) and is never validation. Using the published Bristol map by hand for a
  blog post is fine at any time and needs no bridge.
- **FUTURE — Animated trip playback** in the viewer (deck.gl trips layer), once
  assignment produces paths worth animating.
- **FUTURE — Industry- or occupation-varying attendance.** Vary the BRES discount by
  destination employment mix instead of using one national rate. Blocked in v1: no
  current source gives attendance by industry (SIC), which is what BRES is classified
  by. ONS OPN does publish it by occupation (e.g. professional occupations 30%
  travel-only / 41% hybrid, elementary 78% / 1%, Apr–Jun 2026), so a route exists via
  a workplace occupation mix (Census 2021 workplace-population tables) if one proves
  robust. Addresses 1.
- **FUTURE — `d` by approximated social grade.** ODWP09EW gives OD flows by
  approximated social grade, which tracks occupation. Map ONS occupation-level hybrid
  rates onto social grade to vary `d` per flow rather than per destination.
  Addresses 1.
- **FUTURE — Generalisation.** Parameterise by `CityConfig` alongside the
  `ons_to_subwaybuilder` package so the lab runs for any English or Welsh city.
