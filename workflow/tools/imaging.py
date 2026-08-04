"""Low-level image helpers shared by the deterministic Tools.

Pure numpy / Pillow. No models, no randomness (except where a seed is given).
"""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage

from ..types import ensure_rgba


def to_pil(pixels: np.ndarray) -> PILImage.Image:
    return PILImage.fromarray(ensure_rgba(pixels), mode="RGBA")


def from_pil(img: PILImage.Image) -> np.ndarray:
    return ensure_rgba(np.array(img.convert("RGBA")))


def nearest_resize(pixels: np.ndarray, width: int, height: int) -> np.ndarray:
    """Nearest-neighbour resize — the pixel-art-safe scaler (no interpolation)."""
    img = to_pil(pixels).resize((width, height), PILImage.NEAREST)
    return from_pil(img)


def box_downsample(pixels: np.ndarray, width: int, height: int) -> np.ndarray:
    """Area/box downsample for reducing resolution before quantization. Uses a
    proper averaging filter so a photo-like input collapses cleanly to a low-res
    grid, which is the correct DSP anti-alias step before nearest upscaling."""
    img = to_pil(pixels).resize((width, height), PILImage.BOX)
    return from_pil(img)


def quantize_palette(pixels: np.ndarray, colors: int) -> np.ndarray:
    """Reduce to at most `colors` colors via median-cut, preserving alpha.

    Alpha is thresholded (a sprite edge is either opaque or transparent), then
    the RGB is palette-quantized. Deterministic for a given input.
    """
    rgba = ensure_rgba(pixels)
    alpha = rgba[..., 3]
    hard_alpha = np.where(alpha >= 128, 255, 0).astype(np.uint8)

    rgb = PILImage.fromarray(rgba[..., :3], mode="RGB")
    quant = rgb.quantize(colors=max(2, min(256, colors)), method=PILImage.MEDIANCUT)
    rgb_q = np.array(quant.convert("RGB"), dtype=np.uint8)

    out = np.dstack([rgb_q, hard_alpha]).astype(np.uint8)
    return out


def alpha_bbox(pixels: np.ndarray, threshold: int = 128) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1) of pixels with alpha >= threshold. None if
    fully transparent."""
    rgba = ensure_rgba(pixels)
    mask = rgba[..., 3] >= threshold
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def paste_onto(
    canvas_w: int, canvas_h: int, content: np.ndarray, x: int, y: int
) -> np.ndarray:
    """Paste `content` (RGBA) onto a transparent canvas at (x, y), clipped."""
    canvas = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    content = ensure_rgba(content)
    ch, cw = content.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas_w, x + cw), min(canvas_h, y + ch)
    if x1 <= x0 or y1 <= y0:
        return canvas
    sx0, sy0 = x0 - x, y0 - y
    canvas[y0:y1, x0:x1] = content[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
    return canvas


def luminance(pixels: np.ndarray) -> np.ndarray:
    """Rec.601 luma of the RGB channels as float32 (H, W) in [0,1]."""
    rgba = ensure_rgba(pixels).astype(np.float32) / 255.0
    return 0.299 * rgba[..., 0] + 0.587 * rgba[..., 1] + 0.114 * rgba[..., 2]
