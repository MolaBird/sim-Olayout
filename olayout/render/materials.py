"""Surface materials for the sprite renderer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    albedo: tuple[float, float, float]
    specular: float = 0.3  # specular strength
    shininess: float = 24.0
    metallic: bool = False  # tint highlights with the albedo


MATERIALS: dict[str, Material] = {
    # Black anodized aluminium: most Thorlabs mounts and post holders.
    "anodized": Material((0.10, 0.105, 0.115), specular=0.35, shininess=14),
    # Slightly lighter anodized parts so stacked plates read separately.
    "anodized_light": Material((0.15, 0.155, 0.17), specular=0.35, shininess=14),
    # Stainless steel: posts, BE1 adapters, clamping forks, cage rods, screws.
    "steel": Material((0.60, 0.615, 0.64), specular=0.55, shininess=12, metallic=True),
    "steel_dark": Material((0.38, 0.39, 0.41), specular=0.45, shininess=12, metallic=True),
    "brass": Material((0.70, 0.56, 0.30), specular=0.6, shininess=40, metallic=True),
    "knob": Material((0.07, 0.07, 0.075), specular=0.18, shininess=10),
    "magnet": Material((0.52, 0.53, 0.56), specular=0.5, shininess=30, metallic=True),
    "fiber": Material((0.93, 0.73, 0.12), specular=0.25, shininess=16),  # yellow SM jacket / boot
    "laser": Material((0.82, 0.83, 0.85), specular=0.35, shininess=20),
    "laser_dark": Material((0.20, 0.21, 0.23), specular=0.30, shininess=18),
    "label": Material((0.85, 0.85, 0.87), specular=0.1, shininess=8),
}

MATERIAL_NAMES: tuple[str, ...] = tuple(MATERIALS)


def material_index(name: str) -> int:
    try:
        return MATERIAL_NAMES.index(name)
    except ValueError as exc:
        raise KeyError(f"unknown material {name!r}; known: {', '.join(MATERIAL_NAMES)}") from exc
