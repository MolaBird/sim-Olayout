"""Turn a G-buffer into a shaded RGBA sprite and a soft shadow mask.

Lighting is mostly overhead (so sprites still look right after the part is
rotated on the canvas), with:

* soft bevels: normals tilted near steps of the height field,
* ambient occlusion: surfaces just below taller neighbours are darkened,
* Blinn-Phong highlights for metals.
"""

from __future__ import annotations

import numpy as np

from olayout.render.materials import MATERIALS, MATERIAL_NAMES
from olayout.render.raster import GBuffer

LIGHT = np.array([-0.32, 0.40, 0.86])
LIGHT = LIGHT / np.linalg.norm(LIGHT)
VIEW = np.array([0.0, 0.0, 1.0])
# Highlights come from a lower light so flat tops stay dark and only curved
# or bevelled surfaces catch a sheen.
SPEC_LIGHT = np.array([-0.55, 0.55, 0.63])
SPEC_LIGHT = SPEC_LIGHT / np.linalg.norm(SPEC_LIGHT)
HALF = (SPEC_LIGHT + VIEW) / np.linalg.norm(SPEC_LIGHT + VIEW)
AMBIENT = 0.42
DIFFUSE = 0.75

BEVEL_INCHES = 0.022  # radius of the soft edge bevel
AO_INCHES = 0.09  # reach of the ambient-occlusion darkening


def box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    """Separable box blur with edge padding (cumulative-sum implementation)."""
    if radius <= 0:
        return image.astype(np.float64, copy=True)
    out = image.astype(np.float64, copy=True)
    size = 2 * radius + 1
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius + 1, radius)
        padded = np.pad(out, pad, mode="edge")
        csum = np.cumsum(padded, axis=axis)
        if axis == 0:
            out = (csum[size:, :] - csum[:-size, :]) / size
        else:
            out = (csum[:, size:] - csum[:, :-size]) / size
    return out


def _downsample(image: np.ndarray, factor: int) -> np.ndarray:
    if factor == 1:
        return image
    h, w = image.shape[:2]
    h2, w2 = h // factor, w // factor
    trimmed = image[: h2 * factor, : w2 * factor]
    shape = (h2, factor, w2, factor) + image.shape[2:]
    return trimmed.reshape(shape).mean(axis=(1, 3))


def shade(gbuffer: GBuffer, supersample: int = 1):
    """Return ``(rgba, shadow)``.

    ``rgba`` is premultiplied uint8 (H, W, 4); ``shadow`` is a float32 (H, W)
    alpha mask in [0, 1] already blurred, both downsampled by ``supersample``.
    """
    height = gbuffer.height
    valid = ~np.isnan(height)
    if not valid.any():
        raise ValueError("empty G-buffer")
    floor = np.nanmin(height) - 0.3
    filled = np.where(valid, height, floor)
    ppi = gbuffer.ppi

    # Soft bevels from the blurred height field.
    bevel_px = max(1, round(BEVEL_INCHES * ppi))
    blurred = box_blur(filled, bevel_px)
    d_row, d_col = np.gradient(blurred)
    slope_x = d_col * ppi
    slope_y = -d_row * ppi  # image rows run toward -y
    grad_normal = np.stack([-slope_x, -slope_y, np.ones_like(slope_x)], axis=-1)
    grad_normal /= np.linalg.norm(grad_normal, axis=-1, keepdims=True)
    weight = np.clip(np.abs(blurred - filled) / 0.006, 0.0, 1.0)[..., None]
    normal = (1.0 - weight) * gbuffer.normal + weight * grad_normal
    normal /= np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-9)

    # Ambient occlusion: how far below the neighbourhood average a pixel is.
    ao_px = max(2, round(AO_INCHES * ppi))
    neighbourhood = box_blur(filled, ao_px)
    occlusion = np.clip((neighbourhood - filled) / 0.35, 0.0, 1.0)
    ao = 1.0 - 0.55 * occlusion

    # Per-material lighting.
    albedo = np.zeros(height.shape + (3,))
    spec_strength = np.zeros(height.shape)
    shininess = np.ones(height.shape)
    metallic = np.zeros(height.shape, bool)
    for index, name in enumerate(MATERIAL_NAMES):
        sel = gbuffer.material == index
        if not sel.any():
            continue
        mat = MATERIALS[name]
        albedo[sel] = mat.albedo
        spec_strength[sel] = mat.specular
        shininess[sel] = mat.shininess
        metallic[sel] = mat.metallic
    if gbuffer.color is not None:  # CAD colours override the material albedo
        has_color = ~np.isnan(gbuffer.color[..., 0])
        albedo[has_color] = gbuffer.color[has_color]
        metallic[has_color] = gbuffer.color[has_color].mean(axis=-1) > 0.45

    n_dot_l = np.clip(normal @ LIGHT, 0.0, 1.0)
    n_dot_h = np.clip(normal @ HALF, 0.0, 1.0)
    # Subtle height cue: higher surfaces are a touch brighter.
    z_min, z_max = np.nanmin(height), np.nanmax(height)
    lift = 0.9 + 0.1 * (filled - z_min) / max(z_max - z_min, 1e-6)
    diffuse = albedo * ((AMBIENT + DIFFUSE * n_dot_l) * ao * lift)[..., None]
    spec = (spec_strength * n_dot_h**shininess * ao)[..., None]
    spec_color = np.where(metallic[..., None], 0.35 + 0.65 * albedo, 1.0)
    color = np.clip(diffuse + spec * spec_color, 0.0, 1.0)

    alpha = valid.astype(np.float64)
    premultiplied = np.concatenate([color * alpha[..., None], alpha[..., None]], axis=-1)
    premultiplied = _downsample(premultiplied, supersample)
    rgba = np.clip(np.round(premultiplied * 255.0), 0, 255).astype(np.uint8)

    coverage = _downsample(alpha, supersample)
    return rgba, shadow_from_coverage(coverage, ppi / supersample)


def shadow_from_coverage(coverage: np.ndarray, ppi: float) -> np.ndarray:
    """Soft drop-shadow mask (0..1) from a sprite's alpha coverage (0..1)."""
    shadow_px = max(1, round(0.05 * ppi))
    return np.clip(box_blur(box_blur(coverage, shadow_px), shadow_px), 0.0, 1.0).astype(np.float32)
