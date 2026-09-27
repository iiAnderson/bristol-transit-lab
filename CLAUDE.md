# Bristol Transit Lab — CLAUDE.md

Read `SPEC.md` in full before planning anything, then upstream's `sources.md` and
`GENERALISATION.md` (`~/Documents/Projects/subwaybuilder-bristol/`), then this repo's
`sources.md`. Phase status is recorded at the end of `sources.md`.

## Setup

```sh
conda env create -f environment.yml     # exact versions: environment.lock.yml
source env.sh                           # PATH + JAVA_HOME (the JDK inside the env)
pip install -e .
lab --help
pytest
```

## Working rules (SPEC.md §0)

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
