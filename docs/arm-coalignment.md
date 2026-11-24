# Co-aligning one arm: pinholes for position, a razor blade at the sample for angle

This is how to put an arm's 2923 nm beam on top of its HeNe tracer, written for
the **top arm**: FC3 (2923), FC4 (HeNe), pick-off M9. The bottom arm is the same
with the labels swapped (section 6).

It runs in two stages:

1. **On the alignment board, with your pinholes.** This sets the beams' relative
   position and gets the angle roughly right.
2. **At the sample, with a razor blade on the MAX311D.** This sets the angle
   finely, matches the two colours' focus depths, and checks that they overlap
   in the objective's pupil.

The HeNe is the reference throughout. Only the 2923's mirrors (M3, and M4 if
needed) are adjusted. M7 and BS2 set the HeNe, and the downstream alignment
(`alignment.md`, steps 5–8) is built on it.

## 1. Why the split works

**The sample plane is the far field.** It is the objective's back focal plane,
so a beam arriving at angle θ focuses at f·θ (f = 5 mm), wherever it enters the
pupil. A blade scanned across the spots therefore measures the angle between
the two colours. It does so after every optic, at the plane that matters, with
no extra mirror.

```text
      2923 ═══╗                     sample plane = back focal plane
      HeNe ───╫──►  objective  ──►   ●◆   ← spot centres differ by f·Δθ only
              ║     (f = 5 mm)       │
     offset in the pupil does        │ blade scanned across, detector behind
     not move the spot at focus      ▼
```

**Position only needs to be coarse.** An offset between the colours doesn't
move the spot. It only moves the beam in the pupil, where 0.1–0.3 mm costs at
most about 1% of the 2923 power. Pinholes on the near-field beam deliver that.

| Quantity | Target | Set by |
|---|---|---|
| Relative position at the pick-off | ≤ 0.1–0.3 mm (≤ 0.2–0.6 mm in the pupil) | pinholes, stage 1 |
| Relative angle, coarse | ≤ 0.3 mrad at the pick-off (spots ≤ 0.8 µm apart) | pinholes, stage 1 |
| Relative angle, fine | spots ≤ 0.2–0.5 µm apart | razor blade, stage 2 |
| Focus depth | the two foci within about 2 µm | razor blade z-scan + HeNe collimator, stage 2 |

## 2. Equipment

| Item | What to use | Notes |
|---|---|---|
| Razor blade, ×2 | Uncoated single-edge carbon-steel blades | One mounted with its edge vertical (x scans), one horizontal (y scans), side by side on a holder that takes the sample's place. Avoid PTFE-coated shaving blades. |
| Detector (with your chopper and lock-in) | **Gentec QS9-IL**: pyroelectric, Ø9 mm, 0.1–1000 µm, voltage output, ±5 V supply | Bare detector made for an external chopper and lock-in. Chop slowly, a few Hz to about 20 Hz. |
| Detector, alternative | **Ophir RM9**: pyroelectric, Ø8 mm, 0.15–12 µm, < 100 nW to 100 mW, about 30 nW noise | Responds only to light chopped at 18 Hz, so set your chopper to 18 Hz. It does its own synchronous detection; read it on an Ophir meter. |
| Detector, no chopper | **Thorlabs S401C** thermal sensor: 0.19–20 µm, 10 µW to 1 W | Works, but slowly, and the HeNe (about 80 µW) is near the bottom of its range. |
| Chopper | Yours | Place it in the common path between M22 and M23, where both arms and both colours pass. Mount it on its own post, off the stage. |
| Stage | The MAX311D, with its piezo controller | The HeNe's knife-edge curve is only 0.9 µm wide; it needs piezo steps. The 2923 can be scanned with the differential drives if necessary. |
| Beam blocks | One per colour, at the collimators | Before the pellicle, so blocking doesn't disturb the other colour. |

Don't use Gentec's THz heads: they are specified for 10–3000 µm.

### Why these choices

**The blade.**
- A good razor's apex is under 0.1 µm wide. That is far below the spot radii
  (0.7 µm HeNe, 2.4 µm 2923), so sharpness is not the issue.
