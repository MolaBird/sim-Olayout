# Aligning the two-beam 2923 nm / HeNe layout

This is the alignment procedure for `breadboard-optimized.olayout.json`, from the
fiber collimators to the two spots on the sample. It also lists the parts that
differ from the earlier draft, `breadboard-test.olayout.json`.

Open the layout in olayout to follow along. Click a component, and the Inspector
shows its position, angle, the hole its clamping fork uses, and where each beam
hits it. Numbers quoted here come from the model: thin collimator lenses, the
BE02R's measured mirrors, and the LMM40X as a 5.0 mm thin lens with a Ø5.1 mm
pupil. Treat them as targets and starting points, not as bench readings.

## 1. What the layout does

Two 2923 nm beams each get a co-aligned HeNe tracer and are expanded 2×. They
are then combined side by side on a knife-edge prism mirror and focused by the
LMM40X into two spots 57 µm apart. For 57 µm at f = 5.0 mm, the beams must
cross in the objective's pupil 11.4 mrad apart, ±5.7 mrad about its axis.

Beam path, top arm (the bottom arm is its mirror image about y = 9"):

1. **2923:** FC3 → M4 → M3 → BS2, transmitted (92%).
2. **HeNe:** FC4 → M7 → BS2, reflected (8%). From here on the two colours
   share one path.
3. **Arm:** BS2 → [M9, magnetic pick-off, lifted in use] → BX6 (2× expander)
   → M20 → straight down onto KE1's upper leg.
4. **Bottom arm:** FC5/FC6 → M5, M2 / M6 → BS1 → [M8] → BX5 → M21 → straight
   up onto KE1's lower leg.
5. **Both beams:**
   - KE1 sends them left, side by side, 8.1 mm apart.
   - M22 (far left) sends them back right, 6.9° downward.
   - M23 (under the objective) sends them up into OBJ2 → sample.

The pair needs 28" of path between the knife edge and the pupil to close its
8.1 mm separation at 11.4 mrad. That path is why the common route goes out to
M22 and back. With 8.1 mm at the edge, each beam's centre sits 4.1 mm
(1.8 w for the 2923) from the edge, and the edge passes 99.98%.

## 2. Parts: what changes from the draft

Between the pellicles and the objective, the draft used nine mirrors: M12, M13,
M17, M18 in KM100s and M1, M10, M11, M14, M15 in KMSR square-mirror mounts. The
new layout uses five optics: M20, M21, M22, M23 and the knife edge KE1.

**Additional parts needed:**

| Qty | Part | Used for |
|---|---|---|
| 1 | MRAK25-P01 knife-edge right-angle prism mirror, protected silver legs, 25 mm | KE1 |
| 1 | KM100B kinematic platform mount (1.92″ platform, ±4° tip/tilt) | KE1 |
| 1 | PH2 + BE1 + CF125C + TR post | KE1's pedestal base |
| 1 | A way to hold the prism on the platform: a clamping arm, or removable adhesive | KE1 |
| 1 | KM100CP + PH2 + TR post | M23, screwed into hole (16, 8) |
| 1 | Ø1″ protected-silver flat mirror (PF10-03-P01 or equal) | M23 |

**Optional:**
- **Linear stages for fine slides:** a one-axis stage with a micrometer
  (e.g. Thorlabs PT1) under KE1 and/or under M20 and M21, oriented along the
  beam. Without one, these slides are done by loosening the fork and pushing
  the base, which is coarse but adequate.
- **Co-alignment tools:** see `coalignment-precision.md`. They all go on the
  alignment board, not on this one.

**No longer needed:**
- 5 × KMSR + PFSQ10-03-P01 square mirrors (draft M1, M10, M11, M14, M15), with
  their 5 pedestal bases.
- 1 × KM100 with its pedestal base. The draft had four (M12, M13, M17, M18);
  the new layout has three (M20, M21, M22).

If you already own the KMSR mirrors, any of them can serve as M20–M22. The
layout is drawn with KM100s, whose mirror surface sits 0.57″ in front of the
post; in the KMSR it is 0.66″. If you swap, change the unit in olayout and move
it so the mirror surface returns to the same point.

**Unchanged:**
- the four collimators;
- M2–M7 (KM100CP, hole-mounted);
- BS1 and BS2 (BP108 pellicles in KM100BPs on KB1X1s);
- the pick-offs M8 and M9 (FMP1 on KB1X1);
- BX5 and BX6 (BE02R);
- OBJ2 and ST1.

**Mirror coatings:** every flat mirror after the pellicles should be protected
silver (P01) or another coating specified at both 633 nm and 2.9 µm. The
BE02R and LMM40X-P01 are protected silver.

## 3. Before you start

**Beam height**
- Pick one beam height `h` for the whole board and set every post for it.
- The BE02R's axis is 25.0 mm above its flange, so raise the expander on a
  spacer or plate to put its axis at `h`.
- The MRAK25's mid-height (12.5 mm above the KM100B platform) goes at `h`.

**Coordinates**
- x is measured from the board's left edge and y from its bottom edge, in
  inches.
- Hole (c, r) is column c, row r. Hole (1, 1) is at (0.5″, 0.5″).
- An angle is the direction the optic faces, counterclockwise from +x.

**Placement table**

For pedestal units, the fork screw goes into the listed hole; olayout picks a
reachable hole automatically. Position values are post axes.

| Label | Unit | x, y (in) | Angle | Mounting |
|---|---|---|---|---|
| FC3 / FC4 | collimator, 2923 / HeNe | 2.500, 13.500 / 2.500, 11.500 | 0° | holes (3, 14) / (3, 12) |
| FC5 / FC6 | collimator, 2923 / HeNe | 2.500, 4.500 / 2.500, 6.500 | 0° | holes (3, 5) / (3, 7) |
| M4 / M3 | KM100CP, 2923 input pair, top | 4.500, 13.500 / 2.500, 15.500 | 158° / 338° | holes (5, 14) / (3, 16) |
| M5 / M2 | KM100CP, 2923 input pair, bottom | 4.500, 4.500 / 2.500, 2.500 | 202° / 22° | holes (5, 5) / (3, 3), side tap |
| M7 / M6 | KM100CP, HeNe fold | 5.500, 11.500 / 5.500, 6.500 | 135° / 225° | holes (6, 12) / (6, 7) |
| BS2 / BS1 | BP108 pellicle on KB1X1 | 4.991, 16.030 / 4.991, 1.970 | 315° / 45° | pedestal, fork (4, 17) / (4, 4) |
| M9 / M8 | FMP1 on KB1X1, pick-off | 7.607, 15.558 / 7.607, 2.656 | 225° / 225° | pedestal, fork (9, 17) / (9, 4) |
| BX6 / BX5 | BE02R / BE02R upside down | 10.000, 15.451 / 10.000, 2.549 | 0° | free-standing; the position is mirror 1 |
| M20 / M21 | KM100, arm steering | 12.870, 15.761 / 12.870, 2.239 | 229.6° / 130.4° | pedestal, fork (15, 15) / (15, 2) |
| KE1 | MRAK25-P01 on KM100B | 12.305, 9.000 | 180° | pedestal, edge over the post, fork (15, 9) |
| M22 | KM100, U-turn | 1.930, 9.033 | 356.7° | pedestal, fork (2, 11) |
| M23 | KM100CP, final fold | 15.500, 7.500 | 131.7° | hole (16, 8), mirror over the post |
| OBJ2 | LMM40X in M1RMSS in CP35 | 15.500, 11.375 | 90° | pedestal, fork (16, 10) |

**Reference points**

| Point | x, y (in) | Detail |
|---|---|---|
| Top arm line (after BS2) | y = 15.451 | |
| Bottom arm line (after BS1) | y = 2.549 | |
| Vertical feeds into KE1 | x = 12.466 | 0.161″ = 4.1 mm right of the edge at x = 12.305 |
| M22's surface | 2.50, 9.00 | |
| M23's surface | 15.50, 7.50 | |
| Objective pupil (model) | 15.500, 12.534 | |
| Pick-off samples | x = 7.500, bottom edge | the line along which both leave the board |

**Tools**
- The HeNe tracers. They make every path after the pellicles visible.
- A thermal power sensor or another 2–5 µm detector for the 2923.
- An IR viewing card for 2–5 µm, if you have one.
- A portable iris on a post at height `h`, plus cards and a ruler.
- Your alignment board, below this one.

## 4. Procedure

Do the top arm first and copy it to the bottom arm; the two are mirror images.
Keep every beam level at `h`. Most problems with this layout show up first as a
beam that has drifted in height.

### Step 1: collimators (FC3–FC6)

1. **Starting distances.** Set each fiber-to-lens distance with the SM1ZB's z
   knob, using the layout values as a start: 5.94 mm for the 2923 (f = 5.95 mm)
   and 11.0 mm for the HeNe (f = 11 mm).
2. **Collimate the HeNe.** Compare its beam size near the collimator and as
   far away as you can, for example across the room. Adjust until the two
   sizes match.
3. **Collimate the 2923.** Its Rayleigh range is only about 1.2 m at
   w = 1.05 mm, so a size comparison over a few metres works. Use your detector
   with the far-field knife edge described in `coalignment-precision.md`.
4. **Match the two colours.** The objective is reflective and achromatic, but
   each colour focuses where its own collimation puts it. As drawn, the model
   puts the HeNe focus 5.4 µm closer to the objective than the 2923's. That is
   about the 2923 focus's Rayleigh range (6.2 µm). The final match is Step 9.
5. **Height and level.** Set each collimator's beam at `h` and level. Check it
   with the portable iris at two distances along the beam's first leg.

### Step 2: 2923 into the arm (M4, M3; bottom: M5, M2)

The 2923 runs FC3 → M4 → back up-left to M3 → along y = 15.451 through BS2.

1. **Rough alignment.** You need to see the invisible beam, using any of:
   - an IR card;
   - the power sensor behind the portable iris;
   - visible light launched into the 2923 fiber, if the fiber carries it.
2. **Target.** Steer with M4 and M3 so the beam crosses BS2's membrane centre
   and continues along y = 15.451 at height `h`, level.
3. **Fine alignment** comes in Step 4.

### Step 3: HeNe into the arm (M7, BS2; bottom: M6, BS1)

1. **The path.** M7 sends the HeNe straight up into BS2, and the pellicle
   reflects 8% of it along the arm.
2. **Rough alignment.** Steer with M7 and BS2's kinematic adjusters until the
   HeNe follows the 2923's line.
3. **Handling the pellicle.** Never touch the membrane. Adjust BS2 only by its
   knobs.
4. **The unused output.** BS2's other output (92% of the HeNe and 8% of the
   2923) runs into its own KM100BP. That is harmless, but block it if stray
   HeNe light bothers your imaging.

