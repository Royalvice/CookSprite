"""Pseudo-pixel artwork to a recovered native logical grid."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..color import oklab_to_srgb, srgb_to_oklab
from ..grid.snapper import GridFit, detect_grid
from ..stages.palette import _weighted_kmeans
from ..types import GridSpec


@dataclass(frozen=True)
class SnappedSequence:
    frames: tuple[np.ndarray, ...]
    grid: GridFit
    palette_srgb: np.ndarray
    metrics: dict[str, Any]


def _sample_cell(rgba: np.ndarray, x0: float, x1: float, y0: float, y1: float) -> np.ndarray:
    left = max(0, int(np.floor(x0)))
    right = min(rgba.shape[1], int(np.ceil(x1)))
    top = max(0, int(np.floor(y0)))
    bottom = min(rgba.shape[0], int(np.ceil(y1)))
    block = rgba[top:bottom, left:right].astype(np.float32) / 255.0
    alpha = block[..., 3]
    active = alpha > 0.02
    if not np.any(active):
        return np.zeros(4, dtype=np.float32)
    rgb = block[..., :3][active]
    weights = alpha[active]
    # A robust medoid retains an actual source colour cluster rather than an
    # average between two pseudo-pixel cells.
    lab = srgb_to_oklab(rgb)
    center = np.average(lab, axis=0, weights=np.maximum(weights, 1e-7))
    selected = int(np.argmin(np.sum((lab - center) ** 2, axis=1)))
    coverage = float(np.mean(alpha))
    return np.concatenate((rgb[selected], [1.0 if coverage >= 0.50 else 0.0])).astype(np.float32)


def _sample_frame(rgba: np.ndarray, grid: GridFit) -> np.ndarray:
    output = np.zeros((grid.grid_height, grid.grid_width, 4), dtype=np.float32)
    for y in range(grid.grid_height):
        for x in range(grid.grid_width):
            output[y, x] = _sample_cell(rgba, grid.cuts_x[x], grid.cuts_x[x + 1], grid.cuts_y[y], grid.cuts_y[y + 1])
    return output


def _shared_palette(frames: list[np.ndarray], budget: int) -> tuple[np.ndarray, np.ndarray]:
    values = np.concatenate([srgb_to_oklab(frame[..., :3][frame[..., 3] > 0.0]) for frame in frames], axis=0)
    if not values.size:
        raise ValueError("snapped sequence contains no foreground")
    unique = np.unique(np.rint(oklab_to_srgb(values) * 255.0).astype(np.uint8), axis=0)
    count = min(budget, len(unique))
    weights = np.ones(len(values), dtype=np.float64)
    centers, _ = _weighted_kmeans(values, weights, count, np.empty((0, 3), dtype=np.float32))
    srgb = np.rint(np.clip(oklab_to_srgb(centers), 0.0, 1.0) * 255.0).astype(np.uint8)
    srgb = np.unique(srgb, axis=0).astype(np.float32) / 255.0
    return srgb, srgb_to_oklab(srgb)


def snap_pseudo_pixels(frames: list[np.ndarray], spec: GridSpec, palette_budget: int = 32) -> SnappedSequence:
    started = time.perf_counter()
    grid = detect_grid(frames, spec)
    sampled = [_sample_frame(frame, grid) for frame in frames]
    palette_srgb, palette_lab = _shared_palette(sampled, palette_budget)
    outputs: list[np.ndarray] = []
    for frame in sampled:
        lab = srgb_to_oklab(frame[..., :3])
        distance = np.sum((lab[..., None, :] - palette_lab[None, None, :, :]) ** 2, axis=3)
        labels = np.argmin(distance, axis=2)
        output = np.zeros(frame.shape, dtype=np.uint8)
        foreground = frame[..., 3] > 0.0
        output[foreground, :3] = np.rint(palette_srgb[labels[foreground]] * 255.0).astype(np.uint8)
        output[foreground, 3] = 255
        outputs.append(output)
    return SnappedSequence(
        tuple(outputs),
        grid,
        palette_srgb,
        {
            "wall_seconds": time.perf_counter() - started,
            "frame_count": len(outputs),
            "grid": grid.as_dict(),
            "palette_budget": palette_budget,
            "palette_actual": len(palette_srgb),
        },
    )
