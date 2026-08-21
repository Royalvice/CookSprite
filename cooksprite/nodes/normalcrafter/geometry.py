"""Resolution- and alpha-safe NormalCrafter image adaptation.

NormalCrafter requires a spatial canvas compatible with the SVD VAE.  The
transform below is intentionally one uniform scale plus symmetric padding, so
it never changes an input's aspect ratio.  The inverse converts encoded normal
colors back to vectors before interpolation and renormalizes them afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class SpatialTransform:
    source_width: int
    source_height: int
    resized_width: int
    resized_height: int
    padded_width: int
    padded_height: int
    left: int
    top: int

    @property
    def right(self) -> int:
        return self.padded_width - self.left - self.resized_width

    @property
    def bottom(self) -> int:
        return self.padded_height - self.top - self.resized_height


def spatial_transform(
    width: int, height: int, *, max_resolution: int, multiple: int = 64
) -> SpatialTransform:
    """Create one aspect-preserving SVD canvas transform.

    Inputs at or below ``max_resolution`` are not enlarged.  This keeps small
    sprites sharp and prevents a needless inference-cost increase.
    """

    if width < 1 or height < 1:
        raise ValueError("NormalCrafter image canvas must be non-empty")
    if not 256 <= int(max_resolution) <= 2048:
        raise ValueError("max_resolution must be between 256 and 2048")
    scale = min(1.0, float(max_resolution) / float(max(width, height)))
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    padded_width = ((resized_width + multiple - 1) // multiple) * multiple
    padded_height = ((resized_height + multiple - 1) // multiple) * multiple
    return SpatialTransform(
        source_width=width,
        source_height=height,
        resized_width=resized_width,
        resized_height=resized_height,
        padded_width=padded_width,
        padded_height=padded_height,
        left=(padded_width - resized_width) // 2,
        top=(padded_height - resized_height) // 2,
    )


def _fill_transparent(rgb: np.ndarray, alpha: np.ndarray, iterations: int = 8) -> np.ndarray:
    """Dilate nearby foreground color into transparent texels deterministically."""

    value = np.asarray(rgb, dtype=np.float32).copy()
    known = np.asarray(alpha, dtype=np.float32) > 1.0 / 255.0
    height, width = known.shape
    for _ in range(iterations):
        if bool(known.all()):
            break
        weight = np.zeros((height, width), dtype=np.float32)
        colors = np.zeros_like(value)
        for dy, dx in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            source_y = slice(max(0, -dy), min(height, height - dy))
            source_x = slice(max(0, -dx), min(width, width - dx))
            target_y = slice(max(0, dy), min(height, height + dy))
            target_x = slice(max(0, dx), min(width, width + dx))
            visible = known[source_y, source_x]
            weight[target_y, target_x] += visible
            colors[target_y, target_x] += value[source_y, source_x] * visible[..., None]
        grow = (~known) & (weight > 0.0)
        if not bool(grow.any()):
            break
        value[grow] = colors[grow] / weight[grow, None]
        known[grow] = True
    value[~known] = 0.5
    return value


def _resize_rgb(value: np.ndarray, width: int, height: int) -> np.ndarray:
    image = Image.fromarray(np.rint(np.clip(value, 0.0, 1.0) * 255.0).astype(np.uint8), "RGB")
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return np.asarray(image, dtype=np.float32) / 255.0


def prepare_rgba(
    rgba: np.ndarray, *, max_resolution: int
) -> tuple[np.ndarray, np.ndarray, SpatialTransform]:
    """Prepare one RGBA frame for NormalCrafter without losing source alpha."""

    value = np.asarray(rgba)
    if value.ndim != 3 or value.shape[-1] != 4:
        raise ValueError("NormalCrafter expects an RGBA frame")
    if value.dtype == np.uint8:
        value = value.astype(np.float32) / 255.0
    else:
        value = np.clip(value.astype(np.float32), 0.0, 1.0)
    height, width = value.shape[:2]
    transform = spatial_transform(width, height, max_resolution=max_resolution)
    alpha = value[..., 3]
    filled = _fill_transparent(value[..., :3], alpha)
    composed = value[..., :3] * alpha[..., None] + filled * (1.0 - alpha[..., None])
    resized = _resize_rgb(composed, transform.resized_width, transform.resized_height)
    prepared = np.full((transform.padded_height, transform.padded_width, 3), 0.5, dtype=np.float32)
    y0 = transform.top
    x0 = transform.left
    prepared[y0 : y0 + transform.resized_height, x0 : x0 + transform.resized_width] = resized
    return prepared, alpha.copy(), transform


def _resize_vectors(vectors: np.ndarray, width: int, height: int) -> np.ndarray:
    """Bilinearly interpolate normal vectors in vector rather than RGB space."""

    channels = [
        np.asarray(
            Image.fromarray(channel.astype(np.float32), mode="F").resize(
                (width, height), Image.Resampling.BILINEAR
            ),
            dtype=np.float32,
        )
        for channel in np.moveaxis(vectors, -1, 0)
    ]
    return np.stack(channels, axis=-1)


def restore_normal(
    prediction: np.ndarray,
    alpha: np.ndarray,
    transform: SpatialTransform,
    *,
    strength: float,
    flip_y: bool,
) -> np.ndarray:
    """Crop, vector-resize, normalize, and alpha-restore one normal prediction."""

    value = np.asarray(prediction, dtype=np.float32)
    if value.ndim != 3 or value.shape[-1] < 3:
        raise ValueError("NormalCrafter prediction must have three channels")
    crop = value[
        transform.top : transform.top + transform.resized_height,
        transform.left : transform.left + transform.resized_width,
        :3,
    ]
    # The upstream pipeline exposes output in [-1, 1].  Clamp only after the
    # conversion because blending/decoding can create small overshoots.
    vectors = np.clip(crop, -1.25, 1.25)
    vectors = _resize_vectors(vectors, transform.source_width, transform.source_height)
    vectors[..., :2] *= float(strength)
    if flip_y:
        vectors[..., 1] *= -1.0
    length = np.linalg.norm(vectors, axis=-1, keepdims=True)
    neutral = np.zeros_like(vectors)
    neutral[..., 2] = 1.0
    vectors = np.divide(vectors, np.maximum(length, 1e-6), out=neutral, where=length > 1e-6)
    encoded = vectors * 0.5 + 0.5
    visible = np.asarray(alpha, dtype=np.float32) > 1.0 / 255.0
    encoded[~visible] = (0.5, 0.5, 1.0)
    return np.clip(encoded, 0.0, 1.0)


__all__ = ["SpatialTransform", "prepare_rgba", "restore_normal", "spatial_transform"]
