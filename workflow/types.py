"""Typed I/O artifacts that flow between workflow tools.

Every tool declares typed input/output ports using these classes so the
runner can check that connections are compatible before executing. Images are
held as numpy uint8 arrays (H, W, C) in memory; persistence to PNG happens in
the workspace layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np


@dataclass
class Artifact:
    """Base class for all typed values passed between tools.

    `kind` is a class-level type tag (used by schema validation); `meta` is a
    free-form dict every artifact carries. Both live here so no subclass repeats
    them."""

    kind: ClassVar[str] = "artifact"
    meta: dict[str, Any] = field(default_factory=dict, kw_only=True)


@dataclass
class Image(Artifact):
    """A single RGBA image, uint8 (H, W, 4)."""

    kind: ClassVar[str] = "image"
    pixels: np.ndarray

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
    """An UNORDERED set of images (e.g. batch inference candidates for one
    prompt). Use FrameSeq when order matters (animation / turntable)."""

    kind: ClassVar[str] = "image_batch"
    images: list[Image]


@dataclass
class FrameSeq(Artifact):
    """An ORDERED sequence of equal-size frames — an animation clip or a
    turntable/direction sweep. `meta` carries e.g. fps or dir_scheme."""

    kind: ClassVar[str] = "frame_seq"
    frames: list[Image]


@dataclass
class NormalMap(Artifact):
    """A tangent-space normal map, uint8 RGB (H, W, 3). RGB encodes XYZ in
    [0,1], mapped from normal components in [-1,1]."""

    kind: ClassVar[str] = "normal_map"
    pixels: np.ndarray

    def __post_init__(self) -> None:
        if self.pixels.ndim != 3 or self.pixels.shape[2] != 3:
            raise ValueError("NormalMap must be (H, W, 3)")
        if self.pixels.dtype != np.uint8:
            raise ValueError("NormalMap must be uint8")


@dataclass
class SpritePair(Artifact):
    """The signature unit: a diffuse image plus an optional same-size normal."""

    kind: ClassVar[str] = "sprite_pair"
    diffuse: Image
    normal: NormalMap | None = None

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

    kind: ClassVar[str] = "sprite_sheet"
    diffuse: Image
    frames: int
    frame_w: int
    frame_h: int
    normal: NormalMap | None = None


@dataclass
class Mask(Artifact):
    """A single-channel mask, uint8 (H, W)."""

    kind: ClassVar[str] = "mask"
    pixels: np.ndarray


@dataclass
class Palette(Artifact):
    """An ordered list of RGBA colors."""

    kind: ClassVar[str] = "palette"
    colors: list[tuple[int, int, int, int]]


# The registry of known artifact kinds, used by schema validation.
ARTIFACT_KINDS: dict[str, type[Artifact]] = {
    Image.kind: Image,
    ImageBatch.kind: ImageBatch,
    FrameSeq.kind: FrameSeq,
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