- What matters is a clean, straight section of edge a few µm long. The blade
  sits in the sample plane, so your imaging system shows it at full
  magnification, with the HeNe spot on it: pick a clean section by eye.
- The measurement is differential, both colours on the same section, so the
  edge's smooth shape largely cancels.

**The detector.**
- It must respond to both wavelengths and be linear. It does **not** need
  equal sensitivity to the two: each scan is normalised to that colour's own
  full power, and the 50% point doesn't depend on responsivity.
- A large-area pyroelectric detector is the natural fit for a chopper and
  lock-in. It also rejects room light, which isn't chopped.
- A cheaper mix also works: any large-area silicon photodiode for the HeNe and
  a pyroelectric for the 2923, swapped between colours. The detector's position
  doesn't matter, as long as it catches everything.

**Collection.**
- Light leaves the focus at up to 30° (NA 0.5). With an 8–9 mm aperture the
  sensitive element must be within about 7 mm of the focus.
- Otherwise put a collection lens (CaF₂) or a concave mirror between the blade
  and the detector. Chromatic aberration doesn't matter after the edge: you
  only need all the light on the detector.

## 3. Numbers at the sample (from the model)

| | 2923 | HeNe |
|---|---|---|
| Power at the sample, per arm | about 2.7 mW | about 80 µW |
| Spot radius w₀ (1/e²) | 2.4 µm | 0.7 µm |
| Knife-edge 10–90% width (1.28 w₀) | 3.1 µm | 0.9 µm |
| Depth of focus (Rayleigh range) | 6.2 µm | 2.5 µm |
| Scan range (±2 w₀), at focus | ±5 µm | ±1.5 µm |
| Scan step (about w₀/5) | 0.5 µm | 0.15 µm |
| Spot radius 10 µm out of focus | 4.6 µm | 2.9 µm |

Spot centres come out to tens of nanometres. The real limits are drift,
vibration and the edge, not the fit.

## 4. Stage 1: position and coarse angle with the pinholes

Do this on the alignment board, as you do now:

1. Seat M9 and lift M8. The top arm's sample goes down the line x = 7.5″ to
   the alignment board.
2. Let the HeNe define the pinholes.
3. Bring the 2923 through them with M4 and M3, for maximum power behind the
   pinholes.
4. Aim for:
   - position within 0.1–0.3 mm of the HeNe;
   - angle as good as the pinholes allow, ideally ≤ 0.3 mrad.

   The angle target is what lets stage 2 finish with M3 alone (see stage 2,
   step 3).
5. Lift M9.

Before stage 2, the downstream path must already be aligned on the HeNe
(`alignment.md`, steps 5–8), so both arms' spots are on the sample.

## 5. Stage 2: fine angle, focus and pupil overlap at the sample

### Set-up

1. **Mount the blades.** Replace the sample with the blade holder. Put the
   edges in the sample plane and the vertical edge near the top arm's spot.
2. **Tilt each blade about its own edge by about 40°,** with the blade body
   leaning away from the objective. This keeps the edge in focus and sends the
   blade's reflection outside the objective's ±30° cone.
   - Facing straight back, the polished face would return a good part of the
     2.7 mW of 2923 toward the fiber and the laser.
   - Leaning away, the blade cannot clip the light passing beside the edge.
3. **Set up the detection.** Put the detector behind the edge, start the
   chopper and lock the lock-in to it.
4. **Check the full signal**, blade moved clear, one colour at a time (block
   the other at its collimator):
   - **No saturation.** If the 2923 saturates the detector, attenuate between
     the blade and the detector, or turn the laser down. Never put an
     attenuator in the beam before the objective: anything there can steer the
     2923.
   - **Full collection.** Moving the detector a millimetre should not change
     the reading.

### Step 1: focus each colour (z-scan)

1. **Find each colour's focal plane.** At five to seven focus positions over
   ±10 µm, scan the vertical edge across the spot and fit its width. The width
   is smallest at that colour's focal plane.
