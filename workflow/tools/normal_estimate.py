"""Deterministic normal-map estimation from a diffuse image.

Treats luminance as a height field and derives tangent-space normals via Sobel
gradients — the standard, model-free bump-to-normal technique. This is a Tool,
not an Op: it is reproducible and fast. (A learned normal Op can be added later
behind the same port contract.)

Normal encoding: N = normalize(-dZ/dx, -dZ/dy, 1/strength); stored as RGB where
channel = (component * 0.5 + 0.5) * 255. Transparent pixels get a flat
up-facing normal (128,128,255).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..tool import ParamSpec, Port, RunContext, tool
from ..types import Artifact, Image, NormalMap
from . import imaging


def _sobel(channel: np.ndarray, axis: int) -> np.ndarray:
    """3x3 Sobel gradient along the given axis, edge-replicated."""
    k = np.array([1.0, 0.0, -1.0])
    s = np.array([1.0, 2.0, 1.0])
    padded = np.pad(channel, 1, mode="edge")
    out = np.zeros_like(channel)
    for i in range(3):
        for j in range(3):
            weight = (k[j] * s[i]) if axis == 1 else (k[i] * s[j])
            if weight == 0.0:
                continue
            out += weight * padded[i : i + channel.shape[0], j : j + channel.shape[1]]
    return out


def estimate_normal(pixels: np.ndarray, strength: float) -> np.ndarray:
    height = imaging.luminance(pixels)
    gx = _sobel(height, axis=1)
    gy = _sobel(height, axis=0)

    strength = max(0.05, float(strength))
    nx = -gx * strength
    ny = -gy * strength
    nz = np.ones_like(height)

    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    norm[norm == 0] = 1.0
    nx, ny, nz = nx / norm, ny / norm, nz / norm

    rgb = np.stack(
        [
            (nx * 0.5 + 0.5),
            (ny * 0.5 + 0.5),
            (nz * 0.5 + 0.5),
        ],
        axis=-1,
    )
    rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)

    # Flat normal where the diffuse is transparent.
    rgba = imaging.ensure_rgba(pixels)
    transparent = rgba[..., 3] < 128
    rgb[transparent] = np.array([128, 128, 255], dtype=np.uint8)
    return rgb


@tool(
    id="normal_estimate",
    inputs=[Port("image", "image")],
    outputs=[Port("normal", "normal_map")],
    params=[ParamSpec("strength", "float", 2.0, "Bump strength")],
    description="Estimate a tangent-space normal map from a diffuse image (Sobel).",
)
def normal_estimate(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    src: Image = inputs["image"]  # type: ignore[assignment]
    strength = float(params.get("strength", 2.0))
    ctx.progress(0.5, "estimating normals")
    rgb = estimate_normal(src.pixels, strength)
    ctx.progress(1.0, "normal map ready")
    return {"normal": NormalMap(pixels=rgb, meta={"source": "sobel", "strength": strength})}
