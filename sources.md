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

## P2 — Baseline supply and skims

Working plan and progress log: `plans/P2.md`.

### Datasets

| dataset | URL | accessed | licence | version / notes |
|---|---|---|---|---|
| BODS vehicle locations, archived (SIRI-VM) | https://data.datalibrary.uk/transport/BODS-ARCHIVE/sirivm/ | 2026-09-27 | OGL v3 (BODS data). Attribution: archive by Open Innovations for the National Data Library; data from the DfT Bus Open Data Service | National `siri.xml` every ~30 s (filenames UTC), archived since 18 Jun 2025; known outage 28 Jul–27 Aug 2026. Used: Tue–Thu 8–10, 15–17, 22–24 Sep 2026, 07:00–16:00 local; clipped to the extent + `supply.clip_buffer_km`; national files never stored. Per-snapshot SHA-256 in `data/raw/avl/archive/manifest.jsonl`, per-day file hashes in `days.jsonl`. Fetched at ≤ 1 request / 5 s under the policy in `config/lab.yaml` |
| BODS regional timetables, archived (GTFS) | https://data.datalibrary.uk/transport/BODS-ARCHIVE/timetables/ | 2026-09-27 | OGL v3, attribution as above | Daily regional GTFS; `itm_south_west_gtfs_20260923.zip` (01:30 on 23 Sep) is the modelled-date bus feed (A4, not yet downloaded) |
| BODS API, SIRI-VM datafeed | https://data.bus-data.dft.gov.uk/api/v1/datafeed/ | 2026-09-27 | OGL v3 (BODS terms) | Live, 10 s polling over the clip box, 29 Sep–1 Oct 2026 07:00–16:00, for the spacing-bias test only. Key registered 2026-09-27, held outside the repo. API docs are behind the BODS login |

