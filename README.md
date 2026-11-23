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
.venv\Scripts\python -m pytest                      # 136 tests, headless
```

Requirements: Python ≥ 3.11, PySide6, numpy and shapely. `trimesh` and
`cascadio` are optional. They are only needed to re-render part sprites from
vendor STEP files; the rendered sprites are committed (see
[CAD sprites](#cad-sprites)).

## What's in it

| Requirement | Where |
|---|---|
| **Breadboards**<br>Your 21" × 20" board by default, plus the Thorlabs MB series and optical tables: 1/4"-20 holes on 1" centers, first hole 1/2" from the edge, column/row numbers, mounting counterbores. | `core/breadboard.py` |
| **Thorlabs parts**<br>KM100, KM100CP, KM100BP with its BP108 pellicle, FMP1, KB1X1, CP35, SM1ZB, ER cage rods (any length), BE02R reflective beam expander, PH2, BE1, CF125C. Each is modeled in 3D from Thorlabs mechanical drawings and checked against the vendor STEP files. | `catalog/parts.py` |
| **Combined units**<br>`base`, `km-mirror`, `mag-km-pellicle` (KM100BP + BP108 8:92 pellicle on a KB1X1), `km-h-mirror`, `mirror`, `h-mirror`, `kb-mirror` and `kb-h-mirror` (FMP1 on a KB1X1), and `collimator` (SM1ZB on a PH2 + four ER rods of any length + CP35 holding the lens), plus `lens`, `h-lens`, the `beam-expander` (BE02R) and its mirrored version, and a free `laser` beam source. | `catalog/units.py` |
| **Layers**<br>Bases (post holders, pedestals, forks) and optics (mounts at beam height) are drawn and collision-checked separately. A mount may overhang another unit's base. | `catalog/parts.py` `Layer` |
| **Placement rules**<br>A PH2 screwed into the table snaps hole to hole. A pedestal base (PH2+BE1+CF125C) can stand anywhere: its fork swivels automatically to a reachable hole (screw travel 1.054–2.294") while avoiding other bases. | `engine/fork.py` |
| **Mounting orientations**<br>Choose which tap sits on the post (`F`). The part is rolled about its optic axis, so the top view and post offset change correctly. KM100/KM100BP: bottom or side hole (the side hole mirrors the L-shaped plates). KM100CP: foot or ear tap, with the mirror always over the post. SM1ZB: its only 8-32 hole is in the top edge, so it hangs inverted with the knob up; choose whether the housing faces the fiber or the lens. | `PartSpec.oriented` |
| **Illegal placement**<br>Overlaps per layer, a post holder off a hole, no hole within the fork's reach, a base hanging over the edge. The offending parts turn red and the overlap region is hatched. They are also listed in the *Checks* panel. Any problem or beam warning can be **ignored** when you know better: it stays listed (dimmed), stops counting and is saved with the layout. | `engine/validation.py` |
| **Gaussian beams**<br>A source has a wavelength, waist, waist position, M², power and direction. Beams reflect, split (R/T), focus (thin lens, off-axis steering), and are blocked by mount bodies or clipped by apertures. Tangential and sagittal q are tracked, so curved mirrors used off-axis are astigmatic. Kinematic mounts have L-shaped plates, so a beam transmitted through a splitter leaves through the open side; if it walks into the mount instead, *Checks* says so and suggests the other orientation. | `optics/` |
| **Beam readout**<br>Hover any beam to see w, w₀, distance to waist, R, power and path length. Pin probes (`P`) to keep these in a table. A beam-radius plot shows the whole path to the selected probe. | `ui/panels/beams.py` |
| **Measurements**<br>Measure tool (`M`) snaps to holes, post axes, optic centers and beams. Shows length (decimal, fractional, mm), Δx/Δy and angle. Measurements persist in the file. | `ui/canvas/tools.py` |
| **Fast rendering**<br>Every part is rendered once from above into a shaded sprite (height-field renderer with bevels, ambient occlusion and soft shadows). Sprites from vendor CAD are committed in `assets/sprites/`; the rest are cached on disk. The canvas only blits pixmaps. | `render/` |
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
- The wheel zooms about the cursor. Middle/right-drag or Space+drag pans, and so does Shift+wheel (sideways).
- **Alt+wheel** rotates the selection by 5° (Alt+Shift: 1°).
- On a touchpad, two-finger scrolling pans and pinching (or Ctrl+scroll) zooms.
- Right-click opens a context menu. On a part with problems it offers to ignore them.

**Keys**

| Key | Action |
|---|---|
| `V` / `M` / `P` | select / measure / probe tool |
| `N` | place a beam source |
| `R`, `Shift+R` | rotate ±90° |
| `Q` / `E` | rotate ±15° (with `Shift`: ±1°) |
| `F` | next mounting orientation |
| `K` | lift a mount off its KB1X1 magnetic base, or put it back |
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
- for the collimator: the fiber-to-lens distances its rods allow
- the part's placement problems, including ignored ones

With nothing selected, it edits the breadboard and the layout notes.

**Lifting a mount off its KB1X1.** Mounts on a KB1X1 magnetic base (the
pellicle beamsplitter and the `kb-` mirrors) can be lifted off and put back,
just like on the table. Press `K`, untick *On its KB1X1* in the Inspector, or
right-click the part. While a mount is off:
- it stops reflecting, blocking and colliding, so beams go straight through;
- only the post and the KB1X1's bottom plate stay, and the mount is drawn faintly where it goes back.

Putting it back restores it exactly. The state is saved in the file as
`"kb1x1": {"seated": false}` and can be undone.

**Ignoring problems.** The model is simplified, so you may know that a flagged
overlap or clipped beam is fine. Select entries in the *Checks* panel and press
*Ignore*, or right-click the part. Ignored problems stay in the list, dimmed,
but no longer turn parts red or count in the status bar. *Restore* brings them
back. Ignoring is undoable and saved in the file. Deleting a part forgets its
ignored problems.

## Coordinates and conventions

- The board origin is its lower-left corner. x goes right, y goes away from you. Units are inches.
- Angles are counter-clockwise, in degrees.
- A component's `x, y` is its **post axis**, the point it rotates about.
- `angle` is the direction the optic's front face points. For sources it is the beam direction.
- The optic itself sits at the mount's true offset from the post:
  - KM100: the mirror surface is 0.335″ plus the optic thickness in front of the post.
  - KM100CP: exactly over the post.
  - FMP1: −0.085″ plus the thickness.
  - KM100BP: the pellicle membrane (front of the BP108 frame) is 0.77″ in front of the post and 0.05″ off-axis.
  - Collimator: the fiber tip is 0.20″ in front of the post; the lens sits mid-CP35.
- The beam expander has no post: its `x, y` is where the input beam meets mirror 1, and `angle` is the input beam's direction.

## File format

```json
{
  "format": "olayout",
  "version": 1,
  "units": "inch",
  "breadboard": {"width": 21.0, "height": 20.0, "pitch": 1.0, "margin": 0.5, "hole_diameter": 0.25, "name": "Board 21x20"},
  "components": [
    {"id": "c1", "type": "collimator", "x": 1.5, "y": 2.5, "angle": 0.0, "label": "FC1",
     "params": {"source": {"enabled": true, "wavelength_nm": 780.0, "mfd_um": 5.0, "power_mw": 1.0},
                "lens": {"focal_length_mm": 25.4, "distance_mm": 25.4}}},
    {"id": "c2", "type": "km-mirror", "x": 7.904, "y": 2.096, "angle": 135.0, "label": "M1",
     "params": {"optic": {"kind": "mirror", "roc_mm": 0.0}, "fork": {"angle": null}}}
  ],
  "measurements": [{"id": "m1", "a": [7.5, 2.5], "b": [7.5, 8.5]}],
  "probes": [{"id": "p1", "x": 4.2, "y": 2.5, "label": "P1"}],
  "ignored": [{"kind": "overlap", "components": ["c2", "c5"], "layer": "base"}]
}
```

`ignored` lists the accepted problems by kind and the components involved:
- placement problems are `overlap` (with `layer` `base` or `optic`),
  `off-hole`, `no-hole`, `off-board` and `invalid`;
- beam warnings are `clipped`, `own-mount` and `segment-limit`.

Hand edits are welcome:
- Missing fields fall back to defaults.
- Duplicate or missing ids are repaired.
- `"breadboard": {"preset": "MB2436"}` is accepted as a shorthand.
- Unknown part types are reported in *Checks* instead of crashing.

## Part data

Dimensions come from Thorlabs drawings (KM100 rev C, KM100CP, KM100BP rev A,
FMP1/M, CP33/CP35, SM1ZB rev B, PH1/PH4, TS25H, CF125C, TR2, MB1224). Each
model was checked against the vendor STEP file: cross-sections at beam height
(which also showed that the KM100-family back plates are L-shaped) and
overlays of the top-view outlines. A few values are still marked `estimate`
in `catalog/parts.py`:
- the BP108 frame thickness, 0.19″ (Thorlabs only gives its Ø1.38″ OD, and it is not in the KM100BP STEP)
- where the fiber tip sits in the SM1ZB carriage
- the KB1X1 plate split

**Collimator.** The SM1ZB, four ER cage rods and a CP35 form one unit. The
rods are plain Ø6 mm rods drawn at the length you set (1.5″ by default, i.e.
ER1.5). The lens sits inside the CP35, so it is not drawn, but it still acts on
the beam. Both plates clamp the rods with setscrews, so the CP35 slides along
them.

The fiber-to-lens distance can be as short as 5 mm, for short-focus lenses.
Closer than the CP35 itself can get, the cage plate stops against the
SM1ZB and the lens sits recessed behind it, in a lens tube. The Inspector
notes how far.

The far end is set by the rods, which must still reach into the SM1ZB plate.
With 1.5″ rods it is about 30 mm with the SM1ZB housing toward the fiber, and
40 mm with it toward the lens. Each extra inch of rod adds 25.4 mm. Distances
outside the range are clamped and noted in the Inspector.

**Beam expander (BE02R).** A housing (base flange, side walls, lid) with two
fixed spherical mirrors. The geometry comes from the vendor STEP file, whose
mirror surfaces are exact spheres:
- The beam enters an SM05 port and meets a convex mirror (R 51.5 mm) at 21.7°
  incidence.
- It then meets a concave mirror (R 100 mm, f = 50 mm) 24.6 mm away, at 16.9°.
- It leaves through the port in the 79° face, turned 9.6° from its input
  direction.

The walls block every other path, and the expander also works backwards as
a 2X reducer.

Both mirrors are modeled as spherical surfaces used off-axis, so the output
is not perfectly round: it is 2.03X larger in the fold plane and 1.89X
across it. It also converges very slightly in the fold plane (focus about
3 m away). Mirror 1's edge is 2.2 mm from the beam axis, so beams over about
Ø3.8 mm clip.

The mirrored version is the same BE02R mounted upside down, so its beam
path folds the other way. The expander is free-standing (`Mounting.FREE`):
the app does not model how it is held, by clamps on its flange or the 8-32
plate on its side. Its sprite shows it with the lid off, so the mirrors are
visible.

## CAD sprites

Every Thorlabs part is registered in `assets/cad/cad_parts.json` with an
alignment measured from its mounting holes and bores, so its sprite is
rendered from the real CAD geometry. Collision footprints and beam blocking
always come from the dimensioned models.

The vendor STEP files are git-ignored, but the sprites rendered from them
are committed: one PNG per part and mounting orientation in
`assets/sprites/`, plus `index.json`. A fresh clone therefore draws the real
parts without the STEP files or the CAD libraries. Each sprite records the
SHA-1 of the STEP file and the renderer settings it was made with. When the
STEP file is present and differs (or the renderer changed), the app
re-renders the sprite in place; commit the updated PNGs. To re-render the
whole catalog at once, and to delete sprites nothing uses any more:

```
python -m olayout.tools.bake_sprites --prune
```

`python -m olayout.tools.cad_inspect <file>` helps register more parts; see
`assets/cad/README.md`.

## Layout of the code

```
olayout/
  core/       units, geometry (poses, ray casting), breadboard, layout document + JSON
  catalog/    3D primitives, Thorlabs parts, combined units (+ parameter fields)
  optics/     Gaussian q/ABCD math, optical elements, 2D beam tracer
  engine/     world placement + build cache, clamping-fork solver, validation, analysis
  render/     top-down rasterizer (primitives + CAD meshes), shading, sprite caches, CAD import
  ui/         theme, document (undo/selection/analysis), canvas (scene, view, items, tools),
              panels (library, inspector, beams, checks), main window, start-up warm-up
  tools/      developer tools: cad_inspect (register a CAD file), bake_sprites
assets/
  cad/       CAD registry (cad_parts.json); the vendor STEP files themselves are not committed
  sprites/   committed top-down sprites rendered from those files
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
- Post heights and beam-height consistency are not modeled (e.g. the BE02R's
  beam is 0.98″ above its base).
- The SM1ZB's fine z-adjustment is not modeled separately; set the fiber-to-lens
  distance directly.

## Development notes

- `OLAYOUT_SETTINGS_FILE=<file.ini>` keeps the app's preferences (recent files,
  view toggles) in that file instead of the user's registry. The test suite
  always sets it.
- `OLAYOUT_CACHE=<dir>` moves the caches of procedural sprites and CAD meshes,
  which default to `.cache/`.
- `ruff check olayout tests` uses the rules in `pyproject.toml`.
