# Precise HeNe / 2923 nm co-alignment without a mid-IR camera

The HeNe in each arm is only useful if it marks where the 2923 nm beam goes.
This note covers three things:
- how well the two colours must be co-aligned;
- why maximising 2923 power through pinholes defined by the HeNe runs out of
  precision;
- methods that do much better with uncooled single-element detectors.

The numbers refer to `breadboard-optimized.olayout.json`. See `alignment.md`
for the full procedure.

## 1. What precision is needed

Downstream of the pellicles, everything is reflective: the BE02R, flat silver
mirrors, the knife edge and the LMM40X. Once the two colours leave the pellicle
together they cannot separate. Whatever angle error they have at the pellicle,
they carry to the sample.

| Error at the pick-off (before the 2× expander) | In the objective | On the sample |
|---|---|---|
| Angle 1 mrad | 0.52 mrad | the two spots 2.6 µm apart |
| Angle 0.2 mrad | 0.10 mrad | 0.5 µm |
| Position 0.1 mm | 0.2 mm in the Ø5.1 mm pupil | spots don't move; slightly more clipping |

Notes on the table:
- The expander has magnification M = 1.94: it halves angles and doubles
  offsets.
- On the sample, a spot moves by f × angle, with f = 5 mm.

The angle is what matters. The 2923 spot is w₀ ≈ 2.4 µm (radius), so a useful
target is **≤ 0.2 mrad at the pick-off**, i.e. ≤ 0.5 µm on the sample. Position
only needs about 0.1 mm.

A third quantity, focus depth, is set by each colour's collimation. The model
puts the HeNe focus 5.4 µm in front of the 2923's. Methods C and D measure it
directly.

## 2. Why maximising power through pinholes stalls

**A peak is flat at the top.**
- A pinhole small compared with the beam (radius w) transmits
  T(x) ≈ T₀ (1 − 2x²/w²) near centre. A relative noise ε therefore hides offsets
  up to x ≈ w √(ε/2).
- At the pick-off the 2923 beam is w ≈ 1.1 mm. With 1% noise each pinhole is
  only found to about 80 µm.
- Placing the pinholes on the HeNe has the same problem, at about 70 µm.
- Two pinholes L apart give an angle of about √2 × 100 µm / L. That is
  0.5 mrad for L = 300 mm, about 1.2 µm on the sample, before any other error.

**Scattering and diffraction.**
- Iris blades and pinhole edges scatter, and the 2923 diffracts 4.6× more than
  the HeNe.
- What the detector reads then depends on how much of that light it collects.
- Iris blades also do not close exactly concentrically, so the reference moves
  with the aperture setting.

**Mechanics.**
- Position and angle are measured together and are coupled, so each step
  disturbs the other and you iterate.
- Seating repeatability, post creep and stage backlash all add directly to the
  error.

**Rayleigh range.**
- At the pick-off the 2923's Rayleigh range is about 1.3 m; the HeNe's is
  about 5 m.
- Over a long baseline the two beams change size and wavefront curvature
  differently, so their transmission peaks have different widths and shapes.
- A long baseline is exactly what the pinhole method needs, so this works
  against it.

**Detector noise and drift.** Thermal sensors are slow and drift. A peak
search is the worst case for that, because the error grows as √ε (see the
first point).

A realistic result is 0.3–1 mrad at the pick-off, i.e. 0.8–2.6 µm between the
spots on the sample.

## 3. Three principles that fix it

**1. Measure the angle in the far field.**
- Focus the sampled beam with a long-focal-length *reflective* optic: a
  protected-silver concave mirror, used at a few degrees, or an off-axis
  parabola. A lens would focus the two colours in different planes.
- In the focal plane a beam's position is f × angle, whatever its position on
  the mirror. Angle and position separate, so there is nothing to iterate.
- Pick-off seating, mirror drift and table creep move both colours together.
  The measurement compares the colours with each other, so these drop out.

**2. Measure on a slope, not on a peak.**
- Use a knife edge parked at its 50% point, a split detector, or anything else
  that reads out an offset linearly and with its sign.
- A knife edge transmits T(x) = ½[1 + erf(√2 x / w)]. Its slope at the centre
  is 0.80/w, so noise ε gives σₓ ≈ 1.25 w ε, against w √(ε/2) for a peak:
  - at 1% noise, 6× better;
  - at 0.1% noise, 18× better;
  - the gain grows as the signal improves.
- A useful consequence: in the far field, σ_θ = σₓ/f = 1.25 ε λ/(π w), with no
  dependence on f.
  - It is the beam's own divergence (0.85 mrad for the 2923 at w = 1.1 mm)
    times 1.25 ε.
  - At 1% noise that is 11 µrad; at 0.1%, 1 µrad.
  - The focal length only sets the physical scale. Longer is easier to
    resolve with an ordinary micrometer.

