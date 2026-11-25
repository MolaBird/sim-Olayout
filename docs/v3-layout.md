# Layout v3: mounted from the hole grid, aligned against checkpoints

`breadboard-optimized-v3.olayout.json` is the two-beam 2923 nm / HeNe layout
rebuilt to be easy to assemble and forgiving to align. It keeps v2's beam
paths: two arms, each with its own expander and steering mirrors, combined on
a knife edge and folded into the objective. It changes how every part is held
and adds checkpoints along the way. It is calibrated for 57 µm between the
two spots.

The parts that stayed put:
- the sample stage and objective (ST1, OBJ2);
- the 2″ pellicles BS4 and BS5, and the right-hand margin, which are reserved
  for the imaging system;
- the collimators, which stay in their column.

The custom parts are described in `custom-parts.md`. For the steps that did
not change (collimation and co-alignment), `alignment.md` and
`arm-coalignment.md` still apply.

The numbers below come from the model. Treat them as targets, not bench
readings.

## 1. Design rules

1. **Mount deterministically.** Every mirror is a KM100CP on a post in a PH2
   screwed into a hole.
   - The KM100CP is post-centred: its reflection point is the hole's centre
     whatever its angle, so a mirror goes back to the same place every time.
   - The BE02Rs stand on machined risers bolted to table holes.
   - The knife edge sits in a machined holder on a post in a hole.
   - The pick-offs M8 and M9 sit on KB1X1s in holes.
   - Only the pellicles BS1/BS2 (KM100BP on KB1X1) and the parts reserved
     above stay on pedestal bases.
2. **Beams need not follow the grid, but every leg has a checkpoint.** A
   checkpoint is an iris (ID25) in a hole on the leg, or a mirror centre. The
   design beam passes each iris within 0.35 mm of its centre, which is below
   what a closed iris resolves.
3. **Parts that are hard to mount deterministically get a checkpoint after
   them.** The pellicles BS1/BS2 are followed by IR1/IR2 and the BE02R's input
   port. The knife edge is followed by the dual-pinhole target.
4. **Use gentle angles and long lever arms.** Angles of incidence are at most
   45° (KE1, a right-angle prism, is 45° by nature). Each arm has
   three steering mirrors 2–5″ apart.

## 2. What changed from v2

| Part | v2 | v3 | Why |
|---|---|---|---|
| KE1 | MRAK25 on a KM100B, on a pedestal at (12.28, 9.00) | MRAK25 in a machined holder (OLY-002), post in hole (13, 10) | Deterministic. Its edge is 4.5 mm ahead of the post, so the beams leave 9.0 mm apart by construction |
| BX7, BX8 | BE02R standing free (BX7 upside down), off the grid | BE02R on identical risers (OLY-001), bolted to holes | Height, position and yaw set by machining. Both are the same way up |
| Arm rows | y = 15.451 and 2.549 | y = 15.500 and 2.500, the hole rows | Irises in holes can check them. M2–M5 fold at exactly 22.5° |
| M26, M29 | FMP1, fixed | KM100CP in holes (11, 13), (11, 8) | Adjustable, with an iris (IR3) after M26 |
| M27, M30 | holes (13, 12), (13, 7) | holes (13, 13), (13, 8) | Room for IR4 and IR5 on the feeds into KE1 |
| M22 | KM100 on a pedestal at (4.13, 9.03) | KM100CP in hole (5, 10) | Deterministic |
| M8, M9 | FMP1 on KB1X1, on pedestals | FMP1 on KB1X1 in holes (8, 3), (8, 16) | Deterministic; the samples leave along x = 7.29 |
| New | | IR1–IR5, BB1–BB2, dual-pinhole target, panel hole at x = 7.29 | Checkpoints; stray light; the pick-off exit |

The calibration solves only the angles. The pair crosses in the objective's
pupil centre, with spots at +28.50 and −28.49 µm (57.0 µm apart). It lands
on KE1 on the post's column, and the two beams sit symmetrically about the
KE1 hole row on the common path, so M22 is hit at its centre.

## 3. Beam path and placement

Top arm (the bottom arm mirrors it about the KE1 row, y = 9.5):

1. **2923:** FC3 → M4 → M3 → BS2, transmitted.
2. **HeNe:** FC4 → M7 → BS2, reflected.
3. **Shared:** IR1 → BX7 (2×) → M31 → M26 → IR3 → M27 → IR4 → KE1's upper
   leg.
4. **Bottom arm:** BS1 → IR2 → BX8 → M28 → M29 → M30 → IR5 → KE1's lower
   leg.
5. **Both beams:**
   - KE1 sends them along the row toward M22, 9.0 mm apart, converging at
     11.4 mrad.
   - They pass the target hole (9, 10) 7.84 mm apart.
   - M22 sends them back under KE1 to M23, which sends them up into OBJ2.
   - The path from KE1 to the pupil is 789 mm.

