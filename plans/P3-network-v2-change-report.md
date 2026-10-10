# Network v2 change report (P3b-0)

Network v1 = P2's flat network (`flat-4873adf6d2`). Network v2 = the same OSM and
timetables with terrain (`tobler-99de5fa760`): OS Terrain 50, R5's Tobler slope cost, walk
speed 4.8 × 1.0318 = 4.953 km/h so that the flattest fifth of OAs still average 4.8 km/h.
Car skims are unchanged (they do not use the R5 network). P2's figures stay on record as
v1; `LAB_NETWORK=flat` reproduces them. Sketch-planning model: indicative and comparative.

Runs: skims `20261010T153248-network-change-5335c0`; v2 PT skims `…092151`-started AM and
`…150351` IP chain (`lab skims pt`), walk and cycle `lab skims active`, accessibility
`20261010T153224-access-92a696`, DfT comparison `20261010T153226-compare-dft-fb74f2`, gap map
`20261010T153228-gapmap-a82544`, spot checks `20261010T092151-spotchecks-eval-60d886`,
coverage `20261010T153521-coverage-score-9c6afb` (v1) and `20261010T153652-coverage-score-ff80c1` (v2).

## 0. The move to the new skim layout changed nothing

With v1 skims moved to `skims/B2026/<network_version>/<mode>/<period>.parquet`,
accessibility, the DfT comparison, the spot checks and their scoring reproduce the P2
closing runs exactly; the gap map agrees to 12 significant figures (summation order).

## 1. Terrain (v1 → v2)

| | v1 | v2 |
|---|---|---|
| PT spot checks within tolerance (AM / IP / evening, of 20) | 15 / 18 / 16 | **15 / 18 / 16** — no extra failures, so v2 is adopted |
| PT skim, AM: median of pair medians | 82.8 min | 82.7 min |
| PT skim, AM: change in a pair's median | — | mean −0.09 min; 5th to 95th percentile −1.3 to +1.5; 2.3% of pairs 2 min slower, 1.8% 2 min faster |
| PT skim, AM: reachable pairs | 2,572,064 | 2,579,280 (7,378 lost, 14,594 gained) |
| PT skim, AM: mean walking within a journey | 28.7 min | 28.4 min |
| PT skim, inter-peak: median; mean change | 82.1 min | 81.9 min; −0.14 min |
| Median PT generalised cost, AM / inter-peak | 128.8 / 126.9 | 128.4 / 126.4 |
| Walk skim: mean time over pairs reachable in both | 69.6 min | +2.4 min (+3.5%) |
| Cycle skim: mean time | 57.4 min | +4.1 min (+7.2%) |
| Jobs within 30 / 45 min by PT, median core OA | 17,288 / 78,630 | 16,675 / 79,055 |
| Jobs within 30 / 45 min by PT, median edge OA | 10,715 / 29,890 | 10,700 / 30,195 |
| DfT Connectivity ρ (OA / excluding edge / LSOA) | 0.947 / 0.963 / 0.952 | 0.947 / 0.963 / 0.952 |
| Gap map: PT − car door-to-door time, mean (high / low car terminal times) | 29.4 / 36.6 min | 29.3 / 36.5 min |
| Gap map: generalised-cost ratio of sums (high / low) | 3.05 / 3.98 | 3.05 / 3.98 |

PT journeys barely move: the rescaled walk speed gives back on flat ground about what the
slope cost takes on hills, and walking is a third of a PT journey. Pure walking is 3.5%
slower and cycling 7.2% slower (cycle speed was not rescaled; one slope function serves
both modes, a recorded limitation). Every accessibility figure above is at 4.8 km/h
effective on flat ground, with gradient.

## 2. Truncation correction on the walk and cycle skims (its own step, on v2)

r5py truncates to the whole minute; `travel_time_corrected` adds 0.5 min, the raw value
stays in `travel_time`. PT skims are not affected (r5r reports tenths).

| pairs within | walk: raw → corrected | cycle: raw → corrected |
|---|---|---|
| 15 min | 22,830 → 20,188 (−11.6%) | 104,197 → 92,648 (−11.1%) |
| 30 min | 78,859 → 74,320 (−5.8%) | 327,116 → 310,350 (−5.1%) |
| 45 min | 157,398 → 151,648 (−3.7%) | 595,922 → 577,520 (−3.1%) |

Jobs within 30 / 45 min **on foot**, median core OA: 14,590 / 35,328 on v1 (raw) →
12,625 / 31,203 on v2 with the correction (terrain and correction together; the walk
figure is context only in the accessibility output).

## 3. Frequent-service coverage: three served cut-offs, both networks

Residents (of 1,219,504) with a service at that frequency, in the worse of AM and
inter-peak, within the cut-off. Walking times carry the half-minute correction.

| served cut-off | frequent (every 15 min) v1 | v2 | high frequency (every 10 min) v1 | v2 |
|---|---|---|---|---|
| **Headline: TfL PTAL, 8 min bus / 12 min rail** | 654,684 (53.7%) | **644,764 (52.9%)** | 435,079 (35.7%) | **426,199 (34.9%)** |
| Strict: El-Geneidy 85th percentiles, 524 m / 1,259 m | 610,903 | 598,321 (49.1%) | 392,592 | 381,452 (31.3%) |
| Loose: unverified WYG figures, 800 m / 1,610 m | 762,407 | 748,477 (61.4%) | 535,401 | 522,079 (42.8%) |
| Headline cut-off on R5's plain truncated minutes | 709,241 | 702,698 | 485,582 | 478,388 |

On v2 at the headline: jobs served 470,463 (66.3%) frequent, 354,209 (49.9%) high
frequency; evening (19–22) 488,924 residents frequent; decay-weighted residents 275,196;
reduced-mobility variant (distances × 0.7) 501,890, and 322,559 / 581,660 at × 0.5 / × 0.85.
Service-quality classes: A 73,249, B 148,073, C 281,394, D 415,825, none 300,963. Share of
residents with a frequent service, England, most to least deprived tenth: 82% … 38%; Wales
(separate index, never pooled): 41% … 38%.

**Where the truncation correction is exact.** R5 floors walking times to the minute, so a
stored value k means a true time in [k, k + 1). Adding half a minute and testing against a
whole-minute cut-off (the headline's 8 and 12 minutes) keeps exactly the pairs with k ≤ 7
and k ≤ 11, that is every pair truly under the cut-off and none over it: for the
**headline variant the corrected count is exact**. At the non-integer cut-offs — strict
(6.55 and 15.74 min) and loose (10.0 for bus, which is integer and exact, and 20.12 for
rail) — a pair stored in the minute that contains the cut-off may fall either side, and
the half minute only places it at the middle of its minute: for the **strict variant and
the rail part of the loose variant the corrected count is an approximation**. The
decay-weighted figures are approximate under every variant.

The cut-off matters far more than terrain: strict to loose is 150,000 residents; terrain is
10,000. The truncation correction is worth 58,000 at the headline cut-off, because 8
minutes falls exactly on a minute boundary.
