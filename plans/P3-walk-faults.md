# P3 — walk-network faults: candidate patch list (nothing applied)

Robbie, 2026-10-10 (P3a stop decision 6): investigate, check against current OSM, list
candidates with evidence, apply nothing without approval. Approved fixes go in a versioned
OSM patch file under `data/` (not `src/`), hashed into the network version (Q15).

**How this list was made.** The 20 stop pairs that `lab coverage stops` cut from one cluster on
network v2 (same name within 150 m or any name within 40 m, but more than 4 minutes apart on
foot in R5). For each: the OSM ways within 20 m of either stop in the extract (26 Sep 2026),
and the same box fetched from the live OSM API on 10 Oct 2026. The classification is from
tags only. **No location has been looked at on a map, on imagery or on the ground**, so each
proposal is a candidate to check, not a finding.

**Current OSM.** At 17 of the 20 locations no highway way has changed since the extract; at 3
there are edits from 8–9 Oct, none of them a new link between the two stops. The extract does
not predate a fix at any of them.

**Pattern.** Nine pairs sit either side of a divided road (one-way carriageways or `foot=no`):
the long walk is a real one, to a crossing. Ten sit on an ordinary two-way road where a
footway, cycleway or promenade is mapped as its own way alongside: R5 snaps one stop to that
path and the other to the road or the far path, and with no mapped link across, walks round.
On a two-way road people simply cross, so these look like artefacts of separately mapped
paths, and they affect every origin that reaches such a stop, not only the clustering. One
(Chapel Green Lane) fits neither pattern from its tags and needs looking at.

