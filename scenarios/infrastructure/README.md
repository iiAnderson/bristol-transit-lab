# Rail infrastructure sections

Data for the track-capacity check and the existing-track run-time rule (plans/P3.md D6,
D8): sections between Darwin timing points, with track count, a `[MODELLED]` limit in
trains per hour per direction, any freight allowance, and junction type. One YAML file per
area; every file is hashed into `spec_hash` of the scenarios that use it.

Empty until P3b-3. Until then the validator knows no sections, so a scenario with an
`existing_rail` segment fails loudly.
