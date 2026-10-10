# Candidate schemes

**Status:** design notes from Robbie's chat discussions (late September to 10 October
2026), written up outside the repo. They describe schemes to build as test scenarios
once the P3b engine exists. **Nothing here is a source.** Every figure is a lead under
rule 3: costs, frequencies, gradients, dates and precedents must be opened in a primary
source before they enter `params/` or `scenarios/`. Station codes, route IDs and
coordinates belong in `scenarios/`, never in `src/` (rule 6).

Costs below are rough chat estimates at 2026 prices with a ~40% risk allowance. TAG
A1.2 stage 1 uplifts (tunnels 55%, stations 70%, rolling stock 61%) are higher, so
these figures are probably low.

---

## 1. Baseline the schemes build on (B2028)

As decided at the P3b opening stop:
- Portishead line (Portishead, Pill), hourly to Temple Meads;
- Henbury-line extension: North Filton (= Bristol Brabazon) from November 2026, Henbury
  from March 2028, hourly (WECA MetroWest Phase 2 FBC update, October 2025);
- sensitivity run without Henbury.

---

## 2. Heavy rail: electrified suburban network and city tunnel

The preferred family. Built in three phases, each a scenario on top of the last.

### Phase 1 — surface network (R1)

- **Electrify** Temple Meads – Filton Bank slow pair – Narroways – Severn Beach line,
  and the Henbury line.
- **Redouble the Severn Beach line** to Avonmouth. Avonmouth – Severn Beach stays
  single unless demand justifies it.
- **Services:**
  - Severn Beach line (via Clifton Down) to Temple Meads, 4 tph to Avonmouth, 2 tph
    beyond;
  - Henbury 2 tph;
  - Weston – Bath through-running at the surface, 2 tph, existing bi-mode fleet
    (tests the market before the tunnel);
  - Portishead 1–2 tph to Temple Meads via a west-facing bay.
- **Option:** Henbury – Portishead through-running (Network Rail and GWR are studying
  it), which removes terminating moves at Temple Meads.
- **Temple Meads:** new east-facing platforms 0 and 1 (in Temple Quarter plans, 6-car)
  plus the reinstated west-end bay.
- **Fleet and depot:** about 10 electric units.
- **Tunnel development** (safeguarding, ground investigation, business case) runs
  alongside.
- **Chat estimate:** £0.8–1.7bn.
- **Variant R1-alt:** Robbie's original idea of a new local double track Parson Street –
  Temple Meads with fast trains on a dedicated pair, and Weston – Henbury stoppers
  through-running. Superseded once the tunnel carries the Weston stoppers, but worth
  keeping as a no-tunnel comparison.

### Phase 2 — city tunnel and Weston – Bath at 4 tph (R2)

**Alignment** (Robbie's sketch, two revisions):
- south portal on the old four-track formation between Parson Street and Bedminster;
  EMU-only gradients of about 3–4% give a ramp of roughly 250–350 m;
- **North Street** (Southville), deep enough to pass under the New Cut;
- **Centre** (College Green / Harbourside side);
- **Broadmead** (Newgate side of Castle Park, keeping boxes and shafts out of the
  scheduled castle remains);
- **Temple Meads East** (Anvil Street area, about 300 m from the Temple Meads platforms,
  next to the Temple Quarter campus; walking link via the new eastern entrance);
- tunnel surfaces east of the station in **St Philip's Marsh**, then a grade-separated
  junction onto the Bath line before the river. Robbie first proposed coming off at
  St Anne's; St Philip's Marsh was suggested instead because St Anne's is constrained
  (housing, the Avon at Netham). Open point.
- Build the Phase 3 junction structures (flyover abutments, alignment) in Phase 2.

**Services:**
- Weston – Bath via the tunnel, 4 tph: Weston-super-Mare, Weston Milton, Worle, Yatton,
  Nailsea and Backwell, Parson Street, North Street, Centre, Broadmead, Temple Meads
  East, Keynsham, Oldfield Park, Bath Spa;
- Bedminster dropped from tunnel trains (North Street is about 600 m away);
- Portishead stays on the surface to Temple Meads (keeps Bedminster served);
- options: reopen Saltford and St Anne's Park (probably needs a Bath-line passing loop
  at 4 tph);
- Bath route electrification is hard (listed structures around Sydney Gardens), so
  battery trains charging on wired sections are the likely option.

**Chat estimate:** £4–7.5bn (tunnel about 4.5 km twin-bore with four stations is the
bulk).

### Phase 3 — northern link and Parkway/Yate restructure (R3)

- Link from the St Philip's Marsh junction to Lawrence Hill and the Filton Bank slow
  pair.
- Portishead via the tunnel to Yate, 2 tph; Bath keeps 4 tph.
- Tunnel south-end limit is about 6 tph (4 on the Weston line + 2 on the Portishead
  line); more needs loops or partial four-tracking on the Weston line.
- Electrify Parkway – Yate; Portishead passing loop and wiring.
- Option: Temple Meads – Filton – Henbury – Avonmouth – Clifton Down – Temple Meads loop
  once both lines are electrified and double-tracked.
- Temple Meads needs a third east-facing bay, or more through-running, at 8–10 tph
  terminating from the east.
- **Chat estimate:** £0.6–1.4bn. **Whole programme:** £5.4–10.6bn.

### Earlier service pattern (pre-Bath branch), for reference

From the network diagram before the Bath branch was added:

| Line | Route | tph |
|---|---|---|
| A | Weston – Henbury via tunnel | 4 |
| B | Portishead – Yate via tunnel | 2 |
| C | Avonmouth – Temple Meads via Clifton Down (2 to Severn Beach) | 4 |
| D | Yate – Temple Meads | 2 |