Hole (c, r) is column c, row r; hole (1, 1) is at (0.5″, 0.5″).

| Label | Part | Hole | Angle | Notes |
|---|---|---|---|---|
| FC3 / FC4 | collimator 2923 / HeNe | (3, 14) / (3, 12) | 0° | unchanged |
| FC5 / FC6 | collimator 2923 / HeNe | (3, 5) / (3, 7) | 0° | unchanged |
| M4 / M3 | KM100CP | (5, 14) / (3, 16) | 157.5° / 337.5° | 2923 input pair, top |
| M5 / M2 | KM100CP | (5, 5) / (3, 3) | 202.5° / 22.5° | 2923 input pair, bottom |
| M7 / M6 | KM100CP | (6, 12) / (6, 7) | 135° / 225° | HeNe folds |
| BS2 / BS1 | BP108 in KM100BP on KB1X1, pedestal | at (5.150, 15.920) / (5.150, 2.080) | 315° / 45° | membrane on the arm row |
| IR1 / IR2 | ID25 iris | (7, 16) / (7, 3) | 0° | arm axis, with the BE02R input port |
| M9 / M8 | FMP1 on KB1X1 | (8, 16) / (8, 3) | 225° | pick-offs, lifted in use |
| BX7 / BX8 | BE02R on riser OLY-001 | bolts (10, 15)+(11, 15) / (10, 2)+(11, 2) | 0° | mirror 1 at (10.0, 15.5) / (10.0, 2.5) |
| M31 / M28 | KM100CP | (13, 16) / (13, 3) | 212.0° / 150.0° | the beam arrives 3–4 mm from the centre: the BE02R's output offset |
| M26 / M29 | KM100CP | (11, 13) / (11, 8) | 27.2° / 325.2° | zigzag |
| IR3 | ID25 iris | (12, 13) | 0° | top zigzag |
| M27 / M30 | KM100CP | (13, 13) / (13, 8) | 224.8° / 135.2° | feeds into KE1 |
| IR4 / IR5 | ID25 iris | (13, 11) / (13, 9) | 270° / 90° | feeds into KE1 |
| KE1 | MRAK25 in holder OLY-002 | (13, 10) | 180° | edge 4.5 mm ahead of the post, pointing −x |
| M22 | KM100CP | (5, 10) | 343.8° | U-turn |
| M23 | KM100CP | (16, 3) | 118.8° | into the objective |
| BB1 / BB2 | beam block OLY-003 | (15, 9) / (13, 6) | 0° / 30° | BS4's and BS5's strays |
| target | dual-pinhole target OLY-004 | (9, 10) | face toward KE1 | alignment only |

Mirror angles are the calibrated values. On the bench, the checkpoints set
them.

## 4. Checkpoints

| Checkpoint | Hole | Checks | Design beam vs. the centre |
|---|---|---|---|
| IR1 / IR2 | (7, 16) / (7, 3) | the arm axis after the pellicle, with the BE02R's input port | on centre |
| BE02R input port | — | the arm axis | on centre |
| M26 / M29 centre | — | M31's / M28's tilt | 0.3 mm |
| IR3 | (12, 13) | M26's tilt, on the way to M27's centre | 0.34 mm low |
| M30 centre | — | M29's tilt | 0.4 mm |
| IR4 / IR5 | (13, 11) / (13, 9) | M27 / M30: the feeds onto KE1 | 0.12 mm toward +x |
| dual-pinhole target | (9, 10) | both beams after KE1 | each 3.92 mm from the centre line, as machined |
| centred targets in OBJ2's thread | — | M22 + M23: the pair on the objective axis | on centre |

Close an iris to 2–3 mm to centre the HeNe on it, then open it to Ø25 mm. The
irises stay in the layout, open.

## 5. Procedure

Do the top arm first and copy it to the bottom arm. Keep every beam level at
the beam height `h`.

1. **Collimators, arms and co-alignment.** Follow `alignment.md` Steps 1–4,
   with two changes:
   - The arm axis is now the hole row y = 15.5 (2.5). Steer the 2923 with
     M4+M3 and the HeNe with M7+BS2 until both pass IR1 (closed) and an SM05
     alignment target in BX7's input port.
   - M9 (M8) now sits in a hole. Seated, it sends the sample down x = 7.29,
     through the hole in the bottom panel, to the alignment board.
2. **Expanders.**
   - Bolt each riser down, set the BE02R on it, and pull its side plate flat
     against the wall with the four 8-32 screws.
   - Nothing on the expander is adjusted. Check that the output leaves the
     79° port cleanly and lands on M31 (M28) 3–4 mm from its centre, as
     designed.
