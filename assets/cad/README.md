# Vendor CAD for part sprites

Parts are drawn on the canvas as top-down sprites rendered **once** and cached
in `.cache/sprites`. By default a sprite comes from the part's dimensioned
primitive model (`olayout/catalog/parts.py`). If a vendor CAD file is
registered here, the sprite is rendered from the real geometry instead.

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
4. Restart the app. Sprites are re-rendered automatically when the file or
   its entry changes.

STEP support needs the optional `cascadio` and `trimesh` packages, both
listed in `requirements.txt`. Without them the app falls back to the
primitive models.