Fast services assumed (approximate, check against Darwin): CrossCountry to Taunton
2 tph, GWR Cardiff – Taunton 1, Gloucester – Bath 1, London – Weston 1.

### Tunnel alternatives considered

Keep as variants:
1. **Bypass:** Bedminster portal – Queen Square – Broadmead – Lawrence Hill; skips
   Temple Meads. Chat estimate £2–3.5bn.
2. **Through Temple Meads low level** (Liverpool Link style): Bedminster – Redcliffe –
   Temple Meads low level – Broadmead – Lawrence Hill. £2.5–4.5bn.
3. **One-way single-track city loop** (Wirral Loop style): Temple Meads low level –
   Queen Square – Broadmead – Temple Meads, hanging off Filton Bank services; capacity
   about 8 tph. £1.8–3bn.

### Known constraints to test

- Filton Bank slow pair is needed by MetroWest locals; don't hand it to trams.
- Freight on the Portishead branch, Henbury – Avonmouth, Filton Bank (D6 allowance).
- **Weston line (two-track) is the tightest section:** about 8 tph mixed stoppers and
  125 mph trains; a Yatton passing loop adds margin.
- Grade separation likely at Parson Street (portal + Portishead branch) and at the
  Lawrence Hill / St Philip's Marsh junctions; Dr Day's junction conflicts for
  Filton Bank locals reaching Temple Meads.
- Centre station in made ground over the culverted Frome; high groundwater.
- Portal gradient and North Street depth are checkable with the DEM.

---

## 3. Tram and light rail

### T1 — Supertram replica

Broadmead – Temple Meads – Filton Bank corridor – Parkway – Aztec West, about 16 km.
The original alignment beside the railway was probably taken by the Filton Bank
four-tracking. Chat estimate £2.5–3.5bn. Conflicts with the heavy-rail plan.

### T2 — tram-train

About 9 km street core (Temple Meads – Redcliffe – Centre – Broadmead – Old Market) with
ramps onto the rail lines; still needs the Phase 1 rail works. Chat estimate
£3.5–6bn. Not favoured: low capacity per vehicle, slower outer journeys, freight
sharing, Sheffield's tram-train cost overrun.

### T3 — urban network, about 50 km

Where the railway doesn't go. Chat estimate £5.5–8.5bn.

| Line | Route | km |
|---|---|---|
| Core | Temple Meads – Redcliffe – Centre – Broadmead – Old Market | ~4 |
| South | Temple Meads – Bedminster – A38 – Hengrove – Bristol Airport | ~15 |
| North | Broadmead – Stokes Croft – Gloucester Road – Southmead Hospital – Brabazon – Cribbs Causeway | ~13 |
| North-east | Broadmead – Stapleton – UWE Frenchay – Parkway | ~10 |
| South-east | Temple Meads – Arnos Vale – Brislington – Hicks Gate P&R | ~8 |

### T4 — combined package (the main comparison)

Rail Phase 1 (R1) + tram core, south and north lines (about 30 km). Chat estimate
£3.5–5bn for the tram part. Compared against R1 + R2 (rail + tunnel).

---

## 4. North-east and south-east corridors

Areas with no railway: Easton, Fishponds, Staple Hill, Kingswood, Downend, Emersons
Green, Frenchay; Brislington, Knowle, Stockwood, Whitchurch, Hengrove, Hanham.

| Corridor | Options |
|---|---|
| Bristol – Bath Railway Path | Tram beside the path (only with a better cycle path; Staple Hill tunnel is narrow); heavy-rail branch competes for tunnel paths |
| A420 Kingswood | Underground only (Jacobs 2019 costed ~10 km) |
| M32 / UWE | Tram (T3 north-east) or MetroBus M3 upgrade |
| A4 Bath Road | Tram or BRT to Hicks Gate (Jacobs 2019 costed both) |
| Bath line stations | Reopen St Anne's Park, Saltford with 4 tph Weston – Bath |
| South Bristol | Line via Knowle, Hengrove, Hartcliffe to the airport |
| All radials | Bus franchising + priority on A420, A432, A4, A37, feeding rail |

---

## 5. Comparisons to run

- **Levers from the UK tram review:** reach into the centre, share of segregated
  running, buses feeding vs competing.
- **R1 vs R1 + R2:** how much benefit the tunnel buys.
- **R2 Weston – Bath pattern:** 4 tph via tunnel vs splitting Weston between Bath and
  Henbury.
- **Tunnel variants 1–3** vs Robbie's alignment: journey time to Broadmead, trips
  needing a change at Temple Meads, spare peak capacity.
- **T4 vs R1 + R2:** coverage (residents served, P3a method) vs journey-time savings
  per pound.
- **Unserved population:** rank the north-east and south-east options by how many of
  the people P3a finds unserved each one reaches.
- **Temple Meads platform occupation** per phase, from Darwin dwell data.

## 6. What the engine needs for these (P3b ops)

- `add_line` on `existing_rail` + `tunnel` segments: R2, R3, tunnel variants.
- `modify_route` with `stopping_pattern`, `extend_to`, `truncate_at`: dropping
  Bedminster, Severn Beach frequencies, Henbury.
- `replace_route` / `reroute`: moving GWR Weston services into the tunnel.
- New on-street routes need `bus_speed_ratio` (trams on street, BRT).
- Capacity check (D6) on every scenario, with infrastructure changes (redoubling,
  loops, new junctions) as data in `scenarios/infrastructure/`.
- Costs (D9) by item: electrification per single-track km, redoubling, passing loops,
  grade-separated junctions, bored tunnel per km, underground stations by type, bay
  platforms, fleet, depot; tram per km by alignment type.