3. **Zigzags.**
   - **Top:** tilt M31 to hit M26's centre. Tilt M26 to pass IR3 and hit
     M27's centre. Tilt M27 to pass IR4 onto KE1's upper leg.
   - **Bottom:** tilt M28 to hit M29's centre, M29 to hit M30's centre, and
     M30 to pass IR5 onto KE1's lower leg.
   - Use each mirror's vertical knob to keep the beam level.
4. **Knife edge and the pair.**
   - Put the dual-pinhole target in hole (9, 10), face toward KE1.
   - Trim M27 (top beam) and M30 (bottom beam) until each HeNe beam passes
     its pinhole: horizontal knobs for x, vertical knobs for height.
   - If both beams miss the same way, turn KE1's post a little. That moves
     both together.
   - Take the target out.
5. **Onto the objective.** Walk M22 and M23 until the pair passes two centred
   targets on the objective's axis, as in `alignment.md` Step 7.
6. **Spots and pupil.** Use the imaging system and the knob table below.
   1. Set the spacing to 57 µm, centred, with M27 (top spot) and M30 (bottom
      spot): 10 µm per mrad.
   2. Check the overlap in the pupil with the defocus test of `alignment.md`
      Step 8.
   3. If one spot walks with defocus, walk that arm's pair: M31 against M27,
      or M28 against M30, in equal and opposite turns. The beam moves in the
      pupil and its spot stays put.
   4. If both spots walk the same way, walk M22 and M23 together.
7. **Fine co-alignment and focus at the sample.** Follow `alignment.md`
   Steps 9–10 and `arm-coalignment.md`.

## 6. Which knob does what

Each turn is 1 mrad of mirror tilt, which deflects the beam by 2 mrad. Spot
movement is at the sample (f = 5.0 mm). Pupil movement is at the objective's
entrance pupil. Slack is how far the knob can turn, each way, before that beam
loses 1% at the objective.

| Knob | Moves | Spot per mrad | Pupil per mrad | Slack |
|---|---|---|---|---|
| M27 / M30 | that arm's spot | 10.0 µm | 1.72 / 1.67 mm | ±0.31 / ±0.32 mrad |
| M31 / M28 | that arm's spot | 10.0 µm | 2.01 / 2.05 mm | ±0.26 mrad |
| M26 / M29 | that arm's spot | 10.0 µm | 1.82 / 1.77 mm | ±0.29 / ±0.30 mrad |
| M31 against M27 (equal, opposite) | the top beam in the pupil | 0 | 0.29 mm | ±1.9 mrad |
| M28 against M30 (equal, opposite) | the bottom beam in the pupil | 0 | 0.38 mm | ±1.4 mrad |
| M22 | both spots | 10.0 µm | 1.17 mm | ±0.45 mrad |
| M23 | both spots | 10.0 µm | 0.51 mm | ±1.0 mrad |
| M22 + M23 (same turn) | both beams in the pupil | 0 | 0.66 mm | ±0.8 mrad |
| KE1 (post rotation) | both spots | 10.0 µm | 1.57 mm | ±0.34 mrad |
| M3 / M4 (M2 / M5) | that arm's 2923 only | 5.1 µm (halved by the expander) | 1.9–2.2 mm | about ±0.25 mrad |
| BX7 / BX8 (riser yaw) | that arm | 2.4 µm | 0.47 mm | ±1.1 mrad |

The objective's pupil sets all the slacks: a beam 0.5 mm off its centre loses
1%. The 2923 beam's 1/e² radius there is about 1.9 mm, in a Ø5.1 mm
pupil. The knife edge costs nothing here: each beam is 4.5 mm (2w)
from the edge, and the edge passes more than 99.99%.

## 7. Notes and limits

- **The model is 2D.** It doesn't cover heights, vertical angles, or a
  tilted knife edge. A rolled edge sends the two beams up and down in
  opposite directions. Each arm's mirrors correct its own beam vertically,
  which is why KE1 needs no tip/tilt platform.
- **Bolt play.** The risers' bolts can leave a few mrad of yaw. M31 (M28)
  absorbs it.
- **Off-centre landings.** The BE02R's output offset puts the beam 3–4 mm
  from M31's (M28's) centre. That is within the Ø1″ mirror's clear aperture.
- **BS1/BS2 strays.** The spare port of each KM100BP pellicle still lands on
  its own mount, as in v2. The model flags it as a warning. It is HeNe light
  and the 8% 2923 reflection, both harmless.
- **The 99.3% at OBJ2** is the pupil clipping the 2923 beam, which fills 76%
  of it. That is inherent.
- **Collimator overlaps.** The overlaps FC3/FC4, FC5/FC6 and FC3/M3 are
  marked as accepted: the knobs that overlap in the drawing sit at different
  heights.
