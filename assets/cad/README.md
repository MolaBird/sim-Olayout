# Vendor CAD for part sprites

Parts are drawn on the canvas as top-down sprites rendered **once**. By
default a sprite comes from the part's dimensioned primitive model
(`olayout/catalog/parts.py`) and is cached in `.cache/sprites`. If a vendor
CAD file is registered here, the sprite is rendered from the real geometry
instead and saved in `assets/sprites/`, which is committed. Clones without
the CAD files still draw the real parts.

Collision footprints and beam blocking always come from the primitive models,
so CAD only changes how a part looks.

## Adding a part

1. Download the STEP file from the Thorlabs product page (CAD → STEP) and put
   it in this folder. STEP files are not committed; this folder is git-ignored
   except for this README and `cad_parts.json`.
2. Find how the file is oriented:

   ```
   python -m olayout.tools.cad_inspect assets/cad/<file>.step
   ```

   This writes `<file>.views.png` with the part seen down each CAD axis, and
   prints the closed loops of a few cross-sections. Mounting holes show up as
   small circular loops; their centers give the tap axis.
3. Add an entry to `cad_parts.json`:

   ```json
   "FMP1": {
     "file": "FMP1-Step.step",
     "axes": ["z", "x", "y"],
     "origin_mm": [0.0, 22.1, 3.2]
   }
   ```

   * `axes` lists the CAD axis, optionally signed, for each part-frame axis in
     order:
     * **x** points out of the optic's front face, toward the beam.
     * **y** points to your right when you face the optic.
     * **z** points up.
   * `origin_mm` is the CAD point that becomes the part origin. It lies on the
     optic axis, directly above the primary mounting tap. For a post-centred
     mount like the KM100CP, use the mirror's front-surface centre.
   * `material` is optional and replaces the file's colours with one of the
     renderer's materials (`anodized`, `anodized_light`, `steel`, ...).
     Thorlabs STEP files are single-colour gray, so set it to match the rest of
     the canvas.
   * `hide_above_mm` is optional. It hides a lid above that part-frame
     height, so the inside shows from above (the BE02R's mirrors, for
     example). Mounted upside down, the part shows its real underside instead.

   To check an alignment, draw the part's dimensioned outline over the CAD
   render. The outline should hug the silhouette, and the post axis should sit
   on the real mounting hole.
4. Bake its sprites and commit them, so clones without the file get them too:

   ```
   python -m olayout.tools.bake_sprites --prune
   git add assets/sprites
   ```

   The app also re-renders a sprite on its own whenever the local file
   changes (its SHA-1 is recorded in `assets/sprites/index.json`).

STEP support needs the optional `cascadio` and `trimesh` packages, both
listed in `requirements.txt`. Without them the app falls back to the
primitive models.