### Step 4: co-align the HeNe and the 2923 on the alignment board

1. **Seat one pick-off.** Seat M9 (press K in olayout; on the table, put the
   FMP1 back on its KB1X1). M9 sends the arm's beam straight down along
   x = 7.5″, across the board and off its bottom edge to the alignment board.
   - For the bottom arm, lift M9 and seat M8, which sends its beam down the
     same line.
   - Use one pick-off at a time. With both seated, the top arm's sample stops
     on M8's back.
2. **Set the pick-offs once.** Set M9 and M8 so the sampled beam leaves
   square to the bottom edge, at height `h` and level. The KB1X1s put them
   back at the same angle every time.
   - Their exact angle doesn't affect co-alignment: both colours reflect off
     the same mirror.
   - It only sets the reference axis you check against.
3. **Co-align.** Let the HeNe define the axis on the alignment board and
   bring the 2923 onto it.
   - Use M3 and M4 for the 2923 (M2 and M5 for the bottom arm).
   - Touch M7 and BS2 only if you need to move the reference itself.
   - Targets, given that the expander halves angles and doubles offsets:

     | | At the pick-off | Equivalent downstream |
     |---|---|---|
     | Angle | ≤ 0.2 mrad | 0.5 µm between the HeNe and 2923 spots on the sample |
     | Position | ≤ 0.1 mm | 0.2 mm in the objective's pupil |

   - Maximising 2923 power through pinholes struggles to reach that.
     `coalignment-precision.md` describes methods that do, without a mid-IR
     camera.
