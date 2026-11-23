# Optical Layout (`olayout`)

A fast, to-scale 2D layout engine for building optical setups on Thorlabs
breadboards, viewed from above. Place real mounts on the hole grid, check that
nothing collides, and trace Gaussian beams through the setup. You can read beam
parameters anywhere along a beam. All dimensions are in inches.

```
python -m olayout                                   # opens the demo on first run
python -m olayout examples/demo.olayout.json        # open a layout
```

## Setup

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m olayout
.venv\Scripts\python -m pytest                      # 91 tests, headless
```

Requirements: Python ≥ 3.11, PySide6, numpy and shapely. `trimesh` and
`cascadio` are optional; they render vendor STEP files into part sprites (see
[CAD sprites](#cad-sprites)).

## What's in it

| Requirement | Where |
|---|---|
| **Breadboards**<br>Thorlabs MB series and optical tables: 1/4"-20 holes on 1" centers, first hole 1/2" from the edge, column/row numbers, mounting counterbores. | `core/breadboard.py` |
| **Thorlabs parts**<br>KM100, KM100CP, KM100BP, FMP1, KB1X1, CP35, SM1ZB, PH2, BE1, CF125C, ER rods. Each is modeled in 3D from the archived Thorlabs mechanical drawings. | `catalog/parts.py` |
| **Combined units**<br>`base`, `km-mirror`, `mag-km-pellicle`, `km-h-mirror`, `mirror`, `h-mirror`, `collimator`, plus `lens`, `h-lens` and a free `laser` beam source. | `catalog/units.py` |
| **Layers**<br>Bases (post holders, pedestals, forks) and optics (mounts at beam height) are drawn and collision-checked separately. A mount may overhang another unit's base. | `catalog/parts.py` `Layer` |
| **Placement rules**<br>A PH2 screwed into the table snaps hole to hole. A pedestal base (PH2+BE1+CF125C) can stand anywhere: its fork swivels automatically to a reachable hole (screw travel 1.054–2.294") while avoiding other bases. | `engine/fork.py` |
| **Mounting orientations**<br>Choose which tap sits on the post (`F`). The part is rolled about its optic axis, so the top view and post offset change correctly. KM100: bottom/side hole. KM100CP: foot/ear tap, with the mirror always over the post. SM1ZB: bottom/side/top. | `PartSpec.oriented` |
| **Illegal placement**<br>Overlaps per layer, a post holder off a hole, no hole within the fork's reach, a base hanging over the edge. The offending parts turn red and the overlap region is hatched. They are also listed in the *Checks* panel. | `engine/validation.py` |
| **Gaussian beams**<br>A source has a wavelength, waist, waist position, M², power and direction. Beams reflect, split (R/T), focus (thin lens, off-axis steering), and are blocked by mount bodies or clipped by apertures. Tangential and sagittal q are tracked, so curved mirrors used off-axis are astigmatic. | `optics/` |
| **Beam readout**<br>Hover any beam to see w, w₀, distance to waist, R, power and path length. Pin probes (`P`) to keep these in a table. A beam-radius plot shows the whole path to the selected probe. | `ui/panels/beams.py` |
| **Measurements**<br>Measure tool (`M`) snaps to holes, post axes, optic centers and beams. Shows length (decimal, fractional, mm), Δx/Δy and angle. Measurements persist in the file. | `ui/canvas/tools.py` |
| **Fast rendering**<br>Every part is rendered once from above into a shaded sprite (height-field renderer with bevels, ambient occlusion and soft shadows), then cached on disk. The canvas only blits pixmaps. | `render/` |
| **Robustness**<br>Snapshot undo/redo, atomic saves, autosave every 15 s with crash recovery, and an exception hook that logs and keeps running. Analysis errors never take the UI down. | `ui/document.py`, `app.py` |
| **Config files**<br>Plain indented JSON, hand-editable, with units in the key names (`wavelength_nm`). | `core/layout.py` |

## Using it

**Mouse**
- Drag a part from the Library onto the board, or double-click it.
- Drag parts to move them.
  - Hole-mounted parts jump hole to hole.
  - Pedestal parts snap their *optic center onto beams*, so a mirror drops right into an existing beam. Otherwise they snap to a 1/8" grid.
  - Hold **Alt** to move without snapping.
- **Rotate** by dragging the blue handle: 15° steps, **Shift** for free rotation, **Ctrl** for 1° steps.
  - **Alt + handle** aims a mirror: it rotates so the incoming beam reflects toward the cursor.
- Wheel zooms. Middle/right-drag or Space+drag pans. Ctrl+wheel rotates the selection.
- Right-click opens a context menu.

**Keys**

| Key | Action |
|---|---|
| `V` / `M` / `P` | select / measure / probe tool |
| `N` | place a beam source |
| `R`, `Shift+R` | rotate ±90° |
| `Q` / `E` | rotate ±15° (with `Shift`: ±1°) |
| `F` | next mounting orientation |
| arrows | nudge 1 hole (hole-mounted) or 1/8" (`Shift` 1/32", `Ctrl` 1") |
| `Ctrl+D`, `Ctrl+C/V/X`, `Del` | duplicate, clipboard, delete |
| `B` / `G` / `L` / `S` | beams · ghost optic layer (see bases underneath) · labels · snapping |
| `Home` | fit the breadboard |
| `Ctrl+Z` / `Ctrl+Y` | undo / redo |
| `F1` | help |

The Inspector edits the selected part, with fields generated from each unit's
parameter list:
- position and angle
- mounting orientation
- optic type, focal length, radius of curvature, R, T and thickness
- source parameters
- fork direction (Auto or preferred)

It also shows:
- the optic center
- where the beam hits it (incidence angle, offset from center, spot size, clipping)
- which hole the fork screw uses
- the collimator's cage-rod length

With nothing selected, it edits the breadboard and the layout notes.

## Coordinates and conventions

- The board origin is its lower-left corner. x goes right, y goes away from you. Units are inches.
- Angles are counter-clockwise, in degrees.
- A component's `x, y` is its **post axis**, the point it rotates about.
- `angle` is the direction the optic's front face points. For sources it is the beam direction.
- The optic itself sits at the mount's true offset from the post:
  - KM100: the mirror surface is 0.335″ plus the optic thickness in front of the post.
  - KM100CP: exactly over the post.
  - FMP1: −0.085″ plus the thickness.

## File format

```json
{
  "format": "olayout",
  "version": 1,
  "units": "inch",
  "breadboard": {"width": 24.0, "height": 18.0, "pitch": 1.0, "margin": 0.5, "hole_diameter": 0.25, "name": "MB1824"},
  "components": [
    {"id": "c1", "type": "collimator", "x": 1.5, "y": 2.5, "angle": 0.0, "label": "FC1",
     "params": {"source": {"enabled": true, "wavelength_nm": 780.0, "mfd_um": 5.0, "power_mw": 1.0},
                "lens": {"focal_length_mm": 35.0, "distance_mm": 35.0}}},
    {"id": "c2", "type": "km-mirror", "x": 7.904, "y": 2.096, "angle": 135.0, "label": "M1",
     "params": {"optic": {"kind": "mirror", "roc_mm": 0.0}, "fork": {"angle": null}}}
  ],
  "measurements": [{"id": "m1", "a": [7.5, 2.5], "b": [7.5, 8.5]}],
  "probes": [{"id": "p1", "x": 4.2, "y": 2.5, "label": "P1"}]
}
```

Hand edits are welcome:
- Missing fields fall back to defaults.
- Duplicate or missing ids are repaired.
- `"breadboard": {"preset": "MB2436"}` is accepted as a shorthand.
- Unknown part types are reported in *Checks* instead of crashing.

## Part data

Dimensions come from archived Thorlabs drawings (KM100 rev C, KM100CP,
KM100BP rev A, FMP1/M, CP33/CP35, PH1/PH4, TS25H, CF125C, TR2, MB1224). Values
that are not on a drawing are marked `estimate` in `catalog/parts.py`:
- BE1 height
- KB1X1 plate split
- the KM100 back-plate through-hole
- the KM100CP ear height
- the KM100BP arm width and membrane plane

> **SM1ZB:** no Thorlabs drawing or product page exists for this part number.
> SM1ZA is the 30 mm-cage z-translator (no post tap); SM1ZP is the post-mountable
> one (not cage-compatible). The `SM1ZB` model is an approximation of a
> cage-compatible, post-mountable z-translator. Replace it in `make_sm1zb()` once
> the actual drawing is known.

## CAD sprites

If a vendor STEP file is registered in `assets/cad/cad_parts.json`, the part's
sprite is rendered from the real CAD geometry instead of the primitive model.
The file is tessellated once, rendered once from above, and cached in `.cache/`.

KM100 and KM100CP are registered. Their STEP files are git-ignored, so drop
them into `assets/cad/` to use them. `python -m olayout.tools.cad_inspect <file>`
helps register more; see `assets/cad/README.md`. Collision footprints always
come from the dimensioned models.

## Layout of the code

```
olayout/
  core/       units, geometry (poses, ray casting), breadboard, layout document + JSON
  catalog/    3D primitives, Thorlabs parts, combined units (+ parameter fields)
  optics/     Gaussian q/ABCD math, optical elements, 2D beam tracer
  engine/     world placement + build cache, clamping-fork solver, validation, analysis
  render/     top-down rasterizer (primitives + CAD meshes), shading, sprite cache, CAD import
  ui/         theme, document (undo/selection/analysis), canvas (scene, view, items, tools),
              panels (library, inspector, beams, checks), main window, start-up warm-up
  tools/      cad_inspect developer tool
tests/        engine, optics, file format, rendering, CAD, and headless UI tests
examples/     demo.olayout.json
```

## Limitations

- Beams are traced in the table plane. All optics are assumed to sit at one
  beam height, and beams cross each other without interacting.
- Lenses are thin and paraxial. Tilting a lens does not add astigmatism; curved
  mirrors do.
- Clipping is estimated from a 1D aperture at each optic. Beams grazing a mount
  edge are not attenuated.
- Post heights and beam-height consistency are not modeled.
