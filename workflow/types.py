"""Typed I/O artifacts that flow between workflow components.

Every component declares typed input/output ports using these classes so the
runner can check that connections are compatible before executing. Images are
held as numpy uint8 arrays (H, W, C) in memory; persistence to PNG happens in
the workspace layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


class Artifact:
    """Base class for all typed values passed between components."""

    kind: str = "artifact"


@dataclass
class Image(Artifact):
    """A single RGBA image, uint8 (H, W, 4)."""

    pixels: np.ndarray
    meta: dict[str, Any] = field(default_factory=dict)
    kind: str = "image"

    def __post_init__(self) -> None:
        _validate_rgba(self.pixels)

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])


@dataclass
class ImageBatch(Artifact):
    """An ordered list of images (e.g. batch inference outputs)."""

    images: list[Image]
    meta: dict[str, Any] = field(default_factory=dict)
    kind: str = "image_batch"


@dataclass
class NormalMap(Artifact):
    """A tangent-space normal map, uint8 RGB (H, W, 3). RGB encodes XYZ in
    [0,1], mapped from normal components in [-1,1]."""

    pixels: np.ndarray
    meta: dict[str, Any] = field(default_factory=dict)
    kind: str = "normal_map"

    def __post_init__(self) -> None:
        if self.pixels.ndim != 3 or self.pixels.shape[2] != 3:
            raise ValueError("NormalMap must be (H, W, 3)")
        if self.pixels.dtype != np.uint8:
            raise ValueError("NormalMap must be uint8")


@dataclass
class SpritePair(Artifact):
    """The signature unit: a diffuse image plus an optional same-size normal."""

    diffuse: Image
    normal: NormalMap | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    kind: str = "sprite_pair"

    def __post_init__(self) -> None:
        if self.normal is not None:
            if (self.normal.pixels.shape[0], self.normal.pixels.shape[1]) != (
                self.diffuse.height,
                self.diffuse.width,
            ):
                raise ValueError("normal map must match diffuse size")


@dataclass
class SpriteSheet(Artifact):
    """A packed horizontal strip of equal-size frames, with an optional
    matching normal sheet."""

    diffuse: Image
    frames: int
    frame_w: int
    frame_h: int
    normal: NormalMap | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    kind: str = "sprite_sheet"


@dataclass
class Mask(Artifact):
    """A single-channel mask, uint8 (H, W)."""

    pixels: np.ndarray
    meta: dict[str, Any] = field(default_factory=dict)
    kind: str = "mask"


@dataclass
class Palette(Artifact):
    """An ordered list of RGBA colors."""

    colors: list[tuple[int, int, int, int]]
    meta: dict[str, Any] = field(default_factory=dict)
    kind: str = "palette"


# The registry of known artifact kinds, used by schema validation.
ARTIFACT_KINDS: dict[str, type[Artifact]] = {
    Image.kind: Image,
    ImageBatch.kind: ImageBatch,
    NormalMap.kind: NormalMap,
    SpritePair.kind: SpritePair,
    SpriteSheet.kind: SpriteSheet,
    Mask.kind: Mask,
    Palette.kind: Palette,
}


def _validate_rgba(pixels: np.ndarray) -> None:
    if pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError("Image must be (H, W, 4) RGBA")
    if pixels.dtype != np.uint8:
        raise ValueError("Image must be uint8")


def ensure_rgba(pixels: np.ndarray) -> np.ndarray:
    """Coerce an array to uint8 RGBA (H, W, 4)."""
    arr = np.asarray(pixels)
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:  # grayscale
        arr = np.stack([arr, arr, arr, np.full_like(arr, 255)], axis=-1)
    elif arr.ndim == 3 and arr.shape[2] == 3:  # RGB
        alpha = np.full(arr.shape[:2] + (1,), 255, dtype=np.uint8)
        arr = np.concatenate([arr, alpha], axis=-1)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        pass
    else:
        raise ValueError(f"cannot coerce shape {arr.shape} to RGBA")
    return arr
