# Sources

Every dataset and tool dependency, with where it came from, when, and its licence, and
every phase's findings. Same discipline as upstream `subwaybuilder-bristol/sources.md`:
no URL is copied from the spec into code without being navigated to first.

## Code dependencies

| Item | Source | Version | Accessed | Licence |
|---|---|---|---|---|
| r5py | conda-forge / https://pypi.org/project/r5py/ | 1.1.7 | 2026-09-27 | "GPL-3.0-or-later or MIT" (PyPI metadata) |
| R5 (Conveyal, r5py build) | https://github.com/r5py/r5/releases/download/v7.5.1-r5py/r5-v7.5.1-r5py-all.jar — fetched by r5py on first use into `~/.cache/r5py/` | v7.5.1-r5py, sha256 `d50be106cadd7b636cfc0e209052767d7df570629f79fdf98ecd5cf5d2d89be7` | 2026-09-27 | MIT |
| OpenJDK (Azul Zulu, conda-forge `openjdk`) | conda-forge | 21.0.10 LTS | 2026-09-27 | GPL-2.0 with Classpath Exception |
| JPype | conda-forge | 1.7.1 | 2026-09-27 | Apache-2.0 |
| DuckDB | conda-forge | 1.5.3 (same as upstream) | 2026-09-27 | MIT |
| Full environment | `environment.lock.yml` (conda export, no builds) | — | 2026-09-27 | — |

## Datasets

| Dataset | Level | Source | Accessed | Licence |
|---|---|---|---|---|
| Upstream Bristol DB | OA / LSOA / MSOA | `~/Documents/Projects/subwaybuilder-bristol/data/interim/BRS/census.duckdb`, `ons_to_subwaybuilder` 0.1.0 (pinned per run; now `main` @ `1c82c76`), sha256 `581da77d7dd7…` (full hash in every `run.json`) | 2026-09-27 | derived from OGL v3 sources; see upstream `sources.md` |
| ODWP14EW (Census 2021 OD by household car/van availability) | MSOA → MSOA only | https://www.nomisweb.co.uk/output/census/2021/odwp14ew.zip (found via https://www.nomisweb.co.uk/sources/census_2021_od), sha256 `9f324a154ef34852…` | 2026-09-27 | OGL v3 |
| NTS0412 (commuter trips by employment status, England) | national | https://assets.publishing.service.gov.uk/media/6a9ecd5ff36e1f225ecb7e8e/nts0412.ods (NTS 2025, published 11 Sept 2026; table updated 10 Sept 2026), sha256 `f3afe920be36fed6…` | 2026-09-27 | OGL v3 |
| NTS0504 (trips by day of week and purpose, England) | national | https://assets.publishing.service.gov.uk/media/6a9ecd5f474b8101ece46432/nts0504.ods (NTS 2025), sha256 `6e45c7e1d7520a64…` | 2026-09-27 | OGL v3 |
| ONS OPN, "Who has access to hybrid work in Great Britain?" supplementary tables | GB | https://www.ons.gov.uk/file?uri=/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/datasets/whohasaccesstohybridworkingingreatbritainsupplementarytables/current/hybridsupplementary8januaryto30march2025.xlsx (release 11 June 2025; fieldwork 8 Jan–30 Mar 2025), sha256 `3d2ce76054548b9c…`. The `cdn.ons.gov.uk` link on the dataset page returns 404 | 2026-09-27 | OGL v3 |
| ONS OPN, working arrangements by personal characteristics | GB, with region and occupation | https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/healthandsocialcare/healthandwellbeing/datasets/publicopinionsandsocialtrendsgreatbritainworkingarrangementsbypersonalcharacteristics/1aprilto28june2026/workingarrangementsbypersonalcharacteristics1aprilto28june2026.xlsx (released 17 July 2026) | 2026-09-27 | OGL v3 |
| LSOA 2021 population-weighted centroids | LSOA | ArcGIS `LSOA_PopCentroids_EW_2021_V4/FeatureServer/0` (ONS Open Geography), sha256 `a4a16e26f08fd3cd…` | 2026-09-27 | OGL v3 |
| ODWP01EW OA and MSOA (national), TS058 OA, BRES 2024 LSOA (national, paged) | OA / MSOA / LSOA | upstream's raw downloads in `subwaybuilder-bristol/data/raw/` (provenance in upstream `sources.md`); read-only; hashes in every run record | 2026-09-27 | OGL v3 |

Every run pins the upstream it read: DB file hash, package version and commit, dirty
flag and a `stage_log` snapshot (`src/lab/upstream.py`).

---

## P0 — Scaffold (2026-09-27)

### Built

