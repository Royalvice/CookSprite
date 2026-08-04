"""Shared test helpers."""

from __future__ import annotations

import numpy as np

from workflow.component import RunContext
from workflow.types import Image


class NullCtx(RunContext):
    """A RunContext for testing Tools in isolation (no inference)."""

    def progress(self, fraction: float, message: str) -> None:
        pass

    def infer(self, op, model_id, inputs, params):
        raise AssertionError("tool should not call infer")


def gradient_image(w: int = 64, h: int = 64) -> Image:
    """An opaque RGBA gradient with luminance variation (good for normals)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = (xx / max(1, w) * 255).astype(np.uint8)
    g = (yy / max(1, h) * 255).astype(np.uint8)
    b = ((xx + yy) / max(1, w + h) * 255).astype(np.uint8)
    a = np.full((h, w), 255, dtype=np.uint8)
    return Image(pixels=np.dstack([r, g, b, a]))


def blob_image(w: int = 64, h: int = 64) -> Image:
    """A centered opaque disc on transparent background (for crop/align tests)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2, h / 2
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    inside = dist <= min(w, h) * 0.25
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[inside] = [200, 120, 60]
    a = np.where(inside, 255, 0).astype(np.uint8)
    return Image(pixels=np.dstack([rgb, a]))