4. **Lift the pick-off** when done.

### Step 5: beam expanders (BX6, BX5)

1. **Centre the input.** BX6's mirror 1 sits at (10.000, 15.451) and its input
   port faces −x along the arm. Shift and rotate the housing until the beam
   enters on the input port's axis. A centred target or SM05 iris in the port
   helps.
2. **Check the output.**
   - It leaves through the 79° face, turned 9.6° from the input: BX6 turns it
     up (toward the top edge), and the upside-down BX5 turns it down.
   - It is about 2× larger. The model gives 2.03× in the fold plane and 1.89×
     across it.
   - It should be collimated.
3. **What moves the output.** The expander's mirrors are fixed, so the housing
   is the only adjustment:
   - turning the housing turns the output by the same angle;
   - shifting it shifts the output.
4. **Input size limit.** Beams larger than about Ø3.6 mm start to clip on
   mirror 1. Yours is Ø2.2 mm.

### Step 6: steering mirrors and the knife edge (M20, M21, KE1)

1. **Place KE1.**
   - Its edge goes at (12.305, 9.000), pointing −x toward M22.
   - The prism's mid-height goes at `h`.
   - Level the KM100B platform so the edge is vertical. A tilted edge sends
     the two outputs up and down in opposite directions.
2. **Set M20.** M20 takes BX6's output and sends it straight down onto KE1's
   upper leg. M21 sends the bottom arm straight up onto the lower leg. For each
   mirror:
   - **Tilt** it until the beam runs exactly vertical. Check with the portable
     iris at two heights along the 6.2″ feed.
   - **Slide** its pedestal along the beam arriving from the expander to set
     the feed's x position.
     - It moves 1:1, and the tilt is unaffected.
     - Aim for x = 12.466″: the HeNe spot on the leg sits 4.1 mm from the edge,
       measured across the beam.
     - There is about 1 mm of room toward the edge before the 2923 loses 0.5%
       there.
