# Rail infrastructure sections

Data for the track-capacity check and the existing-track run-time rule (plans/P3.md D6,
D8): sections between Darwin timing points, with track count, a `[MODELLED]` limit in
trains per hour per direction, any freight allowance, and junction type. One YAML file per
area; every file is hashed into `spec_hash` of the scenarios that use it.

- `darwin_sections.yaml` — generated from the Darwin timetable by `lab supply
  rail-infrastructure`: every pair of consecutive timing points in use on the modelled date.
- `untimed_sections.yaml` — hand-authored: line with no timed train today (freight-only or
  stations not yet built), each entry with its source or its `[MODELLED]` flag.

Limits, track counts, junction types and freight allowances are added in P3b-3 with the
capacity check. The validator reads `from` / `to` from every `*.yaml` here.