- `git init`; `SPEC.md` (amended, below); `CLAUDE.md` (SPEC §0 plus setup).
- `environment.yml` → conda env `transit-lab`; `environment.lock.yml`; `env.sh`.
- `config/lab.yaml` — upstream location and the extent, kept out of `src/` (rule 6).
- `params/base.yaml`, `params/costs.yaml` — tagged skeletons. `src/lab/params.py`
  rejects any bare number, unknown tag, or tag missing its required field (`source`,
  `target`, `reasoning`). `lab params` lists everything and counts placeholders.
- `src/lab/upstream.py` — opens the upstream DB `read_only=True` only, and refuses by
  name every table that carries game adjustments (`od_msoa_adj`, `base_flows`,
  `ext_clamp`, `edge_fold`, …). A test proves a write attempt fails.
- `src/lab/runrecord.py` — `run.json` with git state, params hashes, inputs, upstream
  pin and environment; validated before it is written.
- `lab` CLI with every §2 command. Commands not yet built exit non-zero and name the
  phase that builds them. `lab run --noop` writes a valid run record.
- 30 tests, all passing.

### Three upstream findings

**1. Name, package and DB location.** The spec's "bristol-sb-map" is
`subwaybuilder-bristol` at `~/Documents/Projects/subwaybuilder-bristol`. Its pipeline is
now the installable package `ons_to_subwaybuilder` (CLI `otsb`, v0.1.0). The Bristol
database is `data/interim/BRS/census.duckdb`, which carries a `stage_log`. The older
`data/interim/census.duckdb` is the pre-package build and is ignored. The lab's config
points at the former; `upstream.pin` fails if the DB has no `stage_log`.

**2. `od_msoa_adj` and `base_flows` are game-adjusted.**

| table | grain | rows | what it already contains |
|---|---|---|---|
| `od_msoa_adj` | OA → MSOA (`o_oa, d_msoa, size`) | 277,138 | origins split to game points; edge OAs folded inward; external ends clamped to nearest points; lockdown correction |
| `base_flows` | LSOA → MSOA | 53,894 | counted *after* clamping and folding (`disaggregate.py` resolves ends through `oa_map` and `ext_clamp` before aggregating) |
| `flows` | OA → OA plus LSOA/MSOA codes for both ends | 265,473 | **raw** — 378,905 commuters with either end in the extent |

`flows` and `oa_flows` hold the same 265,473 pairs and 378,905 commuters; `flows`
is `oa_flows` with the lookup codes joined on (`extract.py`). The lab uses `flows`.

**3. Two of the spec's P1 items already exist upstream.** External spreading over the
nearest *k* points is the `clamp_spread` setting (Bristol ships at 1; measured on
Cardiff at 5). The census home-working share — 36.2% of workers, from TS058 — is
already computed and removed on the census side.

### Decisions (user, 2026-09-27)

- **Upstream facts accepted**; SPEC corrected throughout.
- **P1 rebuilds LSOA → MSOA from raw `flows`.** Neither `od_msoa_adj` nor `base_flows`
  is consumed.
- **Lockdown correction is reused, not re-implemented.** Preferred: an upstream change
  exposing an analysis-grade stage (raw flows at a chosen grain → corrected matrix, no
  point placement, folding or clamping), as a separate upstream commit, with the
  upstream Bristol regression test still passing. It is proposed to the user before it
  is made. Fallback: re-implement in the lab, with a test comparing both on the same
  input.
- **Reconcile in parts.** Upstream's 356,286 includes the 30 km cut and clamping, so a
  rebuild from raw flows should not match it. P1 acceptance is now: internal → internal
  exact before correction; externals reconciled as a separate line; median correction
  factor near 1.43, with any difference explained here. The old "no zone > 1% from
  external spreading" check is removed.
- **The BRES discount is not the census 36.2%.** TS058's figure is March 2021 lockdown
  home-working and is already removed. The BRES discount is the share of jobs not
  travelled to on an average weekday *now*, from the ONS Data Science Campus / NTS
  approach, tagged `[SOURCED]`. Order: discount → rescale → check whether the 4.0 cap
  binds; drop it if not; log either way.
- **Destination grain** (MSOA split by BRES LSOA jobs, or MSOA destinations skimmed
  through job-weighted LSOA points) is decided and logged in P1.
- **External commuters: built in P1, excluded from v1.** Tagged `external=true` in the
  demand table; excluded from mode choice, assignment and connectivity. Added in v1.1
  through gateways (SPEC §11b).
- **Upstream is read-only** and pinned in every run record.

### External spreading withdrawn

The earlier §6.1 spread each external origin over ~5 in-extent zones. That is withdrawn.
Spreading gives an external commuter a trip cost from an edge zone rather than from
where they actually start. A Cardiff-to-Temple-Meads commuter would be modelled as a
short trip from a zone near Newport, so their mode choice would respond to a journey
they do not make. That makes the choice meaningless, and it would push those trips onto
whatever PT serves the edge zone. Spreading is a placement fix that suits the game; it
doesn't work as a behavioural input. v1 omits externals and says so (SPEC §11 item 13);
v1.1 models them through gateways, with a fixed main mode.