**3. Modulate, and use one flat-response detector.**
- A chopper and lock-in (or modulating the laser) removes thermal drift and
  background.
- A thermal or pyroelectric detector responds the same to 633 nm and
  2.92 µm, so one detector and one blade serve both colours.
- For reading positions you need linearity, not absolute calibration. Collect
  everything that passes the blade with a large detector right behind it, so
  edge diffraction does not bias the reading.

## 4. Methods

### A. Far-field knife edge on the alignment board (recommended)

**Setup.** Both pick-offs (M9 for the top arm, M8 for the bottom) send their
sample down the same line, off this board at x = 7.5″, so one setup serves both
arms. On the alignment board:
1. A concave silver mirror, f = 300–1000 mm, used at ≤ 3°. The astigmatism
   at 3° is f × 0.003 ≈ 1.4 mm at f = 500 mm, which is negligible here.
2. At its focus, a razor blade on an x–y micrometer or motorized stage. A
   differential micrometer helps.
3. Behind the blade, a large-area thermal sensor, or a pyroelectric detector
   with a chopper.

Focal spot radii and depths of focus at f = 500 mm:

| | Spot radius w_f | 10–90% edge width | Rayleigh range at focus |
|---|---|---|---|
| 2923 | 0.42 mm | 0.54 mm | 190 mm |
| HeNe | 0.10 mm | 0.13 mm | 53 mm |

**Procedure.**
1. **Find the focal plane** with the HeNe by making its 10–90% edge width as
   small as possible. The mirror is achromatic, so the 2923 focuses in the
   same plane.
   - Keep the blade within ±5 mm of that plane.
   - The leftover sensitivity to beam position there is under 1 µm, i.e.
     2 µrad.
2. **Measure the HeNe's centre in x**: the blade position where half the
   power passes. Fit the whole erf scan for better precision. Repeat for y.
3. **Steer the 2923 onto the same points**, using M3 + M4 (top arm) or
   M2 + M5 (bottom arm).
   - The quickest way is to park the blade at the HeNe's 50% point.
   - Steer the 2923 until exactly half its power passes. Measure its total
     power first with the blade out.
   - That turns the adjustment into a live null.
4. **Check the position** with the blade close to the pick-off (the near
   field). A 0.1 mm tolerance is easy there: 1% noise gives about 14 µm.

**Precision.** About 10 µrad at the pick-off with 1% noise, i.e. under 0.03 µm
on the sample. That is 20× better than needed, so drift sets the real limit.
Always approach the 50% point from the same side, to avoid backlash.

**Ready-made alternative.** A scanning-slit beam profiler with a pyroelectric
detector (e.g. Ophir NanoScan, pyro version) measures the centre and width of
both colours live, uncooled. Put it at the mirror's focus.

### B. Knife-edge bi-cell: a live, power-normalised null

**Setup.** Put a second MRAK25 knife-edge mirror, or a D-shaped mirror, at the
focal plane. Its edge splits the focal spot between two detectors.

**What it measures.**
- The difference over the sum, (P₁ − P₂)/(P₁ + P₂) = erf(√2 x / w_f), is zero
  when the spot sits on the edge.
- It has a sign.
- It ignores laser power fluctuations.
- Its slope is 1.6/w_f, twice the single blade's: about 5 µrad at 1% noise.

**Use.**
1. Centre the edge on the HeNe, i.e. balance the two detectors.
2. Steer the 2923 to bring its own difference back to zero.

Use two matched thermal or pyroelectric detectors, so one balance point holds
for both colours. Each edge measures one axis: rotate the mirror 90° for the
other.

This is the best choice if you want to monitor co-alignment continuously.

### C. Knife edge at the sample with the MAX311D (commissioning check)

**What it measures.** The spots themselves, after every optic including the
objective. Do it once at set-up, and again whenever the objective or a
collimator changes.

**The edge.**
- Use an edge on a substrate that transmits both 633 nm and 2.92 µm: a
  chromium or gold edge on CaF₂ or sapphire, or a razor blade.
- Avoid N-BK7, which absorbs beyond about 2.5 µm, and UV-grade fused silica,
  whose OH absorption band sits near 2.7 µm.

**Scanning.**
1. Scan the edge across each spot with the stage's piezos, recording the
   transmitted power. The light leaves the focus at about 0.4 NA, so put the
   detector within a few millimetres, or collect with a mirror.
2. An erf fit gives each colour's spot centre to tens of nanometres. It also
   gives the spot sizes and the 57 µm spacing.
3. Repeat at several focus positions (z). That gives:
   - each colour's focal plane, i.e. the 5.4 µm depth mismatch, so you can
     match the HeNe collimation to the 2923;
   - whether a spot walks sideways with defocus, which means its beam is
     off-centre in the pupil.