| Darwin Push Port timetable (`_v8`) and reference (`_ref_v4`) | supplied by Robbie: `PPTimetable_20260923020537_*` (generated 02:05 on 23 Sep); dev fixture `PPTimetable_20260927020529_*` | 2026-09-27 | **NRE OGL** (OGL v2.0 with NRE amendments); attribution to National Rail as data provider required — https://www.nationalrail.co.uk/developers/darwin-data-feeds/ | SHA-256 `6d85e50e…` (`_v8`), `18d94f33…` (`_ref_v4`); in the feed registry |
| NaPTAN rail access nodes | https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv&atcoAreaCodes=910 (API listed on https://beta-naptan.dft.gov.uk/download) | 2026-09-27 | OGL v3 | 2,768 rows (2,716 `RLY`); `9100<TIPLOC>` → coordinates; SHA-256 `e9febab7…` |
| MobilityData GTFS validator | https://github.com/MobilityData/gtfs-validator/releases/tag/v8.0.1 (`gtfs-validator-8.0.1-cli.jar`) | 2026-09-27 | Apache 2.0 | SHA-256 `19293ddd…`; run with `-c gb -d <modelled date>` |

| DfT travel time measures: SRN (England) and local 'A' roads (Great Britain), Apr 2025 – Mar 2026 | release page https://www.gov.uk/government/statistics/travel-time-measures-for-the-strategic-road-network-england-and-local-a-roads-great-britain-april-2025-to-march-2026; tables zip https://assets.publishing.service.gov.uk/media/6a72ecfe8a340ed57ba476fe/travel-time-measures-on-srn-local-a-roads-apr-2025-mar-2026.zip | 2026-09-27 | OGL v3 | published 2026-08-06; SHA-256 `bbfec682…`; tables CGN0404/0405 (SRN), CGN0503–0510 (local A roads, England, GB, Scotland, Wales) |
| OSM, five Geofabrik extracts | `…/england/{bristol,somerset,gloucestershire,wiltshire}-260926.osm.pbf`, `…/wales-260926.osm.pbf` (resolved from `-latest`) | 2026-09-27 | ODbL 1.0 | replication timestamp 2026-09-26T20:22:51Z for all five; MD5 checked against Geofabrik's `.md5`; merged and clipped to the clip box with osmium 1.19.1 (`complete_ways`): 5,804,074 nodes, 959,590 ways. OSM date (26 Sep) ≠ modelled date (23 Sep): accepted, both recorded |

| DfT road traffic statistics, AADF by direction | https://storage.googleapis.com/dft-statistics/road-traffic/downloads/data-gov-uk/dft_traffic_counts_aadf_by_direction.zip (from https://roadtraffic.dft.gov.uk/downloads) | 2026-09-27 | OGL v3 | 2000–2025, last-modified 2026-06-03; SHA-256 `6941be90…`. Clip box: 1,002 count points, 434 in 2025 (incl. Newport 33, Monmouthshire 12); on A roads mostly estimated (2025: 457 of 534 `PA` points estimated) |
| ONS Rural Urban Classification 2021, OAs, England and Wales | https://geoportal.statistics.gov.uk/datasets/ons::rural-urban-classification-2021-of-output-areas-in-ew/about (CSV via the geoportal download API, item `ed33e08c…`) | 2026-09-27 | OGL v3 | 188,880 OAs; covers **all 3,799 internal OAs incl. Wales** (urban 3,458; rural 341). Note DfT's urban/rural split uses the 2011 definition (settlement ≥ 10,000), not RUC 2021 |
| National Highways WebTRIS | https://webtris.nationalhighways.co.uk/api/v1.0 (swagger at `/api/swagger/docs/v1`) | 2026-09-27 | OGL v3 | 486 sites in the clip box, 362 active; 15-min reports 1 Jul 2025 – 30 Jun 2026 (data ends ~3 months back: 30 Jun 2026 was the last day with data on 27 Sep). England only |
| Bristol City Council ANPR journey times ("Historic journey times", now "Journey Counts" + "Journey Links") | https://maps2.bristol.gov.uk/server2/rest/services/ext/Traffic/MapServer/3 (counts), `/2` (240 links); ArcGIS items `b9d7bc32…`, `059b8dad…` | 2026-09-27 | OGL v3 | The old Open Data Bristol page (`…/datasets/5e20d884…`) is gone (404). 5-minute ANPR speeds weighted by plate matches, **Dec 2018 – Dec 2024**. Post-2020, so usable for held-out validation of absolute speeds, but it ends 21 months before the modelled date. Fetched in P2b |
| DfT Transport connectivity metric 2025 | https://www.gov.uk/government/publications/transport-connectivity-metric → https://assets.publishing.service.gov.uk/media/68c966fc07d9e92bc5517b80/connectivity_metrics_2025.ods | 2026-09-27 | OGL v3 | V1.0.0, version date 2025-08-21, experimental; timetables Q4 2024; **England and Wales**; OA/LSOA/LAD/RGN scores 0–100 by purpose × mode. SHA-256 `4f6589ae…`. The 1 GB `content.xml` is streamed to CSV (`src/lab/supply/ods.py`). The PT employment column is labelled "Business (public transport)" |
| Welsh trunk-road speeds | — | 2026-09-27 | — | **None published.** Traffic Wales is live-only; M4 speeds appear only in ad-hoc FOI releases (e.g. https://www.gov.wales/atisn18581). Welsh trunk roads keep transferred factors (D8). The M4 J24–J28 through Newport has 50 mph average-speed enforcement: free-flow speeds there must honour it |

### P2b / early P2c datasets (2026-09-27 – 28)

| dataset | URL | accessed | licence | version / notes |
|---|---|---|---|---|
| ONS LAD boundaries, December 2024, BGC | https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Local_Authority_Districts_December_2024_Boundaries_UK_BGC/FeatureServer/0 | 2026-09-27 | OGL v3 | the seven authorities with internal LSOAs; Dec 2024 because DfT names authorities as at 1 January of the data year (2025) |
| ONS LSOA 2021 boundaries, BGC V5 | https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5/FeatureServer/0 | 2026-09-27 | OGL v3 | 729 internal LSOAs; areas from `Shape__Area` (BNG m²) |
| ONS LSOA 2021 population-weighted centroids (clip box) | https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/LSOA_PopCentroids_EW_2021_V4/FeatureServer/0 | 2026-09-28 | OGL v3 | 890 within the clip box (729 internal + 161 external), skim and accessibility destinations |
| ONS OA 2021 population-weighted centroids | https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/OA_December_2021_EW_PWC_V4/FeatureServer/0 | 2026-09-27 | OGL v3 | all 3,799 internal OAs (upstream's `pwc` lacks 3 whose own centroid is outside the extent); identical to upstream's on the 3,796 shared |
| ONS RUC 2021, LSOAs | https://geoportal.statistics.gov.uk/api/download/v1/items/9dbf7613cbb147b8bb8627ddb3568cff/csv?layers=0 | 2026-09-27 | OGL v3 | area types (D4) |
| DfT TRA0307, TRA0306 | https://www.gov.uk/government/statistical-data-sets/road-traffic-statistics-tra | 2026-09-27 | OGL v3 | TRA0307 2025: traffic index by hour × day of week, GB all roads — the flow profile for like-for-like all-day speeds (DfT's own finer weights are unpublished) |
| DfT travel time statistics, background quality report | https://www.gov.uk/government/publications/road-congestion-and-travel-time-statistics-information/travel-time-statistics-background-quality-report | 2026-09-27 | OGL v3 | average speed = Σ(length·N)/Σ(journey time·N), N by hour, day type, month, road type and urban/rural; Inrix GPS cars and vans; free flow = 85th percentile capped at NSL (delay tables only) |
| Bristol ANPR journey times (Journey Counts / Links) | https://maps2.bristol.gov.uk/server2/rest/services/ext/Traffic/MapServer/3 (and `/2`) | 2026-09-28 | OGL v3 | **hourly** rows (not 5-minute): `SPEED` in mph, `JOURNEY_TIME` in s, `TOTAL_MATCHES`; 2019 1.06M rows → 2023 583,769 → 2024 119,246 (to 12 Dec); 240 links. The service takes native SQL date literals (`DATE_TIME >= 'YYYY-MM-DD'`); ArcGIS `TIMESTAMP`/`date` literals fail. 2023–24 fetched for absolute validation |
| Legislation: RTRA 1984 s.81(1); SI 2022/800 (W. 178) | https://www.legislation.gov.uk/ukpga/1984/27/section/81 ; https://www.legislation.gov.uk/wsi/2022/800/made | 2026-09-27 | OGL v3 | 30 mph restricted roads; 20 mph in Wales from 17 Sep 2023 (free-flow defaults) |

### PT waiting in generalised cost (TAG M3.2, 2026-09-28)

| document | URL | accessed | licence | version / notes |
|---|---|---|---|---|
| TAG unit M3.2 Public Transport Assignment Modelling | https://assets.publishing.service.gov.uk/media/666af32effd07973a043d110/tag-unit-m3.2-public-transport-assignment-modelling.pdf (from https://www.gov.uk/government/publications/webtag-tag-unit-m3-2-public-transport-assignment-modelling) | 2026-09-28 | OGL v3 | May 2024 (page last updated 30 May 2024); SHA-256 `d080d610…` |

Used: Table 1 indicative weights (walk/access/egress/transfer 1.5–2.0; waiting 1.5–2.5;
IVT rail 1, bus 1–1.4; boarding/transfer penalty 2–10 min); §3.2.3–3.2.9 waiting (half
the headway for short headways; wait curves for the first boarding of infrequent
services; transfer waits exact in timetable models; capped curves only with care).
Figure 2's illustrative wait curve, digitised by eye (±0.5 min): headway 10 → 5.0, 20 →
9.5, 30 → 11.5, 40 → 13.0, 60 → 15.5, 90 → 19.5 min. PDFH B4 curves (membership) not used.

Effect on the P2 PT skims: median GC −2.7 min (AM) / −2.9 min (IP), p90 −10 min, from
the first wait on infrequent services; median wait in the skims is 13.4 min (16% of
journey time), rail-led 15.6 min.

### Road class and the car network (P2b, 2026-09-28)

- **OSM `trunk` ≠ SRN.** OSM marks many locally managed A roads as `trunk` (A38, A37,
  A4174, A403, …). Class comes from DfT's count-point categories, **propagated along
  the road** (network distance over same-`ref` links): by A-road length, 69.7% by count
  point, 21.8% propagated, 8.5% OSM fallback. Class changes on exactly three roads —
  A36, A4 (both B&NES / Bristol) and A4042 (Newport) — at nodes listed in the run.
- **WebTRIS directions are nominal** (the M4 "westbound" runs at 331° near Almondsbury);
  sites are matched to the carriageway side within 90°; 309 of 323 mainline sites match.
- **No PM bus data from the archive** (07:00–16:00 window); PM local shape is national
  until the live 16:00–19:00 data (29 Sep – 1 Oct) replace it where coverage allows.

### First Bristol 5/77 (A4, resolved 2026-09-28)

On the modelled day vehicles on 5 and 77 were bound for Transport Hub (`010000012`), none
for Black Boy Hill (`0100BRP90986`); the Black Boy Hill variant (60 trips, each with an
exact-duplicate copy) is excluded by trip pattern via config. On 8 Sep the opposite held —
service `6606` (to Transport Hub) began on 22 Sep — which is why the modelled-day
vehicle data, not an earlier day, had to decide.

### Job-weighted destination points (D3 test, 2026-09-27)

Census 2021 ODWP01EW OA file (upstream raw, read-only; `ODWP01EW_OA.csv`), place of work
indicator 3 ("Working in the UK but not working at or from home", 15,095,659 workers in
E&W), totalled by OA of workplace; each internal LSOA's point is the workplace-weighted
mean of its OAs' population-weighted centroids. Lockdown depresses the counts' level;
only the within-LSOA distribution is used. Result (run
`20260927T205257-spike-d3-d38ffb`): flow-weighted median |Δ| over internal HBW pairs is
1.0 min / 3.1% (PT) and 0.38 min / 3.4% (car), below the 2 min / 5% thresholds, so
**HBW destinations stay at LSOA PWCs**.

### Car routing engine (D2, 2026-09-27)

**OSRM, native** — Homebrew `osrm-backend` **26.9.0** (bottled for arm64 Sequoia;
https://github.com/Homebrew/homebrew-core/blob/HEAD/Formula/o/osrm-backend.rb),
BSD-2-Clause. **Docker is not used.** Profile: the formula's `car.lua`. MLD pipeline
(`osrm-extract` → `osrm-partition` → `osrm-customize --segment-speed-file`, served by
`osrm-routed --algorithm mld` on a probed free port, never 5000).

Measured:
- **Direction test** (the D2 synthetic road, 5.013 km): segment speeds 16 km/h A→B and
  97 km/h B→A give 18.83 min and 3.08 min — exact, per direction, no workaround.
- **Clip** (5.80M nodes): extract 7.2 s / 0.8 GB peak; partition 0.9 s; customize 0.35 s
  (so each period's speed file costs about a second); full 3,799 × 729 OA → LSOA table
  9.0 s, no unroutable cells, free-flow median 26.5 min.

**Why not R5 for car** (spike runs `20260927T194301-spike-d2-a3be8b`,
`20260927T194305-spike-d2-9a3b88`): R5 7.5.1 honours `maxspeed` and
`maxspeed:forward` but **ignores `maxspeed:backward`**. The only workaround — splitting
each two-way way into two one-way ways — is directional but adds an unexplained
**1–6 min per 5 km** on a synthetic 5 km road (worse when the two carriageways are drawn
apart). A real-way test could not discriminate: 29 of 40 routes took 0 min at R5's minute
resolution, and slowed ways were bypassed on parallel streets.

### r5r for PT cost components (D7, 2026-09-27)

| item | source | accessed | licence | version / notes |
|---|---|---|---|---|
| R (conda-forge `r-base` 4.5) + OpenJDK 21, separate env `transit-lab-r` | `environment-r.yml` | 2026-09-27 | GPL-2/3 (R) | kept apart from the Python env. **Correction:** the D7 approval note assumed R was already in the toolchain for UK2GTFS; it was not — UK2GTFS was dropped at the repo check when rail moved to Darwin. The separate env is the fix (approved 2026-09-27) |
| r5r | https://cran.r-project.org/package=r5r | 2026-09-27 | MIT | **2.4.0** (published 2026-05-20), pinned via `remotes::install_version`; pins R5 **7.5.1** (`onLoad.R`: `r5_jar_version <- "7.5.1"`), the same R5 version as r5py 1.1.7 (`~/.cache/r5py/r5-v7.5.1-r5py-all.jar`) |

Measured on the D3 1% sample (38 OAs × 729 LSOAs, Wed 23 Sep 08:00, 60-min window):
expanded matrix 6.2 s (1.48M rows, one per OD × departure minute). With `max_rides = 8`
(r5r's default is 3; r5py's 8) p50 totals match r5py: same reachable pairs, r5r − r5py
median +0.5 min, p5–p95 0.0–0.9. Components (access + wait + ride + transfer + egress)
sum to `total_time` within 0.05 min except walk-only rows, where r5r leaves them at 0.

### Bus GTFS (A4, 2026-09-27)

Inputs: the NDL archive's `itm_south_west_gtfs_20260923.zip` (SHA-256 `cb0026b2…`) and
`itm_wales_gtfs_20260923.zip` (`b8fb8573…`), fetched one at a time during a pause of
the SIRI-VM fetch. Both are BODS feed version `20260922_02424x`, feed start 22 Sep 2026,
so they cover the modelled date (A4 checks 1 and 3).

`lab supply bus` (run `20260927T191653-supply-bus-ef66a1`): 58,922 trips active on
Wed 23 Sep in the two feeds → 12,798 calling in the extent → 3,664 exact duplicates
removed → 198 left with fewer than two calls in the clip box → **8,936 trips, 286,130
stop times, 5,818 stops**; 32,420 calls beyond the box cut. Validator: 0 errors,
128 warnings (90 missing bike allowance, 17 duplicate route names, 15 unexpected enum
values, 3 long short names, and the single-date ones).

- **Duplicates are mostly whole-route doubling.** 62 of 262 routes lost more than 10%:
  First Bristol 41, FlixBus 16, National Express 4, Newport Bus 1. Where one route ID
  runs on the date, trips are doubled exactly by overlapping service calendars
  (e.g. First 75: 256 → 128). A route name can cover several real services (First "1"
  is a Bath circular, Bristol–South Gloucestershire and a Weston route); none of them
  duplicate each other. FlixBus repeats each trip up to ~11× (UK940: 344 → 31).
- **Unresolved variants (kept, listed):** 66 groups of trips leave the same first stop at
  the same minute but differ later, 61 of them on First 5 and 77. The longer variant
  (to "Transport Hub", `010000012`) belongs to service `6606` (calendar_dates only,
  Mon–Thu from 22 Sep); the shorter ends at Black Boy Hill (`0100BRP90986`). On 8 Sep
  vehicles on 5 and 77 were bound for Black Boy Hill, but that predates `6606`. To be
  resolved from the 23 Sep vehicle destinations when the archive fetch reaches them.
- **Check 2** (23 Sep vs Wed 7 Oct, same feeds): every local operator's route counts are
  identical; only FlixBus raw counts differ (duplicate multiplicity).
- **Welsh coverage (D8 measurement).** The South West file gives the Welsh zones only
  coaches and 4 Newport Bus trips. The Wales file gives Newport Bus 751 trips on 61
  routes and Stagecoach South Wales 442 trips on 8 routes. Every line observed running
  in the Welsh zones in the 8 Sep vehicle positions (Stagecoach 18 lines, Newport Bus 4)
  is present in the Wales timetable. Newport Bus reports few vehicles to BODS (10 seen
  in the 8 Sep morning), so its vehicle-location coverage is thin even though its
  timetable is complete.

### DfT local 'A' road speeds: actual grain (A7, 2026-09-27)

The plan assumed a table by local highway authority × period × urban/rural. It
doesn't exist. What the release publishes:

| table | grain | measure |
|---|---|---|
| CGN0503d (England), CGN0509b (Wales) | local authority × year | all-day average speed |
| CGN0503e (England), CGN0509c (Wales) | local authority × A road × year | all-day average speed |
| CGN0503a, CGN0505b (by nation), CGN0509a (Wales) | month (national) | all day, urban, rural, weekday AM, IP, PM, off-peak |
| CGN0404d | SRN link (junction to junction, by direction) × year | average speed |

Definitions (CGN0503 notes): flow-weighted; cars and light vans only; **all day = 24
hours over all days including weekends and bank holidays**; urban = settlement of
10,000+ (RUC 2011); AM 07–10, IP 10–16, PM 16–19 weekdays (school holidays included).
2025 carries a series break (sample change).

2025 all-day speeds for the extent's authorities (mph): Bristol 15.3, Bath and North
East Somerset 23.0, South Gloucestershire 24.3, North Somerset 28.0, **Newport 26.2,
Monmouthshire 27.0**. Road-level rows: Bristol 17, B&NES 12, South Glos 12, North
Somerset 7, Newport 7, Monmouthshire 8.

- **This is the first release to cover Great Britain.** Welsh authorities (Newport,
  Monmouthshire) now have authority- and road-level targets, so D8's "no car-speed
  calibration data in Wales" is no longer true for local A roads.
- **The authority target is all-day, not by period.** A like-for-like modelled figure
  needs speeds for every hour of every day, flow-weighted: AM and IP from the model,
  plus PM, off-peak and weekend speeds and an hourly flow profile. Period shape can only
  come from the national monthly split (by nation, urban/rural) and the bus data.

### Rail GTFS (A5, 2026-09-27)

`lab supply rail` on the 23 Sep snapshot: 34,059 journeys on the date → 613 trips kept in
the clip box, 3,343 stop times, 37 stations, operators AW, GW, XC. Excluded: 7,456 not
passenger, 2,101 timetabled buses (BS), 868 bus replacements (BR), 360 run-as-required,
221 ships, 109 charters, 86 cancelled journeys, 1 deleted; 658 cancelled calls dropped
(the snapshot was generated on the day, so it carries short-notice cancellations); 3
public calls at a junction with no CRS dropped. GTFS validator: 0 errors, 3 warnings
(all from a single-date feed built by the lab).

- **Temple Meads reconciles:** 436 journeys call there = 435 eligible + 1 cancelled call
  (23rd) or 1 bus replacement (28th); 3 of the 435 have no other call inside the box
  (Temple Meads ↔ Devon non-stop), leaving 432.
- **Correction to the repo-check record:** Portishead (`PRTSHD`, POH), Henbury (`HENBURY`,
  HBR) and Pill (`PILL`, PIA) are all in the Darwin reference with no services, and
  North Filton is **Bristol Brabazon** (`NRHFILT`, BBZ), also unserved on 23 and 28 Sep.
  The earlier "Portishead and North Filton not in the reference data" was a name
  mismatch. NaPTAN lists Bristol Brabazon as an *active* station and Darwin gives it an
  operator (GW), which suggests an imminent opening.
- **Pilning** is unserved on weekdays (Saturday-only service): configured as such.

### School calendars (A6 day check, 2026-09-27)

All nine archive days (8–10, 15–17, 22–24 Sep 2026) and the live days (29 Sep–1 Oct) are
term time in all four councils; no bank holidays fall in September. Term 1 2026/27:

| council | term 1 | source |
|---|---|---|
| Bristol | 3 Sep – 23 Oct 2026 | https://www.bristol.gov.uk/residents/schools-learning-and-early-years/school-term-and-holiday-dates |
| South Gloucestershire | 1 Sep – 23 Oct 2026 | https://consultations.southglos.gov.uk/gf2.ti/f/1719074/238668389.1/PDF/-/School%20Term%20Holiday%20Dates%202026-27.pdf |
| North Somerset | 3 Sep – 23 Oct 2026 | https://n-somerset.gov.uk/my-services/schools-learning/local-schools/school-term-dates/2026-27-term-dates |
| Bath and North East Somerset | 2 Sep – 23 Oct 2026 | https://www.bathnes.gov.uk/school-term-dates |

INSET days are set by each school (and academies set their own terms), so no council
calendar can rule them out; a scattered INSET day is not a network-wide effect.

### P2b round 2 findings (2026-09-28)

- **Zero-distance AVL legs.** Once slow legs away from stops are kept, 0.2% of traversal rows have matched distance 0 (both fixes snapped to one point). Their speed is undefined and they zero harmonic means, so they are dropped.
- **ANPR hour stamp most likely marks the end of the hour.** Journey-time peaks sit at labels 9 and 18 in both GMT and BST; the volume profile matches TRA0307 with SSE 0.39 (r 0.84) under hour-end vs 1.32 (r 0.30) under hour-start. Not confirmed by Bristol City Council.
- **Signal delays do not remove the central IP bias.** The fitted centre delay is 0 s, and urban delays (24 s per approach) are offset by the DfT level fit. ANPR validation half IP: 0.86.
- **Bus noise floor.** Random half-splits differ by a median 6.1% (≈ 3% sampling error for a full cell). Held-out errors (cell 27%, corridor 19%) are far above it, so they are model structure, not sampling noise.

### Live AVL: spacing bias and PM shape (2026-10-08)

- **BODS SIRI-VM vehicles report about every 30 s**, whatever the polling rate: live (10 s polls) median gap between fixes 30 s, archive (30 s snapshots) 31 s.
- **30 s spacing bias is small:** thinned ÷ full bus speed median 0.993; worst road class × area cell 0.982; 0.973 where 4+ stops lie within 100 m. Below the 5% threshold, so archive speeds are not corrected.
- **PM/IP bus ratios** (live Tue 29 Sep – Thu 1 Oct 2026, 16:00–19:00 vs 10:00–16:00) are 0.92–1.00 by direction × area × road group, replacing the national 0.939 [CALIBRATED from live data; national fallback unused]. Stagecoach West's partial outage on 30 Sep reduces its share of these days.

### Findings

- **SIRI-VM records are often stale.** In the 07:00 BST snapshot of 23 Sep, 102 of the
  598 positions in the clip box were over an hour old; median age 22 s. Positions are
  de-duplicated per vehicle and old ones flagged (`stale`, `age_s`), not dropped, at
  fetch time; B2 applies the filter.
- **Newport Bus (`NWPT`) appears in BODS vehicle locations** (15 vehicles in that
  snapshot), although the plan assumed BODS covers England only. Relevant to D8; the
  timetable side is measured in A4.

## Phase status

| phase | status |
|---|---|
| P0 | done 2026-09-27 (`5be73ab`); committed and pushed to `git@github.com:iiAnderson/bristol-transit-lab.git` |
| P1 | approved 2026-09-27; upstream `correct()` merged to upstream `main` (`1c82c76`, from `0ea228d`); external-factor fallback added after approval |
| P2 | P2a in progress: A0 done (`7525703`); A6 archive fetch running |