| Stops | ids | at | apart | R5 walk | Kind | Road | Separate path at the stop | Current OSM | Proposal |
|---|---|---|---|---|---|---|---|---|---|
| Broadoak Road | 0190NSC30295 / 0190NSC30296 | 51.32990, -2.98063 | 37 m | 7 min | single carriageway, path mapped separately | A370 Uphill Road North | way 1223550245 (cycleway), way 1223550245 (cycleway) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Churchland Way | 0190NSA01322 / 0190NSA01323 | 51.35107, -2.90115 | 15 m | 5 min | single carriageway, path mapped separately | Churchland Way; Wolvershill Road | way 1414072464 (cycleway), way 1414072464 (cycleway) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Upper Church Road | 0190NSC30070 / 0190NSC30044 | 51.35214, -2.98636 | 38 m | 10 min | single carriageway, path mapped separately | Knightstone Road; Upper Church Road | way 78020941 (pedestrian), way 78020941 (pedestrian) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Elton Road | 0190NSC30821 / 0190NSC30820 | 51.44006, -2.86246 | 49 m | 5 min | single carriageway, path mapped separately | B3130 Elton Road; The Beach | way 27622842 (footway), way 362238941 (footway) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Chapel Green Lane | 0100BRP92042 / 0100BRP92047 | 51.47241, -2.60963 | 56 m | 10 min | unclear | Canowie Road; Redland Road | — | no highway way changed | Look at the location before proposing anything |
| Docks Way Middle | 5310AWB30451 / 5310AWB30452 | 51.56533, -3.01037 | 35 m | none found | divided road | A48 Docks Way | way 144333613 (footway), way 1221808279 (footway) | no highway way changed | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |
| Spytty Lane | 5310AWB30951 / 5310AWB30952 | 51.57514, -2.96056 | 80 m | 10 min | divided road | A48 Spytty Road; Spytty Lane | way 765456946 (cycleway), way 1470911953 (cycleway) | no highway way changed | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |
| Westfield Drive | 5310AWB30552 / 5310AWB30551 | 51.61159, -3.00471 | 39 m | 5 min | divided road | A4051 Malpas Road | way 874943150 (footway), way 47573053 (footway) | no highway way changed | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |
| Eleanor Hudson House | 5310AWB30647 / 5310AWB30646 | 51.61415, -2.96911 | 32 m | 8 min | single carriageway, path mapped separately | Lodge Road | way 924937823 (footway), way 924937820 (footway) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| St Cadoc`s Hospital | 5310AWB30645 / 5310AWB30644 | 51.61481, -2.96645 | 17 m | 6 min | single carriageway, path mapped separately | Lodge Road | way 924937823 (footway), way 924937824 (footway) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Riverleaze | 0100BRA10696 / 0100BRA10712 | 51.48224, -2.65017 | 60 m | 7 min | divided road | A4 Portway | way 164189606 (cycleway) | no highway way changed | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |
| Lyppincourt Road | 0100053293 / 0100053294 | 51.51321, -2.61977 | 69 m | 5 min | divided road | A4018 Wyck Beck Road; residential | way 366980628 (footway) | no highway way changed | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |
| Brentry Lane | 0100BRP91051 / 0100BRP91052 | 51.50706, -2.61642 | 24 m | 5 min | divided road | A4018 Passage Road | way 338057555 (footway) | no highway way changed | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |
| Brislington House | 0100BRA10888 / 0100BRA10889 | 51.42773, -2.53128 | 111 m | 6 min | single carriageway, path mapped separately | A4 Bath Road | way 829277224 (footway), way 365103094 (footway) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Tudor House | 0170SGB20290 / 0170SGB20291 | 51.42400, -2.48685 | 42 m | 9 min | single carriageway, path mapped separately | A4175 Keynsham Road | way 190281498 (footway), way 190281498 (footway) | 1 way(s) edited since (190281498), none a new link between the stops | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Wayside | 0180BAC30140 / 0180BAC30141 | 51.36280, -2.36984 | 118 m | 5 min | divided road | A367 Wellsway | way 281566707 (cycleway) | 2 way(s) edited since (94830787, 197960553), none a new link between the stops | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |
| Widbrook Farm | 4600WIA11501 / 4600WIA13623 | 51.33246, -2.23528 | 10 m | 7 min | single carriageway, path mapped separately | A363 Widbrook Hill | way 177642682 (cycleway), way 177642682 (cycleway) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Murhill Turn | 4600WIA11389 / 4600WIA11388 | 51.34776, -2.30522 | 11 m | 10 min | single carriageway, path mapped separately | B3108 Winsley Hill; Murhill Lane | way 368439261 (footway), way 368439261 (footway) | no highway way changed | Candidate: a short footway link between the two stop positions (people cross here); or snap both stops to the road centreline |
| Gipsy Patch Lane | 0170SGP90775 / 0170SGP90777 | 51.52169, -2.57074 | 37 m | 5 min | divided road | A38 Gloucester Road North; primary_link | way 148725405 (cycleway), way 1467831126 (footway) | no highway way changed | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |
| The Grove | 0170SGP90771 / 0170SGP90770 | 51.53136, -2.56982 | 77 m | 9 min | divided road | A38 Gloucester Road; primary_link | way 1467831108 (footway), way 1467831107 (footway) | 3 way(s) edited since (4754001, 4755911, 145246809), none a new link between the stops | None: a real barrier. Check only that the nearest mapped crossing is where R5 crosses |

## Known from P2 (PT spot checks), not yet in a patch list

- **Bath Spa station, rear entrance:** services set down behind the station and the model walks
  about 750 m round to the front (9.4 min) where passengers use the rear entrance. Candidate: a
  footway link through the rear entrance. To check: how the entrance is mapped today.
- **Newport bus station (Friars Walk):** a 6-minute, 480 m walk from a stand to a point 147 m
  away. Candidate: links between the stands and the adjoining streets. To check likewise.

## Not faults

- The largest network ÷ straight-line ratios (up to 15) are across Bristol's Floating Harbour,
  at the ferry landings and the stops beside it: 140–230 m of water, 20–28 minutes round.
  Real. Listed in each `lab coverage walk` run (`largest_network_to_straight_line_ratios.csv`).
- 40 OAs are within 400 m of a stop yet a disproportionate walk from it
  (`origins_far_from_their_nearest_stop.csv`); not yet classified.
