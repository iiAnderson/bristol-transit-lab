# Bristol Transit Lab

A sketch-planning tool for comparing public transport schemes in the Bristol–Bath city
region (bus, tram/light rail, heavy rail) on **connectivity** and **cost**. Indicative
and comparative only: good for ranking ideas and finding gaps, not for a business case.

## What v1 does

1. Builds a baseline network (`B2026`: OSM, BODS bus GTFS, rail GTFS from Darwin).
2. Applies a scenario (YAML ops: `add_line`, `modify_route`, `replace_route`,
   `remove_route`) to produce a routable network (`B2028` is a scenario on `B2026`).
3. Skims it with R5/r5r and reports, against the parent: jobs reachable in 30/45 min by
   PT, frequent-service coverage, residents better and worse off, track capacity and
   capex/opex (low/central/high).

`lab compare <scenario> <parent>` writes `compare.md`, `compare.json`, `change_oa.parquet`.
`lab map [scenario...] [--open]` builds one HTML map of the projects listed in `config/lab.yaml`
`map.projects` (each the latest `lab compare` of a scenario against its baseline). A sidebar ticks
projects on and off and picks the layer: minutes saved, why it improved, jobs newly within 45
minutes, time to the centre, a click-an-area 45-minute reach, the baseline gap map and the lines.
Needs an internet connection for the tiles.

## Not in v1

Demand beyond commute, mode choice, assignment, calibration gates, viewer, blog
components, external gateways: see `docs/DEFERRED.md`. Car-congestion calibration
(`src/lab/congestion/`) is frozen; car times are indicative.

## Start here

- Setup and working rules: `CLAUDE.md`
- Specification: `SPEC.md` (v1 = up to P3)
- Data provenance and phase findings: `sources.md`
- Plans: `plans/`
- Before the scope cut: git tag `archive/pre-v1-scope`

```sh
source env.sh && lab --help && pytest
lab scenario validate B2028
lab compare B2028 B2026   # scenario id, then what to compare against
```