### Viewer and blog components replace the game bridge (user, 2026-09-27)

The Subway Builder bridge (old SPEC §11a, old P7) was doing three jobs, and the game
turned out to be the right tool for none of them:

| job | the game | verdict |
|---|---|---|
| sketching lines | good at it | any GeoJSON tool does it too |
| a second opinion on ridership | weak | its model is commute-only, with no buses or cycling; the spec already said agreement with it is not validation |
| visualising lab results | cannot do it | it shows its own simulation, not the lab's outputs |

Results will be published as blog posts, so good visualisation is central to the
project, not a finishing touch. The bridge is replaced by a static-file viewer for
exploration and a set of self-contained blog components (MapLibre GL JS, PMTiles,
deck.gl; SPEC §11a). Two rules come with them: every visual is generated from a
recorded run and shows its `run_id` and data hash, and no number in a visual is
hand-edited. P7 splits into P7a (viewer skeleton and gap map, which can start as soon
as P2 produces the first gap map) and P7b (full viewer and the remaining components).
P8 becomes blog production. The bridge moves to SPEC §12 FUTURE. Drawback 17 (game
bridge) is replaced by the risk that maps imply more precision than the model has.

### SPEC amendments made at P0

- Header, §0, §1, §2 stack/layout, §3: upstream name, path, package and DB; rule 10
  (read-only upstream); rule 7 extended with the upstream pin; rule 9 names edge
  folding.
- §3: upstream row now says consume raw `flows`, not `od_msoa_adj`/`base_flows`; adds
  ORR station-to-station OD and DfT traffic counts (v1.1).
- §4: `demand.external` column.
- §6.1: replaced per the decisions above. The 30 km cut for stored externals is left
  as a P1 decision.
- §7.3: v1 excludes `external=true` rows.
- §9: note that observed totals (e.g. station usage) include externals v1 omits.
- §10: P0 and P1 wording; P1 acceptance replaced; v1.1 listed after P8.
- §11 item 13 replaced; §11b added (v1.1 external gateways, committed scope).
- §12: "Outside connections" (Subway Builder `outside_connection`) replaced by
  "External demand beyond commuting", since externals are now §11b's job; the gate for
  starting §12 now includes v1.1.

### Environment: r5py's requirements vs upstream's

| | upstream (`bristol-sb`) | lab (`transit-lab`) | why |
|---|---|---|---|
| Python | 3.13.9 | 3.13.15 | r5py 1.1.7 requires ≥ 3.10; 3.13 is supported (conda-forge build installs and runs) |
| Java | Homebrew `openjdk` (keg-only, now 26.0.2), via `env.sh` PATH | conda-forge `openjdk=21` inside the env, `JAVA_HOME=$CONDA_PREFIX/lib/jvm` | r5py docs: "a Java Development Kit (jdk), version 21 or later". Upstream needed Java only for planetiler. R5 is built against the 21 LTS, and JPype locates the JVM through `JAVA_HOME`, so pinning 21 in the env is both closer to R5's target and independent of whatever Homebrew upgrades to |
| DuckDB | 1.5.3 | 1.5.3 | kept identical so both envs read the same files |
| pandas | 2.3.3 | 3.0.6 | whatever the solver chose for r5py 1.1.7. **Watch in P2:** pandas 3 changes copy-on-write and string dtypes; if r5py output misbehaves, pin `pandas<3` |

Checked: `import r5py` starts JVM 21.0.10, R5 classes load, and r5py downloaded its R5
jar (hash above) on first use. No routing has been run yet; that is P2.

### Measured for P1's reconciliation

Raw `flows`, split by whether each OA end is in `oa_extent` (polygon intersection):

| | pairs | commuters |
|---|---|---|
| internal → internal | 182,597 | 275,369 |
| external → internal | 52,340 | 66,186 |
| internal → external | 30,536 | 37,350 |
| total | 265,473 | 378,905 |

One upstream behaviour will move the lab's correction off upstream's figures. The
game-oriented `oa_points` is used as the filter for TS058, so the 105 edge OAs (centroid
outside the bbox, folded inward) have their no-fixed-place workers dropped instead of
redistributed. That is 1,963 people in the TS058 column before the offshore share
comes out. An analysis-grade stage should include them.

### Not done in P0

- The first no-op run predated the first commit, so it recorded `git.sha: null`; it was
  re-run after committing P0.
- No scenario files, land use, or `lab.duckdb`: P1 onwards.

## P1 — Analysis-grade demand export

### Decisions before the build (user, 2026-09-27)

