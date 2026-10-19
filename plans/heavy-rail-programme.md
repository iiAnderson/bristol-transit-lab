# Heavy-rail programme — encoding brief (R1, R2, R3)

**For:** the Claude Code session that encodes these schemes as scenarios.
**From:** Robbie's design discussions (late September to October 2026). Supersedes the
heavy-rail parts of `plans/schemes.md` §2 where they differ; schemes.md keeps the
alternatives (tunnel variants 1–3, R1-alt).
**Diagrams:** `plans/heavy-rail-programme-*.png` (one per phase).

**Status of every figure here: a lead, not a source (rule 3).** Frequencies and calling
patterns are Robbie's design choices: encode them as `[MODELLED]` with "Robbie's design,
plans/heavy-rail-programme.md" as the reasoning. Station positions are approximate, read
off Robbie's sketch. Costs are rough chat estimates (2026 prices, about 40% risk added):
use them only to sanity-check the cost engine's output. Never copy them into
`params/costs.yaml`.

## How to work

1. Read this file, then SPEC §5 ("as built"), `scenarios/B2028.yaml`,
   `scenarios/infrastructure/*` and `params/costs.yaml`.
2. Check the brief against the repo. Record corrections in the plan log.
3. Before writing any scenario, **stop and report on the engine gaps in §6.** G1 and G2
   decide whether R1 can show anything at all. Propose a design for each and wait for
   approval.
4. Build one scenario per phase, each on the last:
   `R1-suburban-electric` (parent `B2028`), `R2-city-tunnel` (parent R1),
   `R3-northern-link` (parent R2). Add each to `map.projects` against its parent.
5. Draw every new alignment with `lab scenario draw-rail`. Plot the new stations and
   portals on a map for Robbie's approval before skimming.
6. Push only when Robbie asks; secret scan first.

## 1. Baseline (B2028, already built)

| Service | Calls (local stations) | tph today / B2028 |
|---|---|---|
| Severn Beach line | Temple Meads – Lawrence Hill – Stapleton Road – Montpelier – Redland – Clifton Down – Sea Mills – Shirehampton – Portway P&R – Avonmouth (– St Andrews Road – Severn Beach) | 2 to Avonmouth, 1 to Severn Beach (Darwin 23 Sep 2026: 35 trains a day each way at Clifton Down, 22 at St Andrews Road) |
| Henbury line | Temple Meads – Lawrence Hill – Stapleton Road – Ashley Down – Filton Abbey Wood – North Filton – Henbury | 1 (B2028) |
| Portishead line | Portishead – Pill – Parson Street – Bedminster – Temple Meads | 1 (B2028) |
| Weston line stoppers | Weston-super-Mare – Weston Milton – Worle – Yatton – Nailsea & Backwell – Parson Street – Bedminster – Temple Meads | about 2 — **measure from the B2028 GTFS** |
| Bath line stoppers | Temple Meads – Keynsham – Oldfield Park – Bath Spa | about 2 — measure |
| Gloucester line stoppers | Temple Meads – … – Filton Abbey Wood – Bristol Parkway – Yate | about 1–2 — measure |

Long-distance services (CrossCountry, GWR London, Cardiff – Taunton and the rest) are
unchanged in every phase. They stay in the capacity check and are not redrawn.

## 2. Phase 1 — `R1-suburban-electric`

The surface network: electric local services on Filton Bank, the Severn Beach line and
the Henbury line; Weston – Bath through-running at the surface to test the market.

### Works

| # | Work | Darwin sections (from → to, both directions) | Chat estimate (base) |
|---|---|---|---|
| W1 | Electrify the Filton Bank slow pair and the Henbury line | BRSTLTM–BRSTLEJ–DRDAYSJ–LAWRNCH–STPLTNR–STPLNAR–ASHYDN–HORFDJN–FILTNEW; FILTNEW–FILTNWJ–NRHFILT–HENBURY | £0.1–0.25bn (W1 + W2 together, about 70 single-track km) |
| W2 | Electrify the Severn Beach line | STPLNAR–MONPELR–REDLAND–CLFDOWN–SEMILLS–SHAMPTN–PTWYPR–AVONMTH–SADWJN–SADWRD–(Severn Beach) | (in W1) |
| W3 | Redouble the Severn Beach line, Narroways to Avonmouth | STPLNAR … AVONMTH. **Single track today apart from Clifton Down** (lead: Wikipedia, "Severn Beach line"; confirm the layout from a primary source). Avonmouth – Severn Beach stays single | £0.1–0.25bn |
| W4 | Henbury line works for 2 tph (turnback and signalling at Henbury) | FILTNWJ–NRHFILT–HENBURY | £0.03–0.08bn |
| W5 | Temple Meads: east-facing bays 0 and 1 (6-car; in the Temple Quarter plans) and the west-end bay reinstated | station | £0.1–0.3bn (with junction and signalling work) |
| W6 | Fleet and depot: about 10 four-car electric units, stabling and maintenance | — | £0.1–0.25bn |
| W7 | Tunnel development: route safeguarding, ground investigation, business case | — | £0.05–0.15bn |

