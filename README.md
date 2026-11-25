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
.venv\Scripts\python -m pytest                      # 246 tests, headless
```

Requirements: Python ≥ 3.11, PySide6, numpy and shapely. `trimesh` and
`cascadio` are optional. They are only needed to re-render part sprites from
vendor STEP files; the rendered sprites are committed (see
[CAD sprites](#cad-sprites)).

## What's in it

| Requirement | Where |
|---|---|
| **Breadboards**<br>Your 21" × 20" board by default, plus the Thorlabs MB series and optical tables: 1/4"-20 holes on 1" centers, first hole 1/2" from the edge, column/row numbers, mounting counterbores. | `core/breadboard.py` |
| **Thorlabs parts**<br>KM100, KM100CP, KM100BP and KM200BP kinematic pellicle mounts, BP107 and BP207 fixed pellicle mounts, BP108 (Ø1") and BP208 (Ø2") pellicles, KMSR (for the PFSQ10-03-P01 1" square mirror), FMP1, KB1X1, CP35, M1RMSS adapter, LMM40X-P01 reflective objective, SM1ZB, ER cage rods (any length), BE02R reflective beam expander, MAX311D NanoMax stage, MRAK25-P01 knife-edge prism mirror on a KM100B platform, ID25 iris, LB1 beam dump, PH2, BE1, CF125C. Each is modeled in 3D from Thorlabs mechanical drawings and checked against the vendor STEP files (the MRAK25, KM100B, ID25 and LB1 come from catalog dimensions only). Four simple machined parts (a BE02R riser, a knife-edge holder, a beam block and a pinhole target) have shop drawings generated from the same model: see [Custom parts](#custom-machined-parts). | `catalog/parts.py` |
| **Combined units**<br>`base`, `km-mirror`, eight magnetic pellicle beamsplitters (Ø1" or Ø2" pellicle, in a kinematic or a fixed mount, on a KB1X1, on a pedestal base or in a hole: see below), `km-h-mirror`, `mirror`, `h-mirror`, `kb-mirror` and `kb-h-mirror` (FMP1 on a KB1X1), and `collimator` (SM1ZB on a PH2 + four ER rods of any length + CP35 holding the lens), plus `sq-km-mirror` (PFSQ10-03-P01 in a KMSR on a pedestal base), `lens`, `h-lens`, `objective` and `h-objective` (LMM40X-P01 in an M1RMSS, in a CP35 on a post), the `beam-expander` (BE02R) and its mirrored version, `beam-expander-riser` (BE02R on a machined riser bolted to two holes), the `sample-stage` (MAX311D with a sample on its platform), the `knife-edge` combiner (MRAK25-P01 on a KM100B, pedestal base) and `knife-edge-post` (MRAK25-P01 in a machined holder on a post in a hole), `iris` and `h-iris` (ID25 on a post), `pinhole-target` (one or two pinholes in a plate on a post), `beam-dump` (LB1) and `beam-block`, and a free `laser` beam source. | `catalog/units.py` |
| **Layers**<br>Bases (post holders, pedestals, forks) and optics (mounts at beam height) are drawn and collision-checked separately. A mount may overhang another unit's base. | `catalog/parts.py` `Layer` |
| **Placement rules**<br>A PH2 screwed into the table snaps hole to hole, and so does the MAX311D stage (its base's counterbores land on the grid). A pedestal base (PH2+BE1+CF125C) can stand anywhere: its fork swivels automatically to a reachable hole (screw travel 1.054–2.294") while avoiding other bases. | `engine/fork.py` |
| **Mounting orientations**<br>Choose which tap sits on the post (`F`). The part is rolled about its optic axis, so the top view and post offset change correctly. KM100/KM100BP/KM200BP: bottom or side hole (the side hole mirrors the L-shaped plates). KM100CP: foot or ear tap, with the mirror always over the post. SM1ZB: its only 8-32 hole is in the top edge, so it hangs inverted with the knob up; choose whether the housing faces the fiber or the lens. | `PartSpec.oriented` |
| **Illegal placement**<br>Overlaps per layer, a post holder off a hole, no hole within the fork's reach, a base hanging over the edge. The offending parts turn red and the overlap region is hatched. They are also listed in the *Checks* panel. Any problem or beam warning can be **ignored** when you know better: it stays listed (dimmed), stops counting and is saved with the layout. | `engine/validation.py` |
| **Gaussian beams**<br>A source has a wavelength, waist, waist position, M², power and direction. Beams reflect, split (R/T), focus (thin lens, off-axis steering), and are blocked by mount bodies or clipped by apertures (a beam meeting an optic at incidence i lights a strip 1/cos i wider than itself). Tangential and sagittal q are tracked, so curved mirrors used off-axis are astigmatic. Kinematic mounts have L-shaped plates, so a beam transmitted through a splitter leaves through the open side; if it walks into the mount instead, *Checks* says so and suggests the other orientation. | `optics/` |
| **Enclosure framing**<br>1/2" T-slot (80/20-style) rails and posts, and enclosure boards, drawn end to end on the canvas. Rails at beam height, posts and boards stop beams; rails lying on the table let them pass over. Framing never collides with itself (corners, boards in slots), only with the rest. | `catalog/units.py` |
| **Beam combining**<br>Press `C` and click two beams. A beamsplitter goes where their paths cross, turned so the reflected beam leaves along the transmitted one. The tool picks the mounting orientation that lets both beams through, and previews the result before you commit. | `engine/combine.py` |
| **Beam readout**<br>Hover any beam to see w, w₀, distance to waist, R, power and path length. Pin probes (`P`) to keep these in a table. A beam-radius plot shows the whole path to the selected probe. | `ui/panels/beams.py` |
| **Measurements**<br>Measure tool (`M`) snaps to holes, post axes, optic centers and beams. Shows length (decimal, fractional, mm), Δx/Δy and angle. Measurements persist in the file. | `ui/canvas/tools.py` |
| **Fast rendering**<br>Every part is rendered once from above into a shaded sprite (height-field renderer with bevels, ambient occlusion and soft shadows). Sprites from vendor CAD are committed in `assets/sprites/`; the rest are cached on disk. Parts that are uniform along their length (rails, boards, cage rods) are rendered as a 1" piece and stretched, so any length draws instantly. The canvas only blits pixmaps. | `render/` |
| **Robustness**<br>Snapshot undo/redo, atomic saves, autosave every 15 s with crash recovery, and an exception hook that logs and keeps running. Analysis errors never take the UI down. | `ui/document.py`, `app.py` |
| **Config files**<br>Plain indented JSON, hand-editable, with units in the key names (`wavelength_nm`). | `core/layout.py` |

## Using it

**Mouse**
- Drag a part from the Library onto the board, or double-click it.
- Rails and boards are drawn: press at one end, release at the other.
  - Their ends snap to holes and to other rails' and boards' ends, otherwise to a 1/8" grid at 15° steps (Shift: any angle, Alt: no snapping).
  - A click instead of a drag places the default 12" length.
  - The tool stays on for the next one until `Esc` or a right-click.
- Drag parts to move them.
  - Hole-mounted parts jump hole to hole.
  - Pedestal parts snap their *optic center onto beams*, so a mirror drops right into an existing beam. Near a point where two beams cross, they snap onto the crossing (yellow ring), where a beamsplitter combines them. Otherwise they snap to a 1/8" grid.
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
| `C` | combine two beams with a beamsplitter (see below) |
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
- the optic center (for a single optic)
- where each beam hits it (incidence angle, offset from center, spot size, clipping), and
  how many beam radii it is from the optic's edge when that is under 3 w
- which hole the fork screw uses
- for the collimator: the fiber-to-lens distances its rods allow
- the part's placement problems, including ignored ones

With nothing selected, it edits the breadboard and the layout notes.

**Lifting a mount off its KB1X1.** Mounts on a KB1X1 magnetic base (the
pellicle beamsplitters and the `kb-` mirrors) can be lifted off and put back,
just like on the table. Press `K`, untick *On its KB1X1* in the Inspector, or
right-click the part. While a mount is off:
- it stops reflecting, blocking and colliding, so beams go straight through;
- only the post and the KB1X1's bottom plate stay, and the mount is drawn faintly where it goes back.

Putting it back restores it exactly. The state is saved in the file as
`"kb1x1": {"seated": false}` and can be undone.

**Thumbscrew side.** A PH2 screwed into a hole can be turned so its
thumbscrew points right, left, back or front of the unit (*Thumbscrew* in the
Inspector, `"post": {"thumbscrew": "left"}` in the file). Crowded neighbours
then stop colliding on their thumbscrews.

**Pellicle beamsplitters.** Eight units, all 8:92 (R:T) pellicles on a KB1X1
magnetic base, each on a pedestal base or (`-h-`) on a PH2 in a hole:

| Unit | Pellicle | Mount |
|---|---|---|
| `mag-pellicle`, `mag-h-pellicle` | BP108, Ø1″ | BP107 fixed |
| `mag-km-pellicle`, `mag-km-h-pellicle` | BP108, Ø1″ | KM100BP kinematic |
| `mag-pellicle-2in`, `mag-h-pellicle-2in` | BP208, Ø2″ | BP207 fixed |
| `mag-km-pellicle-2in`, `mag-km-h-pellicle-2in` | BP208, Ø2″ | KM200BP kinematic |

The pellicle's ring drops into the mount: onto a lip in the KM100BP / KM200BP
front plate, or behind the two clamp tabs of the BP107 / BP207 U-shaped
cradle. The beam meets the 2 µm membrane on the ring's front face. Only the
split ratio is editable (other BPx0x coatings share the ring).

**Ignoring problems.** The model is simplified, so you may know that a flagged
overlap or clipped beam is fine. Select entries in the *Checks* panel and press
*Ignore*, or right-click the part. Ignored problems stay in the list, dimmed,
but no longer turn parts red or count in the status bar. *Restore* brings them
back. Ignoring is undoable and saved in the file. Deleting a part forgets its
ignored problems.

**Combining two beams.** Press `C` (or *Tools ▸ Combine Beams*), click the
beam that should pass straight through, then the beam to reflect onto it.
- The beamsplitter goes where their paths cross. Its surface bisects the
  two beams, so the reflected beam leaves along the transmitted one.
- If a splitter is selected (including any mount whose optic is set to
  *Beamsplitter*), it is moved there. Otherwise a new pellicle beamsplitter is
  added. Splitters also offer this from the right-click menu and the Inspector.
- Every mounting orientation, and either face toward the reflected beam, is
  traced. The tool keeps the one that sends the most light from *both* beams
  along the combined path. For example, an L-shaped kinematic mount is turned
  so the transmitted beam leaves through its open side.
- While you hover the second beam, a ghost shows the splitter, and a dashed
  line shows the combined beam with the power each beam contributes. The
  click is one undoable step; `Esc` or a right-click backs out.
- Beams can be followed through windows and on-axis lenses to their crossing.
  Parallel beams, and paths that would cross only behind a source or after a
  beam is stopped, are explained instead.
- A hole-mounted splitter lands on the nearest hole, so it misses the crossing
  slightly; the tool reports by how much.
- The splitter's unused output port usually ends on its own mount, which
  *Checks* lists. That is expected for a combiner: ignore it there.

## Coordinates and conventions

- The board origin is its lower-left corner. x goes right, y goes away from you. Units are inches.
- Angles are counter-clockwise, in degrees.
- A component's `x, y` is its **post axis**, the point it rotates about.
- `angle` is the direction the optic's front face points. For sources it is the beam direction.
- The optic itself sits at the mount's true offset from the post:
  - KM100: the mirror surface is 0.335″ plus the optic thickness in front of the post.
  - KM100CP: exactly over the post.
  - KMSR: the mirror rests against the holder's back wall, 0.427″ plus the optic thickness in front of the post.
  - FMP1: −0.085″ plus the thickness.
  - KM100BP: the pellicle membrane (front of the BP108 ring) is 0.545″ in front of the post and 0.05″ off-axis.
  - KM200BP: the membrane (front of the BP208 ring) is 0.59″ in front of the post and 0.045″ off-axis.
  - BP107 / BP207: the membrane is 0.165″ in front of the post, right over it.
  - Collimator: the fiber tip is 0.20″ in front of the post; the lens sits mid-CP35.
  - Objective: `angle` is the direction it looks. Its nose is 1.05″ in front of the post, and a collimated beam focuses one working distance (7.8 mm) beyond it.
- The beam expander has no post: its `x, y` is where the input beam meets mirror 1, and `angle` is the input beam's direction. The same holds on its riser, whose two bolts land on holes when mirror 1 sits on a hole row at a whole-inch x.
- `knife-edge-post`: `x, y` is the post axis and `angle` the direction the beams leave. The edge is 4.5 mm ahead of the post, so two beams fed along the post's own hole column leave 9.0 mm apart.
- A rail or board's `x, y` is one end, and it runs `length` along `angle`. A post's `x, y` is its axis.
- The sample stage's `x, y` is its base centre on the hole grid, and `angle` is the direction the sample faces.

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
- where the fiber tip sits in the SM1ZB carriage
- the KB1X1 plate split

The KMSR, M1RMSS, LMM40X-P01 and the pellicle parts (BP107, BP207, BP108,
BP208, KM200BP) were measured directly on their STEP files.

**Square mirror (KMSR + PFSQ10-03-P01).** The KMSR's holder grips the 1″
square, 6 mm mirror between two lips, against a solid back wall. It is a
mirror mount: anything transmitted ends in the holder. Its 8-32 tap sits
under the mirror, 0.625″ below the optic axis. The second tap, in the back
face, would point the mirror at the ceiling, so it is not offered.

**Sample stage (MAX311D).** Measured on its STEP file. The base's counterbored
holes sit on a 12.6 mm pitch made to fit both 1" and 25 mm grids. The stage's
position is the 1" grid point they fit best, 0.8 mm from the base centre: on a
breadboard hole, five of them land within 0.4 mm of holes.
- The sample faces out of one of the platform's two free faces (*Sample face*);
  the two choices are mirror images.
- *Sample position* puts its surface in front of the platform face (it rides
  on a bracket at beam height). The stage's travel, ±2 mm toward the objective
  and across, moves it further; requests beyond that are clamped.
- The sample can be opaque, a mirror, partly reflective or transparent, like
  any optic. On an opaque sample the Inspector reports the spot size.
- Only the sample moves with the travel; the drawing of the stage stays put.
- The stage itself is at table level (its platform is 2.46" high), so beams
  pass over it and mounts may overhang it.

**Enclosure framing.** The 1/2" T-slot is a generic four-slot profile; its slot
sizes are estimates (80/20's own "1/2"" profile, 1050, is 1" × 1/2"). What a
member meets depends on its height:
- a rail at beam height blocks beams and collides with mounts;
- a rail lying on the table only collides with bases (beams pass over it);
- posts and boards stand from the table to above the beams: they block beams
  and collide with both mounts and bases.

Framing pieces never collide with each other, since corners overlap and boards
sit in the rails' slots. They are free-standing, so they may stand off the
breadboard too.

**Knife-edge combiner (MRAK25-P01 on a KM100B).** A right-angle N-BK7 prism
whose two 25 mm legs are silvered on the outside and meet in a bevel-free 90°
edge. Two beams arriving square to the output from opposite sides leave side
by side, as close together as their own sizes allow: the edge wastes no
aperture. A beam whose centre is 1.5 w from the edge keeps 99.9%; at 1.3 w it
loses 0.5%. The edge sits over the post, and the component's angle is the
direction the beams leave. Thorlabs shows the prism on a KM100B platform mount
(1.92″ square); no vendor CAD is registered for either, so they are drawn from
catalog dimensions, and the KM100B's thicknesses are estimates. The prism is
25 mm tall (its legs are 25 mm × 25 mm faces). `knife-edge-post` holds the
same prism in a machined holder on a post instead (see below).

**Iris (ID25).** A Ø43.7 mm × 6.6 mm housing standing across the beam, its
opening set from Ø1 to Ø25 mm. Beams through the opening pass (clipped if it is
small for them); the blades and the housing stop everything else. Drawn from
catalog dimensions.

**Objective (CP35 + M1RMSS + LMM40X-P01).** The brass M1RMSS ring threads
into the CP35 flush with its front face, and the objective seats on it.
- The LMM40X is reflective, with a convex and a concave mirror. In the beam
  plane it acts as an ideal lens, so it is modelled as a thin lens with its
  5.0 mm effective focal length. The lens sits at its principal plane, 2.8 mm
  in front of the nose, so a collimated beam focuses at the 7.8 mm working
  distance.
- Its Ø5.1 mm entrance pupil (NA 0.50) is the aperture that clips beams.
- The internal mirrors are not traced, and the central obscuration of the
  secondary mirror is ignored.
- The focal length, working distance and pupil can be edited for other RMS
  objectives.
- The CP35 is drawn from its committed CAD sprite, which shows the plate
  rolled 180° about its axis (as in the collimator). From above, a cage plate
  looks the same either way up.

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
3 m away). Mirror 1's edge is 2.2 mm from the beam axis (2.1 mm across the
beam, which meets it at 21.7°), so beams over about Ø3.6 mm clip.

The mirrored version is the same BE02R mounted upside down, so its beam
path folds the other way. The expander is free-standing (`Mounting.FREE`):
the app does not model how it is held, by clamps on its flange or the 8-32
plate on its side. `beam-expander-riser` holds it by that plate, on a machined
riser bolted to two table holes. Its sprite shows it with the lid off, so the
mirrors are visible.

## Custom machined parts

Four simple parts make the two-beam layout (v3) mount from the hole grid. They
are modeled like the Thorlabs parts, and their shop drawings are generated
from the same constants, so a drawing cannot drift from the simulated part:

```
python -m olayout.tools.drawings        # writes docs/drawings/*.svg
```

| Part | Unit | Drawing |
|---|---|---|
| BE02R riser: an L-block that sets the BE02R's beam height and bolts to two holes | `beam-expander-riser` | `docs/drawings/be02r-riser.svg` |
| Knife-edge holder: the MRAK25's edge 4.5 mm ahead of the post | `knife-edge-post` | `docs/drawings/knife-edge-holder.svg` |
| Beam block, 1″ × 1″ × 3/8″ | `beam-block` | `docs/drawings/beam-block.svg` |
| Dual-pinhole alignment target | `pinhole-target` | `docs/drawings/dual-pinhole-target.svg` |

`docs/custom-parts.md` covers what to check before machining and how each
part is used.

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
  engine/     world placement + build cache, clamping-fork solver, validation, analysis,
              beam combining (where and how to place a splitter)
  render/     top-down rasterizer (primitives + CAD meshes), shading, sprite caches, CAD import
  ui/         theme, document (undo/selection/analysis), canvas (scene, view, items, tools),
              panels (library, inspector, beams, checks), main window, start-up warm-up
  tools/      developer tools: cad_inspect (register a CAD file), bake_sprites,
              drawings (shop drawings of the custom parts)
assets/
  cad/       CAD registry (cad_parts.json); the vendor STEP files themselves are not committed
  sprites/   committed top-down sprites rendered from those files
tests/        engine, optics, file format, rendering, CAD, and headless UI tests
examples/     demo.olayout.json
docs/         the two-beam layout: alignment procedure and co-alignment methods
              (breadboard-optimized.olayout.json), the v3 layout (v3-layout.md),
              custom parts and their drawings (custom-parts.md, drawings/)
```

## Limitations

- Beams are traced in the table plane. All optics are assumed to sit at one
  beam height, and beams cross each other without interacting.
- Lenses (and the reflective objective) are thin and paraxial. Tilting a lens does not add astigmatism; curved
  mirrors do.
- Clipping is estimated from a 1D aperture at each optic, in the table plane
  (an iris is treated as a slit). Beams grazing a mount edge are not attenuated.
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
