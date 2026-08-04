"""Deterministic pixelization and pixel-perfect cleanup Tools.

All signal-processing, no model. `pixelize` reduces an image to a crisp low-res
pixel grid (box-downsample to the target cell grid, palette-quantize, then
nearest-upscale back). `pixel_perfect` snaps an already-small image to an exact
integer pixel grid so every logical pixel is a solid NxN block with no stray
half-tones — the classic "perfect pixel" requirement.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..tool import ParamSpec, Port, RunContext, tool
from ..types import Artifact, Image
from . import imaging


@tool(
    id="pixelize",
    inputs=[Port("image", "image")],
    outputs=[Port("image", "image")],
    params=[
        ParamSpec("target_width", "int", 64, "Pixel-grid width (cells)"),
        ParamSpec("target_height", "int", 64, "Pixel-grid height (cells)"),
        ParamSpec("colors", "int", 32, "Max palette colors"),
        ParamSpec("upscale", "int", 1, "Integer upscale of the final grid"),
    ],
    description="Reduce an image to a crisp low-res pixel grid with a limited palette.",
)
def pixelize(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    src: Image = inputs["image"]  # type: ignore[assignment]
    tw = max(1, int(params.get("target_width", 64)))
    th = max(1, int(params.get("target_height", 64)))
    colors = int(params.get("colors", 32))
    upscale = max(1, int(params.get("upscale", 1)))

    ctx.progress(0.2, "downsampling to pixel grid")
    small = imaging.box_downsample(src.pixels, tw, th)

    ctx.progress(0.6, "quantizing palette")
    quant = imaging.quantize_palette(small, colors)

    if upscale > 1:
        ctx.progress(0.85, "integer upscale")
        quant = imaging.nearest_resize(quant, tw * upscale, th * upscale)

    ctx.progress(1.0, "pixelized")
    meta = {**src.meta, "pixelized": True, "grid": [tw, th], "colors": colors}
    return {"image": Image(pixels=quant, meta=meta)}


@tool(
    id="pixel_perfect",
    inputs=[Port("image", "image")],
    outputs=[Port("image", "image")],
    params=[ParamSpec("cell", "int", 1, "Logical pixel cell size to snap to")],
    description="Snap an image to an exact integer pixel grid (no half-tones).",
)
def pixel_perfect(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    src: Image = inputs["image"]  # type: ignore[assignment]
    cell = max(1, int(params.get("cell", 1)))
    h, w = src.height, src.width

    # Reduce to logical resolution (one sample per cell) then re-expand so each
    # logical pixel becomes a solid cell x cell block. This removes any stray
    # interpolation from upstream steps — the defining pixel-perfect operation.
    lw, lh = max(1, w // cell), max(1, h // cell)
    ctx.progress(0.4, "collapsing to logical grid")
    logical = imaging.box_downsample(src.pixels, lw, lh)
    # Hard alpha: a logical pixel is fully opaque or fully clear.
    logical[..., 3] = np.where(logical[..., 3] >= 128, 255, 0).astype(np.uint8)

    ctx.progress(0.8, "expanding to solid cells")
    snapped = imaging.nearest_resize(logical, lw * cell, lh * cell)

    ctx.progress(1.0, "pixel-perfect")
    return {"image": Image(pixels=snapped, meta={**src.meta, "pixel_perfect_cell": cell})}