**Phase total:** items £0.5–1.3bn base, **£0.7–1.8bn with 40% risk** (the earlier chat figure was £0.8–1.7bn).

### Services

| Route | Op | Calls | Headway min AM / IP / PM / OP | Change |
|---|---|---|---|---|
| Severn Beach line, to Severn Beach | `replace_route` (today's Severn Beach line services) | Temple Meads … Avonmouth – St Andrews Road – Severn Beach | 30 / 30 / 30 / 60 | 1 → 2 tph |
| Severn Beach line, to Avonmouth | `add_line` (same route as above, turning at Avonmouth) | Temple Meads … Avonmouth | 30 / 30 / 30 / 60 | with the above: 2 → 4 tph to Avonmouth |
| Henbury line | `modify_route` `headways_min` on the B2028 Henbury routes | as B2028 | 30 / 30 / 30 / 60 | 1 → 2 tph |
| Weston – Bath (surface) | `add_line`, all `existing_rail` | Weston-super-Mare – Weston Milton – Worle – Yatton – Nailsea & Backwell – Parson Street – Bedminster – Temple Meads – Keynsham – Oldfield Park – Bath Spa | 30 / 30 / 30 / 60 | new, 2 tph, today's bi-mode fleet |
| Portishead | unchanged | as B2028 | 60 | terminates in the west bay |

Option (a separate variant, not in the base): Henbury – Portishead through-running,
which Network Rail and GWR are studying.

## 3. Phase 2 — `R2-city-tunnel`

A twin-bore tunnel under the centre carries Weston – Bath at 4 tph.

### Alignment (approximate; refine against OSM and plot for approval)

| Point | Approx. lon, lat | Notes |
|---|---|---|
| South portal | −2.6040, 51.4352 | On the old four-track formation between Parson Street and Bedminster. Electric-only gradient about 3–4%, so a ramp of about 250–350 m |
| North Street (new, underground) | −2.6048, 51.4410 | Southville. Has to pass well under the New Cut, so it will be deep |
| Centre (new, underground) | −2.6000, 51.4525 | College Green / Harbourside side. Made ground over the culverted Frome |
| Broadmead (new, underground) | −2.5905, 51.4560 | Newgate side of Castle Park. Keep boxes and shafts out of the scheduled castle remains |
| Temple Meads East (new) | −2.5795, 51.4528 | Anvil Street area, about 300 m from the Temple Meads platforms; walking link via the new eastern entrance |
| East portal | about −2.5700, 51.4480 | St Philip's Marsh (industrial land); thread around the GWR depot |
| Junction onto the Bath line | about −2.5660, 51.4490 | Grade-separated, before the Avon at Netham |

Tunnel length: about 4.5 km. `alignment_type: tunnel` from portal to portal, with
the station legs as tunnel stops.

### Works

| # | Work | Chat estimate (base) |
|---|---|---|
| W8 | Twin-bore tunnel, portals and systems (about 4.5 km) | £1.0–1.9bn |
| W9 | Four stations: North Street, Centre, Broadmead (underground), Temple Meads East | £1.0–2.0bn |
| W10 | Parson Street junction, grade-separated (tunnel portal and Portishead branch) | £0.15–0.4bn |
| W11 | St Philip's Marsh junction onto the Bath line, grade-separated, **with the Phase 3 link's flyover abutments built now** | £0.15–0.4bn |
| W12 | Electrify the Weston line, Parson Street – Weston-super-Mare (both tracks) | £0.08–0.2bn |
| W13 | Bath line: battery-charging or part wiring (the Sydney Gardens listed structures make full wiring hard), plus a passing loop for 4 tph stoppers among fast trains | £0.1–0.25bn |
| W14 | Fleet: about 8 more units (battery-electric for Bath) | £0.1–0.2bn |
| W15 | Land, property (south portal) and development | £0.2–0.4bn |

**Phase total:** items £2.8–5.8bn base, **£3.9–8.1bn with 40% risk** (earlier chat figure £4–7.5bn). Tunnel and stations are about 70% of it.

### Services

| Route | Op | Calls | Headway AM / IP / PM / OP | Change |
|---|---|---|---|---|
| Weston – Bath (tunnel) | `replace_route` of R1's surface Weston – Bath | Weston-super-Mare – Weston Milton – Worle – Yatton – Nailsea & Backwell – Parson Street – North Street – Centre – Broadmead – Temple Meads East – Keynsham – Oldfield Park – Bath Spa | 15 / 15 / 15 / 30 | 2 → 4 tph; Bedminster no longer called |
| Severn Beach, Henbury | unchanged from R1 | | | |
| Portishead | unchanged, surface to Temple Meads | keeps Bedminster served | 60 | |

Options (variants, not base): reopen Saltford and St Anne's Park on the Bath line.

## 4. Phase 3 — `R3-northern-link`

A link from the tunnel's east end to Filton Bank sends Portishead trains through the
centre to Parkway and Yate.

### Works

| # | Work | Chat estimate (base) |
|---|---|---|
| W16 | Northern link, about 1 km of new double track: St Philip's Marsh junction to the Filton Bank slow pair at Lawrence Hill, grade-separated (abutments from W11) | £0.15–0.4bn |
| W17 | Electrify Filton Abbey Wood – Bristol Parkway – Yate (FILTNEW–BRSTPWY–WSTLGHJ–YATE) | £0.05–0.15bn |
| W18 | Portishead branch: passing loop for 2 tph, and electrification | £0.1–0.25bn |
| W19 | Temple Meads: a third east-facing bay, or more through-running instead | £0.05–0.15bn |
| W20 | Fleet: about 4 more units | £0.05–0.1bn |

**Phase total:** items £0.4–1.1bn base, **£0.6–1.5bn with 40% risk**.

**Programme:** items £3.7–8.1bn base, **£5.1–11.3bn with 40% risk** (earlier chat figure £5.4–10.6bn). TAG
A1.2 stage 1 uplifts (tunnels 55%, stations 70%, rolling stock 61%) are higher than
the 40% used here, so the engine should come out above these.

### Services

| Route | Op | Calls | Headway AM / IP / PM / OP | Change |
|---|---|---|---|---|
| Portishead – Yate (tunnel) | `replace_route` of the B2028 Portishead service | Portishead – Pill – Parson Street – North Street – Centre – Broadmead – Temple Meads East – Lawrence Hill – Stapleton Road – Ashley Down – Filton Abbey Wood – Bristol Parkway – Yate | 30 / 30 / 30 / 60 | 1 → 2 tph, through the centre |
| Yate – Temple Meads (surface) | `modify_route` on today's Gloucester-line stoppers to 2 tph, if they are below it | as today | 30 / 30 / 30 / 60 | check against B2028 first |
| Weston – Bath, Severn Beach, Henbury | unchanged from R2 | | | |

Tunnel loading: south end 6 tph (4 Weston + 2 Portishead), which is about its limit
without loops or four-tracking on the Weston line. East end: 4 tph to Bath + 2 tph north.

Option (variant): once the Henbury and Severn Beach lines are both electrified and
double, a loop Temple Meads – Filton – Henbury – Avonmouth – Clifton Down – Temple Meads
in place of the Henbury and Avonmouth terminators.

## 5. Decisions Robbie has not made (default in bold)

1. R1 Weston – Bath surface service: **added on top of today's Weston and Bath
   stoppers**, or replacing Temple Meads – Weston shuttles where they exist?
2. R3 Bedminster: once Portishead trains move into the tunnel, Bedminster keeps only
   whatever GWR stoppers still call. **Report the residents affected**; closure is not
   assumed.
3. Severn Beach beyond Avonmouth: **2 tph** (needs a passing point or redoubling there
   too, or 2 tph on single track is checked by the capacity rule).

## 6. Engine gaps to report before encoding

- **G1 — works on existing track have no op.** Electrification, redoubling, passing
  loops, new bay platforms and grade-separated junctions on existing lines can be
  neither costed nor seen by the capacity check: infrastructure files are global, not
  per scenario, and `rail_works` items are only reached through new alignments. Propose a
  scenario-scoped op (for example `infrastructure: {sections: …, tracks: 2,
  electrified: true}`, `junction: {…, type: grade_separated}`, `platforms: {…}`) that
  overrides the capacity files for that scenario and its children and is costed from
  `rail_works`. Stop for approval.
- **G2 — the base layout is wrong on single-track lines.** `capacity.yaml` defaults to
  two tracks and lists only the Portishead branch as single. The Severn Beach line
  (Narroways – Avonmouth, single apart from Clifton Down; to confirm) and possibly
  parts of the Henbury line are treated as double today. That would make 4 tph look
  possible in B2028, and W3 buy nothing. Correct the base, from a primary source, before
  R1 is encoded.
- **G3 — electric fleet speed.** Existing-track run times are today's diesel and
  bi-mode working times (SPEC §11 item 23). Keep that; state it on R1–R3.
- **G4 — new sections.** The portals, tunnel stations, the St Philip's Marsh junction and
  the northern link are not in `darwin_sections.yaml` or `untimed_sections.yaml`. Add them
  to `untimed_sections.yaml` as hand-authored `[MODELLED]` entries with new timing-point
  ids, so the speed-profile rule and the capacity check reach them.
- **G5 — cost placeholders.** `rail_works.track_doubling`, `grade_separated_junction`
  and the heavy-rail lease rate are `[PLACEHOLDER]`, so R1–R3 costs will be marked
  incomplete until they are sourced. Report what is missing. Don't fill gaps from §2–4.
- **G6 — offsets.** Every generated service in R1–R3 takes the three phase offsets
  unless phase-anchored (SPEC §5). The Henbury 2 tph re-timing divides the B2028
  headway, so it should anchor; check it does.
- **G7 — not modelled at all.** Temple Meads platform occupation and turnback times,
  junction conflicts (`junctions: []`), freight beyond the allowance. Say so on every
  output.