2. **Compare the two foci.** The model puts the HeNe focus 5.4 µm closer to the
   objective than the 2923's: almost one 2923 depth of focus.
3. **Match them** with the HeNe collimator's z knob (FC4), so the 2923 keeps
   its best collimation. Aim for within about 2 µm.
4. **Re-check stage 1.** Refocusing a collimator can steer its beam slightly.
5. **Measure at the 2923's focal plane** from here on. That is where the
   sample will sit.

### Step 2: measure the spot centres

At the 2923 focal plane:
1. **HeNe, x.** Block FC3. Scan the vertical edge across the HeNe spot,
   always approaching from the same side. Fit
   P(u) = (P₀/2)·[1 + erf(√2 (u − u₀)/w)]. The centre is u₀.
2. **2923, x.** Unblock FC3, block FC4. Measure the 2923 the same way.
3. **Repeat the HeNe.** If it moved, the stage drifted: average the two HeNe
   results, or wait and redo.
4. **y.** Do the same with the horizontal edge.

The result is Δx and Δy, the 2923 centre minus the HeNe centre.

### Step 3: correct the angle with M3

1. **Know the sensitivity.** Tilting M3 moves the 2923 spot about 5.15 µm per
   mrad. Calibrate the knob directly: turn it a known amount, re-scan the 2923,
   and note µm per turn and the direction.
2. **Null it live.**
   - Park the edge at the HeNe's centre u₀.
   - Measure the 2923's full power P₀ (blade clear), then bring the blade back.
   - Turn M3 until the 2923 reads exactly P₀/2.
   - The reading has a sign: above half means the spot is on the open side.
   - Do x with the horizontal knob and the vertical edge, y with the vertical
     knob and the horizontal edge.
3. **Know the cost.** M3 also moves the 2923 in the objective's pupil: about
   **0.34 mm per µm of spot correction**.
   - **Correction up to about 1 µm:** M3 alone is fine. It costs at most
     0.35 mm of pupil offset.
   - **More than about 1 µm:** stage 1 left too much. Either improve the
     pinhole alignment (a longer baseline), or use M3 and M4 together in the
     combination that doesn't move the pupil.
   - **That combination is expensive.** In the model each µm of spot then
     costs about 1.4 mrad on M3 and 1.2 mrad on M4.
   - **Calibrate it before relying on it.** Measure each mirror's effect on the
     spot (this step) and on the pupil (step 4), then solve for the two moves.

### Step 4: check the overlap in the pupil, without going back to the alignment board

**The principle.** A beam entering the pupil off-centre by p focuses as a
tilted cone, so its spot walks sideways by p/f per unit of defocus. Measure
both colours' centres at z = −10 µm and z = +10 µm, from the 2923 focus. Their
difference in walk gives their relative pupil offset:

```text
   x                          slope of a colour's centre vs z  =  −p / f
   │      2923 ◆                         ◆
   │                 ◆ ●         ●
   │   ●  HeNe                        ← equal slopes = same place in the pupil
   └──────┼──────────┼──────────┼────── z
        −10 µm      focus      +10 µm

   Δp = f × (difference of the two slopes)
   over a 20 µm span:  0.1 mm of Δp  ↔  0.4 µm difference in walk
```

**What to do with the result.**
- **Target:** Δp ≤ 0.3 mm, i.e. a walk difference ≤ 1.2 µm over the 20 µm.
- **If it is larger,** step 3 used too much M3. Go back to stage 1, or use the
  M3 + M4 combination.
- **Why it also matters for the spots.** With a pupil offset, the two spots
  coincide in only one plane, the one you nulled in step 3. Keep the pupil
  offset small and the foci matched (step 1), so the HeNe also marks the 2923
  slightly out of focus. An offset of 0.3 mm with a 2 µm focus mismatch shifts
  the spots by about 0.1 µm.

### Step 5: verify and record

1. **Repeat the at-focus scans.** Run HeNe, 2923, HeNe again, for x and y.
2. **Accept when:**
   - |Δx| and |Δy| ≤ 0.2–0.5 µm;
   - the two foci are within about 2 µm;
   - the pupil offset is ≤ 0.3 mm.