**Cost.** It needs room for the edge and a detector at the sample position,
so it is a set-up exercise rather than a routine check.

### D. Photothermal readout through the HeNe (visible detection only)

**When it applies.** The 2923 must be absorbed at the focus. Water absorbs it
within about 1 µm.

**How it works.** The 2923 heats the absorber, and the co-focused HeNe probe
feels that heat:
- **Deflection.** Chop the 2923 and send the transmitted HeNe onto a silicon
  quadrant photodiode. Read the difference channel with a lock-in.
  - The deflection reverses sign across the heated spot.
  - It is zero when the HeNe sits at its centre: a sign-sensitive null right
    at the sample, read with a cheap silicon detector.
  - The sum channel (the thermal lens) peaks there.
- **Thermochromic film or temperature-sensitive fluorescence.** Put it at the
  sample plane and watch it with your existing camera. The 2923 spot shows up
  as a coloured or dimmed patch. Heat spreading blurs it, but symmetrically,
  so its centre is still the 2923 spot's centre.
- **Burn mark.** Expose an absorbing test film (thermal paper, a polymer
  film) for a moment. 2.7 mW in a 2.4 µm spot is about 3 × 10⁴ W/cm². Then see
  where the HeNe sits on the mark. This is destructive, but trivial to do.

**Precision.** Sub-micrometre on the sample, limited by thermal drift and how
symmetric the heated spot is. This is the natural in-situ check if your
samples are watery.

### E. Back-coupling into the fibers (autocollimation)

**Setup.** On the pick-off's KB1X1, use a flat mirror square to the beam
instead of the 45° mirror. Both colours then retrace their paths into their own
fibers. Each couples back only when its beam is square to that one mirror.
Maximising both back-coupled powers therefore makes the two beams parallel.

**Why it is better than pinholes.** The fiber's mode acts as a perfect
pinhole: no scattering, no blade wander, no placement error.

**Precision.** It is still a peak search, with an angular half-width of λ/(2πw)
in mirror tilt. That gives about 20–50 µrad, angle only.

**Caveats.**
- Each source needs a fiber coupler or circulator.
- The 2923 laser must tolerate the light returned into it, so check for
  feedback.
- The HeNe meets the pellicle twice on the way back, so only 0.64% of it
  returns.

## 5. Comparison

| Method | What it measures | Angle precision at the pick-off | Error on the sample | Extra hardware | Effort |
|---|---|---|---|---|---|
| Pinholes + max 2923 power (current) | angle and position, coupled | 0.3–1 mrad | 0.8–2.6 µm | — | low, slow to converge |
| A. Far-field knife edge | angle | ~10 µrad (1% noise) | < 0.03 µm | concave mirror, blade on a micrometer stage, detector | moderate |
| B. Knife-edge bi-cell | angle, live | ~5 µrad | < 0.02 µm | knife-edge mirror, two matched detectors | moderate |
| C. Knife edge at the sample | the spots (x, y, focus depth) | — | ~0.05 µm | edge on CaF₂/sapphire, detector at the sample | moderate–high |
| D. Photothermal | the spots, during use | — | ~0.1–0.5 µm | absorbing sample, Si quadrant PD + lock-in, or a film | moderate |
| E. Fiber back-coupling | angle | 20–50 µrad | ~0.05–0.13 µm | fiber couplers, retro mirror | moderate, feedback risk |

## 6. Recommended workflow

1. **Routine co-alignment:** method A on the alignment board. Seat M9, align
   the top arm's 2923 to its HeNe, lift M9, then the same with M8. Upgrade to B
   if you want to monitor continuously.
2. **Commissioning:** method C once. It confirms the spots, the 57 µm spacing,
   the focus-depth match and the pupil centring, all at the sample.
3. **In use:** method D if your samples absorb at 2.9 µm. It checks the
   overlap without touching the optics.

## Formulas

- **Spot offset on the sample:** Δ = f_obj × θ_pick / M. With f_obj = 5 mm
  and M = 1.94, that is 2.6 µm per mrad at the pick-off.
- **Beam divergence:** θ_div = λ/(π w).

  | | At w | θ_div |
  |---|---|---|
  | 2923 | 1.1 mm | 0.85 mrad |
  | HeNe | 0.98 mm | 0.21 mrad |

- **Focal spot at focal length f:** w_f = f θ_div.
- **Rayleigh range at the focus:** z_R = π w_f² / λ.
- **Position error, for relative noise ε:**

  | Method | σₓ |
  |---|---|
  | Peak search | w √(ε/2) |
  | Single knife edge | 1.25 w ε |
  | Bi-cell | 0.63 w ε |

- **Far-field angle error:** σ_θ = σₓ / f. For an edge this is 1.25 ε θ_div,
  whatever the focal length.
- **Defocus coupling:** at a distance Δz from focus, a beam at position x₀
  appears shifted by Δz x₀ / f.
