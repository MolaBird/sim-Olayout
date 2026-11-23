"""Bake the CAD sprites of every catalog part into ``assets/sprites``.

    python -m olayout.tools.bake_sprites [--prune]

Renders each CAD-registered part in every orientation the catalog uses,
skipping sprites whose stamp (CAD file SHA-1 + renderer settings) is
current.  Commit ``assets/sprites`` afterwards, so clones without the vendor
STEP files still draw the real parts.  ``--prune`` deletes baked sprites
that no catalog part uses any more (e.g. after re-aligning a CAD entry).
"""

from __future__ import annotations

import argparse
import sys

from olayout import paths
from olayout.catalog.units import catalog_parts
from olayout.render.cad_sprites import CadSprites


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prune", action="store_true", help="delete baked sprites no catalog part uses")
    args = parser.parse_args(argv)

    store = CadSprites(paths.BAKED_SPRITES_DIR)
    wanted = {}
    for part in catalog_parts():
        key = store.key_for(part.part_id, part.roll, part.z_offset)
        if key is not None:
            wanted.setdefault(key, part)

    missing = 0
    for key, part in sorted(wanted.items()):
        before = store.entry(key)
        sprite = store.get(part.part_id, part.roll, part.z_offset)
        if sprite is None:
            state = "MISSING: no CAD file here and nothing baked"
            missing += 1
        elif store.entry(key) != before:
            state = "rendered"
        elif store.registry[part.part_id].readable():
            state = "up to date"
        else:
            state = "kept (CAD file not here)"
        print(f"{key:<28} {state}")

    stale = store.prune(set(wanted)) if args.prune else []
    for key in stale:
        print(f"{key:<28} deleted (unused)")
    print(f"{len(wanted)} CAD sprites in {paths.BAKED_SPRITES_DIR}, {missing} missing, {len(stale)} pruned")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