3. **Check the output.**
   - Both outputs leave KE1 toward M22 at height `h`, side by side, about
     8 mm apart.
   - Nothing should spill past the edge: look at the far side of the prism
     with a card.

### Step 7: common path onto the objective (M22, M23)

1. **M22 (U-turn).** Its surface is at (2.50, 9.00). It returns the pair
   toward M23 at 6.9° below horizontal.
   - The return path passes about 1.2″ under KE1.
   - It crosses M21's upward feed; crossing beams don't interact.
2. **M23 (final fold).** It sits in hole (16, 8) and sends the pair straight
   up the objective's axis, x = 15.5″.
3. **Centre the pair on the objective's axis.**
   - Unscrew the LMM40X and put a centred target in its place.
   - Put a second target 2–3″ further up the axis.
   - Walk M22 (far) and M23 (near) until both beams pass both targets.
   - The pair is nearly overlapped there (0.6 mm apart 2″ below the pupil),
     so this centres the pair as a whole.
4. **Refit the objective.**

### Step 8: spots, spacing and pupil overlap

Watch the two HeNe spots on the sample with your imaging system.

1. **Spot positions.**
   - Tilt M20 to place the top arm's spot and M21 for the bottom arm's: 10 µm
     per mrad of horizontal tilt. Neither touches the other spot.
   - Set the spacing to 57 µm, centred on the objective's axis.
   - Use the vertical adjusters to put both spots at the same height. M22 and
     M23 move both spots together.
