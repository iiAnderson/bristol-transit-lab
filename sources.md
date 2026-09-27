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
| Upstream Bristol DB | OA / LSOA / MSOA | `~/Documents/Projects/subwaybuilder-bristol/data/interim/BRS/census.duckdb`, `ons_to_subwaybuilder` 0.1.0 @ `2361e300`, sha256 `581da77d7dd7…` (full hash in every `run.json`) | 2026-09-27 | derived from OGL v3 sources; see upstream `sources.md` |

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

## Phase status

| phase | status |
|---|---|
| P0 | done 2026-09-27; committed and pushed to `git@github.com:iiAnderson/bristol-transit-lab.git` |