- **Upstream `correct()` approved**, with conditions. `bres_discount` takes a float or
  a table keyed by MSOA (the ONS Data Science Campus rate varies by area-type cluster).
  `cap` takes `None`, and the lab always passes it explicitly. "Unchanged game output"
  means identical rows after sorting by every key column: exact on integers, and exact
  on floats if achievable, otherwise ≤ 1e-9 relative, recording which held. File bytes
  are not compared. The edge-OA TS058 quirk stays on the game path and is logged
  upstream as a known issue. The upstream commit is made from a clean tree, after the
  user has reviewed the diff and regression results.
- **Externals** are stored uncut at their real MSOA, with distance attached, and tagged
  by direction: `external_in` (66,186 in raw flows) and `external_out` (37,350). The
  30 km cut is a v1.1 decision. v1.1 gateways cover both directions (SPEC §11b).
- **Destination grain:** each MSOA is split across its LSOAs by BRES LSOA jobs, using
  discounted jobs if the discount varies by area. **Assumption:** within an MSOA,
  every origin gets the same destination pattern. **Why:** upstream measured
  LSOA → LSOA as too noisy, with 21.5% of commuters in flows of 2 or fewer.
- **BRES discount definition:** d = 1 − (workers attending a fixed workplace on an
  average weekday ÷ BRES jobs). The Data Science Campus report supplies the method,
  not the rate: its 2018–19 and 2020–21 variants are neither current. The rate comes
  from recent NTS commuting / working-from-home tables and ONS hybrid-working
  statistics. P1 outputs the matrix at low, central and high d.
- **Segment order:** the ODWP14EW CA/NCA split is applied after `correct()`.
  **Assumption:** redistributed no-fixed-place workers share their origin's
  car-availability split.

### Build, round 1 (2026-09-27)

`lab demand commute` (`src/lab/demand/commute.py`) writes `demand` rows
`p1-low`, `p1-central` and `p1-high` (purpose HBW, period `DAY`, segments CA / NCA) to
`data/interim/lab.duckdb`, plus its intermediate tables. The whole build takes a few
seconds and writes a run record with the upstream pin and input hashes. Tests run it
end to end into an in-memory DB.

### Finding: upstream `flows` has no external codes

`flows` labels every external end's LSOA and MSOA as the literal `'EXT'`. External
MSOAs therefore come from the raw `oa_flows` and upstream's national lookup
`oa_lu_all`. That lookup covers England and Wales only. 80 workplace OAs in Scotland
(`S00…`, 76 OAs, 128 commuters) and Northern Ireland (`N20…`, 4 OAs, 6 commuters)
are therefore kept at country level (`S92000003`, `N92000002`), with no distance.
The build fails loudly if any other end is unmapped.

### Reconciliation in parts

All exact, against upstream's raw `oa_flows`:

| | lab | upstream raw |
|---|---|---|
| internal → internal | 275,369 | 275,369 (and 0 difference in every destination MSOA) |
| external_in | 66,186 | 66,186 |
| external_out | 37,350 | 37,350 |

Then from upstream-equivalent inputs to the lab's, one difference at a time, at d = 0
and cap 4.0 (`p1_reconciliation`):