2. **Overlap in the pupil.** The beams should cross in the objective's
   entrance pupil. To check, step the stage's focus by ±10–20 µm and watch
   each spot.
   - **The test.** A beam entering the pupil off-centre by d focuses as a
     tilted cone, so its spot walks sideways by d/f per unit of defocus. For
     d = 0.5 mm that is 1 µm per 10 µm of defocus.
   - **Both spots walk the same way:** the pair is off-centre. Walk M22 and
     M23.
   - **They walk in opposite directions:** the beams cross above or below the
     pupil. Slide KE1 along the beams until neither spot walks.
     - This moves the two beams in the pupil in opposite directions, 1 mm per
       mm of slide.
     - The spots don't move.
   - **One spot walks, the other doesn't:** slide that arm's M20 or M21. Its
     beam moves 1:1 in the pupil and its spot stays put.
3. **After a large spot move.** Tilting M20 or M21 also moves that beam in
   the pupil, by 1.7 mm per mrad. After a correction of more than about
   0.2 mrad (2 µm of spot), repeat the defocus check.

### Step 9: focus depth

1. **Measure.** Compare the HeNe and 2923 focal planes. Use the in-situ knife
   edge or the photothermal check in `coalignment-precision.md`.
2. **Adjust the HeNe, not the 2923.** Bring each HeNe focus onto its 2923
   focus with the HeNe collimator's SM1ZB z knob (FC4, FC6), so the 2923 keeps
   its best collimation. In the model, moving the HeNe fiber about 0.08 mm
   toward its lens closes the 5.4 µm gap; the bench value will differ.
3. **Repeat Step 4**, because refocusing a collimator can shift its beam
   slightly.

### Step 10: final checks

1. **Power.** Measure the 2923 just before the objective.
   - Expected: about 2.76 mW per arm. Only the pellicle's 8% is lost before
     the objective, and the model's pupil clips another 0.9%.
   - Anything lower is clipping. Look at KE1's edge, the BE02R input port and
     the pupil, in that order.
2. **Lock everything.** Tighten all forks and post-holder thumbscrews, and
   note the adjuster positions.
3. **Re-check.** Seat each pick-off in turn and confirm the co-alignment on
   the alignment board.

## 5. Which knob does what

| Knob | Moves | Leaves alone | Sensitivity | Slack before 1% loss at the objective |
|---|---|---|---|---|
| M20 / M21 tilt | its arm's spot | the other arm | 10 µm per mrad (pupil: 1.7 mm per mrad) | ±0.25 mrad |
| M20 / M21 slide | its arm, on KE1 and in the pupil, 1:1 | its spot | — | ±0.5 mm |
| M22 tilt | both spots | the spacing | 10 µm per mrad (pupil: 0.9 mm per mrad) | ±0.5 mrad |
| M23 tilt | both spots | the spacing | 10 µm per mrad (pupil: 0.26 mm per mrad) | ±2 mrad |
| KE1 slide | the two beams in the pupil, in opposite directions, 1:1 | both spots | — | ±0.45 mm |
| KE1 platform | both beams vertically: pitch together, roll in opposite directions | horizontal | — | — |
| M3 / M4 (M2 / M5) | that arm's 2923 only | its HeNe | 5 µm per mrad (halved by the expander) | about ±0.25 mrad |
| M7 / BS2 (M6 / BS1) | that arm's HeNe only | its 2923 | 5 µm per mrad | — |

The objective's pupil sets the 1% limits, not the knife edge: the 2923 beam
fills 76% of the Ø5.1 mm pupil.

## 6. Day-to-day checks

- **Co-alignment:** seat M9, check on the alignment board, lift it; then the
  same with M8.
- **Spots:** the separation (57 µm) and their positions, on the imaging
  system.
- **If one spot moved:** suspect that arm, meaning its upstream mirrors or
  M20/M21. Check its co-alignment first.
- **If both spots moved together:** suspect M22, M23 or the objective.
- **If the spacing changed:** suspect M20 or M21.
