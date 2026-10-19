# Heavy-rail programme (R1–R3): plan log

Brief: `plans/heavy-rail-programme.md`, diagrams `plans/heavy-rail-programme-*.png`.
Opened 2026-10-18. **Status: engine work built (§5); R1, R2 and R3 written, validated, built and checked for
capacity and cost (§7), skimmed and compared (§8), and on the map.**

Read first: the brief, SPEC §5 (as built), `scenarios/B2028.yaml`,
`scenarios/infrastructure/*`, `params/costs.yaml`, and the code the gaps touch
(`src/lab/capacity.py`, `cli/scenario.py::_capacity`, `costs.py`, `generator.py::_retime`).

## 1. Corrections to the brief (checked against the repo and B2028's feeds)

Baseline measured on B2028 offset 0 (`data/interim/scenarios/B2028/97406e0a7fc0`, rail + generated
feeds, 23 Sep 2026), counting vehicle journeys by `original_trip_id`.

| # | Brief says | Repo says |
|---|---|---|
| C1 | Severn Beach line 2 tph to Avonmouth, 1 to Severn Beach; 35 trains a day at Clifton Down, 22 at St Andrews Road | **Confirmed.** 35 a day each way at Clifton Down (busiest hour 2), 34 at Avonmouth, 21–22 at Severn Beach |
| C2 | Weston line stoppers about 2 tph | **About 2.5 tph** (45 a day each way at Nailsea & Backwell; AM 2.3–3.3, IP 2.5) |
| C3 | Bath line stoppers about 2 tph | Confirmed: 35 a day each way at Keynsham, 32 at Oldfield Park, about 2 tph |
| C4 | Gloucester line stoppers "about 1–2", R3 raises them to 2 tph "if below" | **Already about 2 tph**: 31 and 33 a day at Yate, AM 2.0, IP 1.8. The diagram's "1 tph (≈)" is low. The R3 `modify_route` is not needed as drawn |
| C5 | Henbury and Portishead 1 tph | Confirmed: 19 and 17 trains a day each way |
| C6 | Local services are separate lines (Severn Beach, Weston stoppers, Bath stoppers, Yate) | **Wrong, see G9.** Route ids are origin–destination pairs and much of the local service already runs through Temple Meads |
| C7 | Weston – Bath surface through-running is **new**, 2 tph (R1) | **11 journeys a day each way already run** (`GW:WSM-BTH`, `GW:BTH-WSM`), about 0.5 tph. R1 adds to or replaces this. This is brief §5 decision 1 and needs your answer |
| C8 | Henbury line "possibly parts" single track | **Double.** MetroWest Phase 2 GRIP 1/2 report (WoE, July 2015, Appendix A) calls the Henbury route "twin tracked" and says the line carries freight to the Avonmouth bulk terminal. The default of two tracks in `capacity.yaml` is right there |
| C9 | Severn Beach line "single track today apart from Clifton Down" (a lead) | Secondary sources (Wikipedia; Bristol Rail Campaign) say single from Narroways Hill Junction with **passing points at Clifton Down and Avonmouth**, and a 300 m single-track section at St Andrews Road crossing north of Avonmouth. The GRIP report does not state the Severn Beach layout. **Not confirmed from a primary source** (Network Rail's Sectional Appendix has not been opened) |
| C10 | W2/W3 chain ends `SADWJN–SADWRD–(Severn Beach)` | All chains in W1–W3 and W17 exist in `darwin_sections.yaml`. Beyond SADWRD the next timing point is `HLSMTHJ`, not a Severn Beach code; the last section to name is `SADWRD–HLSMTHJ`. To check when drawing |
| C11 | G5: the heavy-rail lease rate is a `[PLACEHOLDER]` | **No: `opex.heavy_rail.lease_per_unit_year` is `[SOURCED]`** (£606,000 a three-car diesel unit a year, WECA MetroWest Phase 2 FBC update, Oct 2025) and so is `staff_per_unit_year`. What is a placeholder: `rail_works.track_doubling`, `grade_separated_junction`, `stations.heavy_rail.bay_platform`, `heavy_rail_vehicle`. The rate is for a three-car **diesel** unit; the brief wants four-car electric and battery units |
| C12 | G1: `rail_works` items "are only reached through new alignments" | **Never reached at all.** `costs.py` does not read `rail_works.*`; for `existing_rail` it only writes the note "no works costed there (electrification, doubling and junction works are not scenario ops yet)". Electrification in tunnels uses `tunnel.electrification_per_track_km` |
| C13 | G6: the Henbury 2 tph re-timing should anchor | **Confirmed in code.** `_retime` anchors when the new headway divides the old (`divides(30, 60)`), takes no offset, and reuses the existing journeys' stop times. It re-times only the dominant stop pattern per direction. Check on the real build |
| C14 | G2: Severn Beach treated as double | **Confirmed**, and worse: see G2 below. Today the Severn Beach sections carry 2 tph a direction in every hour, and no block or loop is modelled |

Diagram note: the services diagrams label the second column "off-peak"; the brief's headways have a separate
IP and OP. Read the diagram's off-peak as OP (evening). Not a model issue.

## 2. Engine-gap report

### G1: works on existing track have no op (confirmed; design proposed)

What is true today:
- `capacity.yaml`, `darwin_sections.yaml`, `untimed_sections.yaml` are read from one global directory
  (`cfg.root / raw["baseline"]["infrastructure_dir"]`); a scenario cannot change them.
- `costs.capex` never prices work on `existing_rail` (C12).
- The capacity check only looks at sections named in the `timing_points` of a generated or extended leg.

**Proposed design: a new op `works`**, scenario-scoped, inherited by children:

```yaml
- works:
    name: W3 redouble Narroways to Avonmouth
    track:      {sections: [STPLNAR-MONPELR, MONPELR-REDLAND, ...], tracks: 2}   # redoubling: adds a track
    electrify:  {sections: [...], tracks: 4}                  # which tracks of those sections
    loops:      [{between: CLFDOWN-SEMILLS, at_km: 1.2, length_m: 400}]          # a passing loop
    junction:   {at: PARSNST, type: grade_separated}
    platforms:  {at: BRSTLTM, kind: bay, count: 2}
```

- **Capacity:** `_capacity` overlays the scenario chain's `works` on `capacity.yaml` (track counts, blocks, limits),
  so R1's redoubling lifts the limit for R1 and every child, and not for B2028.
- **Cost:** each item is priced from `rail_works`: electrification per single-track km (RIA, sourced),
  doubling per km, junction each, bay platform each (the last three are placeholders today, so the cost is
  marked incomplete and the lines are listed as not costed; nothing is filled from the brief).
- **Length of a section:** needs one new generated column, `length_km` in `darwin_sections.yaml`, from the OSM
  track between the timing points (the same graph `draw-rail` uses). Without it the engine can't price per km.
- **Validation:** one named rule per failure (unknown section, section named twice, tracks lower than today,
  electrify more tracks than exist, loop outside its block), like the other ops.
- **Not built, said on every output:** junction conflicts, platform occupation (G7).

Cost: about 250 lines, one new test file, one `lab scenario validate` rule set. **Needs your approval.**

### G2: the base layout is wrong on single-track lines (confirmed; the proposed fix is bigger than the brief says)

Marking the Severn Beach sections `tracks: 1` is **not enough**. `capacity.limits` applies
`60 ÷ (2 × run + 2 × 3)` to each Darwin section on its own. Measured median run times on the line are 1.5 to
4 minutes a section, so even as single track every section would allow 6 or more trains an hour each way and
4 tph would still pass. Single-track capacity is set by the **block between passing places**, not the station
pair. From the Darwin run times (no dwells), with the passing places taken from the secondary sources in C9:

| Block (passing place to passing place) | One-way run | Limit, `60 ÷ (2 × run + 6)` | Today |
|---|---|---|---|
| Narroways – Clifton Down (STPLNAR–MONPELR–REDLAND–CLFDOWN) | 5.5 min | 3 tph | 2 |
| Clifton Down – Avonmouth (CLFDOWN–SEMILLS–SHAMPTN–PTWYPR–AVONMTH) | 10.0 min | 2 tph | 2 |
| Avonmouth – Severn Beach | not measured here | | 1 |

So today's 2 tph sits **at** the modelled limit on the long block, and 4 tph fails: the behaviour the brief
expects. Intermediate dwells (about 0.5 to 1 min a station) make the limits slightly lower.

**Proposed design:** add `blocks` to `capacity.yaml`: lists of consecutive sections that share one limit
(`tracks: 1`, passing places at the ends), limit computed from the summed Darwin run times of the block; a `works`
loop splits a block, and a `works` redoubling turns it into double track. Sections outside any block keep the
per-section rule. Portishead (Parson Street to Pill to Portishead) would become one block as well, which
fixes the same understatement there. Every number stays `[MODELLED]` until Network Rail's Sectional Appendix is
opened.

**Decisions I need on G2:**
1. The passing places: is "Clifton Down and Avonmouth" right (secondary sources), or only Clifton Down (the
   brief)? Can you point me at a primary source?
2. Avonmouth – Severn Beach: the brief keeps it single. The 300 m single section at St Andrews Road in the
   sources suggests the rest may be double. Which?
3. The Henbury line stays double (C8), with the freight allowance as now.

The capacity table already has no limit below "today's busiest hour plus freight", so after the fix the
Severn Beach blocks report a floor where they are at today's 2 tph; I'd keep that (it is how the check avoids
failing a line for the trains it carries today).

### G3: electric fleet speed (agreed)
Existing-track run times stay today's diesel and bi-mode working times; the caveat (SPEC §11 item 23) is
printed on R1–R3 through the scenario `caveats`.

### G4: new sections (agreed, with one addition)
Add the portals, tunnel stations, St Philip's Marsh junction and the northern link to
`untimed_sections.yaml` as `[MODELLED]` entries with new timing-point ids. **Addition:** the validator
checks `timing_points` against known sections only for `existing_rail` segments; a tunnel or new-build
segment may carry `timing_points` (the generator reads them) but they are not validated. I propose validating
them for every alignment type, so a typo in a tunnel leg fails loudly instead of dropping the section out of
the capacity check.

### G5: cost placeholders (corrected, see C11)
Will make R1–R3 incomplete. Missing, as the library stands:
- `rail_works.track_doubling` (W3, W13 loop), `grade_separated_junction` (W10, W11, W16), `bay_platform`
  (W5, W19), passing loop (no item), signalling (no item), **electrification of existing track**
  (the rate is sourced but no op reaches it: G1), part-wiring or battery charging (no item);
- heavy-rail fleet: a **lease** rate exists (sourced, diesel, three-car); electric and battery units have
  none, so the engine would apply the diesel rate to four-car electric units, which I'd refuse: I propose the
  fleet line be listed as not costed rather than approximated;
- tunnels and underground stations: the library has costs but its underground station figures are upper
  bounds only (see the P3 plan log).
I will not fill any gap from the brief's §2–4.

### G6: offsets (checked, see C13)
Re-timing the B2028 Henbury routes anchors. The new Portishead – Yate `replace_route` (R3) at 30 minutes
replaces a 60-minute service, so it divides and anchors too. R1's Weston – Bath and Severn Beach services are
new or replace something at a different phase: they take the three offsets.

### G7: not modelled (agreed)
Temple Meads platform occupation and turnback times, junction conflicts (`junctions: []`), freight beyond the
allowance. Added to every scenario's `caveats`.

### New gaps found while checking

**G8: the capacity check cannot see re-timed routes.** `_capacity` counts a re-timed route's trains **at
today's paths** (`base_use(tp, set())` when any `gen:` trip is in the rail feed) and `scenario_use` only reads
`add_line`, `replace_route` and `extend_to` legs. A `modify_route.headways_min` therefore adds trains the check
never counts: R1's Henbury 1 → 2 tph would pass whatever the limit. Proposed fix: count a re-timing's
journeys over the timing points of the stop times they copy (the same `base_use` on the new journeys),
and remove the replaced ones. Small, in `capacity.py`.

**G9: today's local services are through services, and route ids are origin–destination pairs.** Measured on
B2028, calls and journeys per day (both directions summed):

| Calls at | Route ids and journeys a day |
|---|---|
| Clifton Down (Severn Beach line) | SVB–BRI 19, BRI–AVN 12, **WSM–SVB 12**, **AVN–WSM 11**, BRI–SVB 9, SVB–WSM 2, and 5 singles: 70 journeys, of which **28 run through to the Weston side** |
| Nailsea & Backwell (Weston line) | WSM–NWP 16, NWP–WSM 13, WSM–SVB 12, AVN–WSM 11, **BTH–WSM 11, WSM–BTH 11**, BRI–WSM 7, WSM–BRI 4, SVB–WSM 2, and singles |
| Keynsham (Bath line) | TRO–BRI 19, BRI–TRO 15, **YAE–TRO 11, TRO–YAE 10**, TRO–NWP 4, BRI–BTH 3, NWP–TRO 3, BPW–TRO 2, and singles |
| Yate | BRI–YAE 21, YAE–BRI 21, YAE–TRO 11, TRO–YAE 10 |

Consequences for the brief:
- "Replace today's Severn Beach line services" spans **about 11 route ids**, and 40% of those trains also
  provide Weston-line service. Replacing them with Temple Meads–Severn Beach shuttles would **remove** Weston
  stopping trains in R1 unless they are put back.
- The Weston – Bath and Yate – Trowbridge through services already exist (C7, C4).
- `replace_route` takes one route id.

**Proposed handling (needs your choice, brief §5 decision 1 is part of it):** add a `route_group` to the
scenario (a name and a list of route ids) that `replace_route` and `modify_route` accept in place of one id,
and encode R1 as follows: the Severn Beach line group is replaced by the new 4 + 2 tph pattern, the through
Weston trains are kept as Weston – Temple Meads stoppers, and the existing Weston – Bath journeys are folded
into the new 2 tph service. I'd rather agree this than guess.

## 3. Decisions I am waiting for

1. **G1:** approve the `works` op (design above), or amend it.
2. **G2:** approve blocks in `capacity.yaml`, and answer the three layout questions (passing places,
   Avonmouth – Severn Beach, a primary source if you have one).
3. **G8:** approve counting re-timed journeys in the capacity check.
4. **G9:** approve `route_group`, and say how R1 should treat the existing through trains (brief §5 decision 1:
   add Weston – Bath on top of today's, or replace the existing 11 journeys a day and the Weston – Temple Meads
   shuttles; my default is replace, since they are the same pattern).
5. **G4:** approve validating `timing_points` for every alignment type.
6. Whether to commit this log, the brief and the diagrams now (nothing is committed or pushed).

## 4. Not done, on purpose
No scenario, no alignment drawing, no `map.projects` entry, no skim. After approval the order in the brief
stands: G1/G2/G8/G9 engine work with tests, then R1, R2, R3 each on the last, with new stations and portals
plotted for your approval before any skim.

## 5. Engine work done (2026-10-18)

Built as approved, with these changes from the proposal:
- **Lengths come with the op, not from the infrastructure files.** `works` items carry `length_km`
  (from `lab scenario draw-rail`): junction timing points have no coordinates, so a `length_km` column in
  `darwin_sections.yaml` could not be generated for them. A `was` track count is required on `track` items and
  must match the capacity data, which makes G2 and G1 check each other.
- **`route_ids`** (G9) rather than a named `route_group`: `replace_route` takes a list and makes one service;
  `modify_route` over a list expands to one op per route in the loader, so cost and capacity see the same ops.
- **G8** also stops the capacity check treating a re-timed route "at today's paths": the replaced journeys now
  leave the base and the new ones are counted over the timing points they copy (through any chain of copies).
- **Not done:** the Portishead branch stays on the per-section rule (see sources.md); Avonmouth – Severn Beach
  has no block (the sources disagree on it). Both are decisions for you.

Check on real data: B2028 unchanged; T002 now over on the 2 tph Clifton Down – Avonmouth block (sources.md).

Still open for R1: the passing places and Avonmouth – Severn Beach (G2 questions 1 and 2), how R1 treats
through trains (default: replace Weston – Bath, keep the Weston stoppers), and the Portishead block (R3).

## 6. Avonmouth – Severn Beach: research and a separate project (2026-10-18)

Asked for: further research on the part of the line beyond Avonmouth, and a separate selectable project in
the map. Answers G2 questions 1 and 2.

**What the sources say**
- *Primary.* Network Rail Signalling Design Group, Signalling Feasibility Report 139797 v1.1 (June 2015),
  Appendix H of the MetroWest Phase 2 GRIP 1/2 report (travelwest.info, 9.-GRIP2-App-H-Signalling-Feasibility-report.pdf):
  "The single line from St Andrews Road to Severn Beach is dual operated by both panels"; "Alterations would be
  required to the one train working to Severn Beach"; the single line is worked tokenless with sequential track
  circuits. The passenger line from Avonmouth to Severn Beach is controlled from St Andrews signal box. The same
  report: level crossings at Avonmouth, Avonmouth Dock Junction and St Andrews Road are fully signaller-controlled;
  road closure times are already extended at St Andrews Road and "the frequency of road closures will increase" with
  every added train.
- *Secondary.* Bristol Rail Campaign (9 Mar 2023): Clifton Down and Avonmouth have "double-tracked loops, and two
  platforms"; "The rest of the line between Narroways Junction and Severn Beach is single track"; north of Avonmouth
  the track is double through the station and level crossing and "reduces to single track for 300m through St
  Andrews Road level crossing". It cites Network Rail's Greater Bristol Rail Network Strategic Study (Feb 2023),
  which "suggests new or extended Severn Beach branch passing loops and reinstated platforms" (the study itself was
  not opened). WECA scheme summary (June 2019): MetroWest Phase 1 = half-hourly to Avonmouth, hourly to St Andrews
  Road and Severn Beach.
- The 300 m "single through the crossing" is not reconciled with the primary source's "single line from St Andrews
  Road": both put the single line beyond Avonmouth's station, which is all the model needs.

**Decisions taken (change them if you disagree)**
1. Passing places: **Clifton Down and Avonmouth.**
2. Avonmouth – Severn Beach: **one single-track block** (`AVONMTH–SADWJN–SADWRD–HLSMTHJ–SVRNBCH`), so the
   branch carries one train at a time. Limit from Darwin run times (9.2 min + 1.5 dwell, margin 3): **2 tph**.
   Today's Severn Beach service (22 a day each way, at most 2 in the busiest hour) is within it.
3. **Project S002** "Severn Beach line, half-hourly to Severn Beach": the four routes whose trips end or start at
   Avonmouth are extended over the branch at today's working times, so the branch gets 2 tph and nothing is added
   between Narroways and Avonmouth. It is a separate entry in `config/lab.yaml` `map.projects`.

**Result of the capacity check:** the branch sits exactly **at** its modelled limit (2 of 2) and no section is over,
at all three offsets. Not modelled, said in the scenario's caveats: the level crossings, Severn Beach layover,
loops and platforms the strategic study suggests, the signal box.

Still open: Portishead branch block (R3); how R1 treats through trains.

## 7. R1, R2, R3 built (2026-10-18)

Scenarios `R1-suburban-electric` (parent B2028), `R2-city-tunnel` (R1), `R3-northern-link` (R2); results and findings are in
sources.md "Heavy-rail programme R1–R3 built, not skimmed". Added to `map.projects` as `pending: true`.

**Corrections to what I said earlier**
- *My stated default for brief §5 decision 1 was wrong.* I said "replace the existing Weston – Bath journeys"; the brief's bold
  default is **added on top of today's Weston and Bath stoppers**. R1 does that.
- *The existing "Weston – Bath" services are limited-stop* (`GW:WSM-BTH`, 7 calls: WSM WNM WOR YAT NLS BRI BTH), not stoppers; I
  called them a through-running stopping service. They stay untouched.

**Engine additions needed to encode the brief:** `truncate_at` (so the through trains end at Temple Meads and the Weston
stoppers are unchanged), a per-feature `speed_profile`, and `traction` in the cost engine (electric units are not priced at
the diesel lease). SPEC §5 "as built at the heavy-rail programme build".

**Decisions taken on the brief's defaults (say if you want any changed)**
1. Weston – Bath surface service added on top; the through trains cut back at Temple Meads (§5 decision 1, default).
2. Bedminster: closure is not assumed; the residents affected are reported (sources.md).
3. Severn Beach beyond Avonmouth: 2 tph, at the single-track branch's modelled limit (§6).
4. Portishead passing loop modelled as a second track (the branch is not a block: an open decision, see §5).
5. R3 does not add a Yate-line `modify_route`: it already runs about 2 tph (C4).
6. The brief's costs, positions and frequencies are `[MODELLED]`; no brief figure is in `params/`.

**External rail passengers** (the gateway work, fb7b0a9): covered in sources.md. The comparison for each phase will add the onward-leg
figures (trips 2+ minutes faster, person-hours saved a weekday). Gateways touched: R1 72% of external rail trips, R2 70%, R3 53%.

**To approve before skimming:** the plot `runs/_review/hr/stations.html` (png alongside): portals, the four underground
stations, the junction, the link. Positions are approximate.

## 8. Skimmed and on the map (2026-10-19)

Approval of the station plot was taken as given when you asked for the map. All three phases ran after a bug fix.

**Bug.** `_retime` built the OP window as one window from the first to the last off-peak journey, spanning the day; Henbury got
43 journeys a direction with duplicates and R5 failed in `StopSequence.transferTime`. Fixed (early and late OP windows) and
tested; no earlier result was affected. Details and the refreshed capacity figures: sources.md.

**Results** are in sources.md (table). Headline: the programme as a whole makes 308,146 residents 2+ minutes faster to the
centre and 681,933 faster to jobs (AM), nobody slower, and 14,349 of 48,713 external rail trips 2+ minutes faster. Most of
the effect is frequency spilling across the network, as with the Supertram (S001). The Temple Meads east throat is over its
modelled limit in every phase.

**Map.** Four heavy-rail projects: the three phases (each against its parent) and the whole programme against B2028
(`id: heavy-rail-programme`, cost shown by phase).

**Still open:** Portishead as a block (R3's loop is a second track), the Henbury and Avonmouth loop variant, costs (track
doubling, junctions, loops, bays, electric unit lease, tunnel track and electrification, the elevated link are placeholders),
and the 390 s dwell at Temple Meads on through-running services.