3. **Check for an edge artefact.** At NA 0.5 the edge's response depends
   slightly on wavelength and polarisation. Remount one blade facing the other
   way (scanning from the other side) and average the two results. A real
   offset stays; an edge artefact flips sign and cancels.
4. **Bonus: the spacing.** Scan the HeNe of both arms. Their centre difference
   is the spot spacing, 57 µm by design. Set it with M20 or M21
   (`alignment.md`, step 8).
5. **Restore.** Note the results, remove the blades and detector, and refit
   the sample. Don't touch M3 afterwards.

## 6. Bottom arm

Everything is the same, with these labels:

| Top arm | Bottom arm |
|---|---|
| M9 seated, M8 lifted (stage 1) | M8 seated, M9 lifted |
| FC3 / FC4 | FC5 / FC6 |
| M3 for the fine angle (M4 for the combination) | M2 (M5) |
| M7, BS2: don't touch | M6, BS1: don't touch |

M2's sensitivities are the same as M3's: 5.15 µm per mrad of spot, and
0.34 mm of pupil offset per µm of spot correction. The bottom arm's spot is
57 µm from the top arm's: move the stage there, and keep the same edge section
if you can.

## 7. Error sources

| Source | Effect | Remedy |
|---|---|---|
| Chip or burr on the edge | Shifts one colour's centre more than the other's | Inspect the edge with your imaging system and use a clean section; average both blade orientations |
| Blade reflection back into the objective | Light returned toward the fiber and laser | Tilt the blade about its edge by about 40°, body leaning away from the objective |
| Edge heating (about 3 × 10⁴ W/cm² at the 2923 focus) | Edge creeps, so repeat scans drift | Turn the 2923 down at the laser; the lock-in has signal to spare |
| Detector saturation or non-linearity | Bends the erf curve, so the centre moves | Check against the full-power signal; attenuate only between the blade and the detector |
| Light missing the detector | Same | Element within about 7 mm of the focus, or a collection lens or mirror |
| Stage drift or thermal creep | Centres move between scans | Scan HeNe, 2923, HeNe; keep scans short; let the stage settle after big moves |
| Laser power drift | Bends the curve | Normalise each scan to its own blade-clear level, measured just before |
| Chopper vibration | Noise at the chopping frequency | Chopper on its own post, off the stage |
| Pupil offset combined with a focus mismatch | The spots coincide only in one plane | Step 1 (match the foci) and step 4 (check the pupil) |

## Sources

- Razor apex width under 0.1 µm: [Science of Sharp, "Quantifying sharp"](https://scienceofsharp.com/2014/01/25/quantifying-sharp)
- Knife-edge measurements of tightly focused beams need care; the edge response depends on polarisation: [Frontiers in Physics, 2020](https://www.frontiersin.org/articles/10.3389/fphy.2020.527734/pdf)
- Gentec-EO QS9-IL pyroelectric detector: [listing](https://findlight.net/light-analysis/detectors/thermal-detectors/gentec-eo-discrete-pyroelectric-sensor-qs9-il), [QS-IL family](https://www.gentec-eo.com/products/QS5-IL)
- Ophir RM9 pyroelectric radiometer: [product page](https://www.ophiropt.com/en/f/rm9-pyro-radiometer), [spec sheet](https://op.s2.mks.com/medias/sys_master/opresources/h5c/h58/9917290512414/RM9_RM9-THz_RM9-PD/RM9-RM9-THz-RM9-PD.pdf)
- Thorlabs S401C thermal sensor: [spec sheet](https://azimp.ru/upload/pdf-static/Thorlabs/Thermal%20Power%20Sensors/High%20Resolution/S401C-SpecSheet.pdf)
- Gentec THz detectors, specified for 10–3000 µm: [THZ-B datasheet](https://lasercomponents.com/fileadmin/user_upload/home/Datasheets/gentec/thz-b-detector.pdf)