| step | fixed-workplace | after no-fixed-place | median factor (all) | median, the 159 MSOAs upstream has |
|---|---|---|---|---|
| upstream (`base_flows`, `pops_s1`, `dest_factor`) | 356,286 | 432,002.52 | — | **1.433** |
| 0 lab grain, upstream-equivalent inputs | **356,286** | **432,002.52** | 1.463 | **1.454** |
| 1 external_out kept external | 328,890 | 404,607 | 1.566 | 1.518 |
| 2 + full TS058 (edge quirk fixed) | 328,890 | 406,537 | 1.563 | 1.515 |
| 3 + all externals uncut (the lab's inputs) | 378,905 | 456,552 | 1.524 | 1.502 |

Step 0 rebuilds upstream's clamp and 30 km cut **for comparison only**. It reproduces
upstream's covid-stage inputs exactly (both assertions are in the build). On the
destinations both sides have, the remaining 0.021 gap in the median is grain: the lab
uplifts origins per LSOA rather than per OA and does not fold edge-OA destinations
into neighbouring MSOAs. The tolerance is 0.05 [MODELLED]. The "all" median is higher
because the lab keeps 10 boundary-sliver destination MSOAs that upstream's folding
removes (see the cap, below).

Why the factor rises from step 0 to step 1: upstream clamped external_out commuters
onto edge points, so 27,396 of them counted towards in-map destination totals and
lowered those MSOAs' factors. In the lab they stay external.

### BRES discount

Definition (user): d = 1 − (workers attending a fixed workplace on an average weekday
÷ BRES jobs). Derived by `src/lab/demand/bres_discount.py` from the raw tables; values
in `params/base.yaml`.

**Found:**
- **Data Science Campus technical report** (27 June 2023,
  https://datasciencecampus.ons.gov.uk/projects/technical-report-estimation-of-travel-to-work-matrices/).
  NTS fixed-workplace share by five mobility clusters (M1 London … M5 rural), in
  2018–19 and 2020–21 variants. Per-cluster values appear only in a figure (A16); there
  is no days-attended term, no linked data and no code. Used for the method (an
  NTS-based attendance share applied to employment), not for the rate.
- **NTS 2025**, England. NTS0412 gives **222.8 commuting trips per employed person per
  year** (2019: 276.7, −19.5%). NTS0504b gives the weekday share of commuting trips:
  **0.899**. Attendance = 222.8 × 0.899 ÷ 2 ÷ 253 weekdays = **0.396** (2019, using
  the "2015 to 2019" day split: 0.493).
- **NTS definitions** (NTS 2025 notes and definitions). Commuting is only home ↔
  usual-workplace. Trips to work from anywhere else, and all work trips by people with
  no usual workplace, are classed as business. **So NTS under-counts attendance**, and
  1 − 0.396 = 0.604 is a ceiling on d.
- **ONS OPN** (GB, 8 Jan–30 Mar 2025, 5,490 working adults, past 7 days). All: travel
  only 41%, hybrid 28%, home only 14%, neither 17%. Full-time: 40 / 34 / 16 / 10.
  Part-time: 53 / 18 / 14 / 15. A travel-only worker who took days off that week
  still counts as travelling, **so this over-counts attendance** and gives a floor on d.

**Not found:**
- Any official UK figure for hybrid workers' days on site. OPN, LFS and NTS do not
  publish it. Indeed Hiring Lab (Sept 2025, job postings): 2 days in 56% of hybrid
  postings, 2–3 days in 81%. Global Survey of Working Arrangements 2025: 1.8 remote
  days a week on average. Both are used only to set the [MODELLED] 2.5-day central
  and 3-day high values.
- An NTS days-per-week-commuted table for England. It was not in the NTS 2024 or 2025
  releases, and the 2021 working-from-home tables (NTSQ09026–28) cover all adults, not
  workers. **Correction:** an early search summary attributed "40% travel to work 5
  days a week, 14% 0 days" to the NTS. Those figures are from Transport Scotland's
  2024 Scottish Household Survey and are not used.
- Any South West or area-type breakdown of attendance. OPN has none, NTS0412 is
  national, and the Data Science Campus cluster values are not published as data. The
  discount is national.
- LFS homeworking data newer than April 2021 in that series.

**Result:**

| | attendance per job | d | how |
|---|---|---|---|
| low | 0.571 | **0.429** | OPN, hybrid 3 days on site, part-time 3.5 days |
| central | 0.461 | **0.539** | midpoint of OPN central (0.527: hybrid 2.5, part-time 3) and NTS (0.396) |
| high | 0.396 | **0.604** | NTS0412 × NTS0504b |

**Tag: `[MODELLED]`, not `[SOURCED]` as SPEC asked.** Every input is sourced. But the
combination is not published anywhere, and it needs modelled days on site. Recorded as
a deviation for the user to accept or change. Not adjusted for: BRES counts jobs, not
people. Second jobs would push d up by roughly the multiple-job-holding rate, a few
per cent.

### Finding: the correction now scales the census matrix down

Upstream rescaled up (median ×1.43), because it matched the census to *all* BRES
jobs. At the central d, BRES × (1 − d) over in-map destinations is 346,119 average
weekday commuters. The census-based matrix into the same destinations, after the
no-fixed-place step, is 410,464. The median factor is **0.703**, with 136 of 169
MSOAs below 1. Low d: 0.870 (112 below 1). High d: 0.603 (149).

This is not a contradiction. The census counted people by where they *mainly* work;
the discounted BRES counts people *present* on an average weekday. The two measure
different things, and the lab's matrix is now the second, which is what a
weekday model needs.

### The 4.0 cap still binds, but only on a geography artefact (superseded — see review round 2)

With a discount, the cap binds on:
- Cotswold 011 at every d (17 modelled commuters against 577 discounted BRES jobs at
  central, ×34.6; ×42.8 low, ×29.7 high);
- Sedgemoor 002 at low d (×4.3).

Both are boundary slivers. BRES is by LSOA, so an LSOA that clips the extent brings
all its jobs, while the census counts only the few in-map OAs. Upstream never saw
this, because its edge folding moves those OAs' commuters into other MSOAs and the
sliver has no row to scale. **Kept at 4.0** as a guard against that artefact, not as a
lockdown correction. The proper fix is to apportion partial LSOAs' BRES by the in-map
share (for example non-residential floorspace). That is a decision for the user.

### Destination split and car availability (superseded — see review round 2)

- **Destinations:** in-map MSOA → LSOA by BRES LSOA jobs (the discount is national, so
  discounted and raw shares are identical). Assumption, as decided: within an MSOA,
  every origin gets the same destination pattern. External_out stays at its external
  MSOA (or country). The split conserves trips to 1e-9 (tested).
- **Car availability:** ODWP14EW is published at MSOA → MSOA only (5 categories;
  place-of-work code 3 = fixed workplace in the UK). A pair's own CA share is used when
  its ODWP14EW total is ≥ 10 [MODELLED]; otherwise the origin MSOA's share. By base
  commuters: pair basis for 7,789 pairs / 285,497 commuters (75%), origin basis for
  41,770 pairs / 93,408. Applied after the correction. Assumption, as decided:
  redistributed no-fixed-place workers share their origin's split. CA share of trips:
  internal 0.874, external_in 0.934, external_out 0.903.

### Externals (superseded — see review round 2)

| | base | p1-central trips | median distance to map | beyond 30 km |
|---|---|---|---|---|
| external_in | 66,186 | 57,715 (rescaled with their destinations) | 26.3 km | 12,081 |
| external_out | 37,350 | 46,088 (no-fixed-place uplift only; no BRES outside the extent) | 19.2 km | 12,134 |

Distance is from the external MSOA's population-weighted centroid to the nearest
in-map OA centroid, the same definition upstream's clamp uses. Stored uncut; the 30 km
cut is a v1.1 decision.

### Demand written (superseded — see review round 2)

| version | internal | external_in | external_out |
|---|---|---|---|
| p1-low | 356,126 | 71,349 | 46,088 |
| p1-central | 287,893 | 57,715 | 46,088 |
| p1-high | 247,003 | 49,523 | 46,088 |

Origins: internal at LSOA, external_in at MSOA. Destinations: LSOA, except
external_out at MSOA (or country).

### Review round 2 (user decisions and rebuild, 2026-09-27)

**Checkpoint and upstream.** `aad1bea` (round 1) is pushed. Upstream
`analysis-grade-correct` was merged into upstream `main` with `--no-ff` as `1c82c76`,
so `0ea228d` stays reachable. On `main` after the merge:
- the full upstream suite passes (64);
- a fresh single-threaded covid + assemble run on a DB copy matches the pre-refactor
  code on every table, with exact floats, and `demand_data.json` is byte-identical;
- the regression and `correct()` tests against that re-run DB pass (23, with the
  routing test skipped).

Upstream `main` was then pushed. The push moved origin from `93602a7` to `1c82c76`, so
it also published the earlier local-only upstream commits (G1–G8 and Cardiff).

**Decisions (user):**
- d is accepted as `[MODELLED]` with the midpoint central. P5 chooses the default
  between low, central and high on the station-usage, BUS01 and traffic-count gates,
  logging the fit for all three (SPEC §9).
- The average-weekday basis is accepted (SPEC §6.1), and external_out is put on the
  same basis.
- Zones are internal only if their population-weighted centroid is inside the extent.
  The cap becomes a check.
- The car-availability fallback gains an origin MSOA × destination LAD level.

#### d by industry: not possible from current data

- **ONS OPN has no industry breakdown.** Neither the hybrid supplementary tables
  (Jan–Mar 2025) nor the newer "working arrangements by personal characteristics"
  tables (1 Apr–28 Jun 2026, released 17 Jul 2026) break down by industry (SIC).
  Occupation is published, and varies a lot. Professional occupations: 30%
  travel-only, 41% hybrid. Elementary occupations: 78% travel-only, 1% hybrid. But
  BRES is classified by industry, not occupation, so there is no mix to weight by.
  Logged as SPEC §12 FUTURE ("industry- or occupation-varying attendance").
- **The 2026 edition adds a region breakdown.** South West: travel-only 44%, hybrid
  23%, home only 14%, neither 19% (660 respondents). England: 42 / 25 / 14 / 19.
  GB 2025 was 41 / 28 / 14 / 17. The 2026 tables give no full-time / part-time split,
  so the full derivation can't be re-run. Applying the formula to all-persons shares
  gives attendance of about 0.507 (GB 2025), 0.50 (England 2026) and 0.508 (South West
  2026). That shifts d by under 0.01, well inside the low–high range, so the accepted
  values are kept. File:
  https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/healthandsocialcare/healthandwellbeing/datasets/publicopinionsandsocialtrendsgreatbritainworkingarrangementsbypersonalcharacteristics/1aprilto28june2026/workingarrangementsbypersonalcharacteristics1aprilto28june2026.xlsx
  (accessed 2026-09-27, OGL v3).
- **Central-Bristol share of HBW trips** (destination LSOAs in Bristol 054, 060 and
  061). d is uniform, so there is no industry-varying version to compare against. The
  only change is from this round's reclassification:
  - the absolute central-Bristol trips are unchanged: 61,836 at central d (76,495
    low, 53,052 high);
  - their share of internal trips rises from **19.69% to 20.40%**, because the
    internal denominator shrinks;
  - their share of all trips to in-map destinations rises from 17.89% to 18.88%.

#### Zones by LSOA population-weighted centroid

Source: ONS ArcGIS `LSOA_PopCentroids_EW_2021_V4` (FeatureServer/0, on the same ONS
Open Geography server upstream uses; accessed 2026-09-27, OGL v3). Fetched by code for
the 772 extent LSOAs into `data/raw/ons_geo/lsoa21_pwc_extent.geojson`
(sha256 `a4a16e26f08fd3cd…`).

**729 of 772 LSOAs are internal; 43 move out.** The table lists their internal →
internal commuters under upstream's classification. Commuters between two moved LSOAs
are counted in both columns (529).

| MSOA | LSOAs moved | as origin | as destination |
|---|---|---|---|
| Bath and North East Somerset 021 | E01014383, E01014418 | 267 | 136 |
| Bath and North East Somerset 022 | E01014442, E01014443 | 250 | 6 |
| Bath and North East Somerset 023 | E01014394 | 110 | 28 |
| Cotswold 011 | E01034790 | 24 | 5 |
| Mendip 001 | E01029057 | 17 | 66 |
| Mendip 005 | E01029060 | 37 | 36 |
| Monmouthshire 009 | W01001542, W01001590 | 94 | 322 |
| Newport 001 | W01001625 | 46 | 278 |
| Newport 003 | W01001620 | 77 | 42 |
| Newport 004 | W01001639, W01001640, W01001641 | 201 | 69 |
| Newport 005 | W01001614 | 159 | 83 |
| Newport 006 | W01001682, W01001683 | 125 | 168 |
| Newport 007 | W01001677 | 68 | 0 |
| Newport 012 | W01001604 | 113 | 26 |
| Newport 017 | W01001631, W01001632 | 577 | 40 |
| Newport 020 | W01001912, W01001913 | 387 | 935 |
| North Somerset 023 | E01014772 | 196 | 95 |
| North Somerset 024 | E01014741, E01014742 | 214 | 189 |
| North Somerset 025 | E01014805 | 331 | 97 |
| Sedgemoor 001 | E01029128 | 68 | 57 |
| Sedgemoor 002 | E01032633 | 26 | 35 |
| South Gloucestershire 001 | E01014986, E01014989 | 133 | 423 |
| South Gloucestershire 002 | E01014991, E01014992 | 406 | 471 |
| South Gloucestershire 003 | E01014884 | 103 | 30 |
| South Gloucestershire 004 | E01014962 | 164 | 235 |
| Stroud 015 | E01022381 | 58 | 184 |
| Wiltshire 008 | E01031950 | 60 | 17 |
| Wiltshire 017 | E01031888, E01031929, E01031930 | 168 | 101 |
| Wiltshire 023 | E01032059, E01032061 | 138 | 47 |
| **total (43 LSOAs)** | | **4,617** | **4,221** |

**The base is now built from the national ODWP01EW OA file** (upstream's
`data/raw/odwp01ew/ODWP01EW_OA.csv`, read-only), not from upstream's `oa_flows`. Three
OAs belong to internal LSOAs but lie outside upstream's polygon-intersection extent.
Upstream's table only holds flows with an end in its extent, so it misses those OAs'
flows whose other end is also outside it. Under upstream's classification, the
national file reproduces upstream's `oa_flows` exactly: internal → internal 275,369
(zero difference in every destination MSOA), external_in 66,186, external_out 37,350.

**Every commuter is accounted for between the two classifications**
(`p1_reclass_flows`; "none" means external at both ends, so not a lab trip):

| was (upstream) | now (lab) | commuters |
|---|---|---|
| internal | internal | 267,060 |
| internal | external_in | 4,088 |
| internal | external_out | 3,692 |
| internal | none | 529 |
| external_in | internal | 151 |
| external_out | internal | 65 |
| outside upstream's flows | internal | 1 |
| external_in | external_in / external_out / none | 59,818 / 15 / 6,202 |
| external_out | external_in / external_out / none | 6 / 34,061 / 3,218 |
| outside upstream's flows | external_in / external_out | 100 / 124 |

**Internal → internal: 275,369 → 267,277.** That is −8,309 moved out and +217 moved
in; the build asserts the total. 9,949 commuters become external at both ends and
are dropped.

**Reconciliation step 4** (d = 0, no cap) switches to the lab's zones. Fixed-workplace
369,181; after the no-fixed-place step 444,860; median factor **1.505** over 157
destination MSOAs; **max 4.48**, against 74.9 at step 3. The two boundary slivers
were what upstream's cap had been catching.

#### The cap becomes a check; nothing binds

The correction now runs with `cap=None`. Any in-extent destination factor above
`commute.destination_factor_check` (4.0, [MODELLED], in `params/base.yaml`) fails the
build. Largest in-extent factors: **2.55 (low d), 2.07 (central), 1.77 (high)**.
Median factors: 0.859 / 0.694 / 0.596.

#### External_out on the average-weekday basis

Each external workplace MSOA gets a national factor: BRES × (1 − d) ÷ census
fixed-workplace inflow from all England and Wales origins. The inputs are the national
ODWP01EW MSOA file and upstream's paged national BRES file `bres_lsoa.csv` (35,672
LSOAs), both upstream raw downloads, read-only. The factor is applied to the base flow.
It already brings the destination total to BRES × (1 − d), so no-fixed-place uplift is
not added on top.

**National factors, 2,243 MSOAs:**

| d | median | max |
|---|---|---|
| low | 0.993 | 38.8 |
| central | 0.802 | 31.4 |
| high | 0.688 | 26.9 |

**Fallback, 2 destinations** (Scotland and Northern Ireland, 168 commuters): the
in-extent overall factor for that d, [MODELLED].

**External_out now varies with d:** 40,776 / 32,962 / 28,280 trips.

**Shown for review, not failed (superseded by the decision below — these now fall back):** some external workplaces have national factors above
4.0. At central d that's 3 MSOAs, 8 base commuters and 102 trips:
- Dacorum 017, ×31 (60,650 BRES jobs against 892 census arrivals, which looks like
  head-office registration, drawback 4);
- Trafford 024 and Hackney 026, both ×6.5.

At low d it's 15 MSOAs (160 base commuters, 814 trips), mostly central London. The
in-extent check does not cover them.

#### External factors above the check (user decision, 2026-09-27)

External workplace MSOAs whose national factor exceeds the 4.0 check are neither capped
nor failed. They fall back to the in-extent overall factor for that d [MODELLED], and
each is listed in the run log (`result.summaries.<variant>.ext_out.above_check`) with
its BRES jobs, census arrivals and raw factor.

**Why not a cap or a failure:**
- **Dacorum 017 (×31 at central d) is almost certainly head-office registration.**
  BRES records 60,650 jobs against 892 census arrivals. Nobody should be scaled up to
  match that.
- **The central London zones above 4.0 at low d may be genuine lockdown effects.**
  These include City of London 001 (675,600 BRES against 85,057 arrivals), Camden 028
  and Islington 022/023. London offices were hit hardest in March 2021.
- **A blanket cap would undercount London-bound rail commuters.** The fallback is a
  holding position. SPEC §11b (v1.1) replaces it with a test against Census 2011
  arrivals: high against 2011 means an artefact, high only against 2021 means a
  lockdown effect.

**Zones falling back:**
- central and high d: Dacorum 017, Trafford 024, Hackney 026;
- low d: those three plus 12 more, mostly central London (City of London 001, Camden
  028, Islington 022/023, Southwark 035, Tower Hamlets 015, Hackney 033, Waltham
  Forest 007), and Test Valley 017, West Suffolk 016, Bracknell Forest 013 and
  Birmingham 064.

**External_out trips:** 40,125 / 32,867 / 28,198 (low / central / high), from 40,776 /
32,962 / 28,280. Internal and external_in are unchanged.

#### Car-availability fallback

Share of base commuters (internal and external) at each level:

| level | cells | commuters | share |
|---|---|---|---|
| pair (≥ 10) | 7,343 pairs | 278,766 | **75.5%** |
| origin MSOA × destination LAD (≥ 10) | 23,258 pairs | 68,084 | **18.4%** |
| origin MSOA | 17,058 pairs | 22,331 | **6.0%** |

Round 1 put 25% on the origin fallback; 18.4 points of that now use destination-LAD
information. CA share of trips: internal 0.874, external_in 0.945, external_out 0.910.

#### Demand written (round 2)

Daily HBW trips:

| version | internal | external_in | external_out |
|---|---|---|---|
| p1-low (d 0.429) | 342,373 | 62,872 | 40,125 |
| p1-central (d 0.539) | 276,762 | 50,824 | 32,867 |
| p1-high (d 0.604) | 237,448 | 43,604 | 28,198 |

Origins: internal at LSOA, external_in at MSOA. Destinations: internal LSOA;
external_out at MSOA (or country). Distance for externals is now to the nearest
internal LSOA centroid. The reconciliation steps keep upstream's
nearest-in-map-OA-centroid definition.

## Phase status

| phase | status |
|---|---|
| P0 | done 2026-09-27 (`5be73ab`); committed and pushed to `git@github.com:iiAnderson/bristol-transit-lab.git` |
| P1 | approved 2026-09-27; upstream `correct()` merged to upstream `main` (`1c82c76`, from `0ea228d`); external-factor fallback added after approval |
