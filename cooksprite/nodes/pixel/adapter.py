"""Array adapters for the deterministic pixel Tool nodes."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .pipelines.continuous import compile_continuous
from .pipelines.pseudo_pixel import snap_pseudo_pixels
from .types import GridSpec, TargetGrid


def _rgba_frames(image: np.ndarray, mask: np.ndarray | None) -> list[np.ndarray]:
    value = np.asarray(image, dtype=np.float32)
    if value.ndim != 4 or value.shape[-1] < 3:
        raise ValueError("IMAGE must have shape [batch,height,width,channels]")
    rgb = np.clip(value[..., :3], 0.0, 1.0).copy()
    if mask is None:
        alpha = np.ones(value.shape[:3], dtype=np.float32)
    else:
        alpha = np.asarray(mask, dtype=np.float32)
        if alpha.ndim == 4 and alpha.shape[-1] == 1:
            alpha = alpha[..., 0]
        if alpha.ndim == 2:
            alpha = alpha[None, ...]
        if alpha.ndim != 3:
            raise ValueError("MASK must have shape [batch,height,width]")
        if alpha.shape[0] == 1 and value.shape[0] > 1:
            alpha = np.repeat(alpha, value.shape[0], axis=0)
    if alpha.shape != value.shape[:3]:
        raise ValueError("MASK batch and canvas must match IMAGE")
    alpha = np.clip(alpha, 0.0, 1.0)
    # Chroma-key removal often leaves green in semi-transparent edge pixels.
    # Correct only that narrow case; fully opaque source colors are untouched.
    edge = (alpha > 0.0) & (alpha < 0.999)
    green_spill = (
        edge
        & (rgb[..., 1] > 0.55)
        & (rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2]) > 0.12)
    )
    rgb[..., 1] = np.where(green_spill, np.maximum(rgb[..., 0], rgb[..., 2]), rgb[..., 1])
    rgb[alpha <= 0.0] = 0.0
    rgba = np.concatenate((rgb, alpha[..., None]), axis=-1)
    quantized = np.rint(rgba * 255.0).astype(np.uint8)
    return [frame for frame in quantized]


def _target_dimensions(image: np.ndarray, target_size: int) -> tuple[int, int]:
    height, width = image.shape[1:3]
    longest = max(int(width), int(height))
    if longest <= 0:
        raise ValueError("IMAGE canvas must be non-empty")
    size = int(target_size)
    if not 16 <= size <= 512:
        raise ValueError("target_size must be between 16 and 512")
    scale = size / longest
    return max(16, round(width * scale)), max(16, round(height * scale))


def _outputs(rgba_frames: Iterable[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    values = np.stack(tuple(rgba_frames), axis=0).astype(np.float32) / 255.0
    rgb = values[..., :3]
    alpha = values[..., 3]
    rgb[alpha <= 0.0] = 0.0
    return rgb, alpha


def pixelize_batch(
    image: np.ndarray,
    mask: np.ndarray | None,
    target_width: int,
    target_height: int,
    profile: str = "production",
    palette_budget: int = 0,
    padding_x: int = -1,
    padding_y: int = -1,
    variants: bool = False,
    target_size: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Compile one shared deterministic pixelization for a ComfyUI batch."""

    frames = _rgba_frames(image, mask)
    if target_size is not None:
        target_width, target_height = _target_dimensions(image, int(target_size))
    target = TargetGrid(
        int(target_width),
        int(target_height),
        None if int(padding_x) < 0 else int(padding_x),
        None if int(padding_y) < 0 else int(padding_y),
        None if int(palette_budget) <= 0 else int(palette_budget),
    )
    if profile not in {"production", "fidelity", "balanced", "graphic"}:
        raise ValueError(f"unknown pixel profile: {profile}")
    # The source facade uses variants for filesystem review exports.  A node
    # has only one image/mask output, so it evaluates the requested profile;
    # the flag is accepted for graph compatibility without creating hidden
    # side effects or unreturned artifacts.
    del variants
    result = compile_continuous(frames, [None] * len(frames), target, profile)
    return _outputs(result.frames)


def snap_batch(
    image: np.ndarray,
    mask: np.ndarray | None,
    grid_mode: str = "auto",
    pixel_size_x: float = 0.0,
    pixel_size_y: float = 0.0,
    phase_x: float = 0.0,
    phase_y: float = 0.0,
    constrained_warp: bool = False,
    palette_budget: int = 32,
    target_width: int = 0,
    target_height: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    frames = _rgba_frames(image, mask)
    spec = GridSpec(
        mode=str(grid_mode),
        pixel_size_x=float(pixel_size_x) if float(pixel_size_x) > 0 else None,
        pixel_size_y=float(pixel_size_y) if float(pixel_size_y) > 0 else None,
        phase_x=float(phase_x),
        phase_y=float(phase_y),
        constrained_warp=bool(constrained_warp),
    )
    snapped = snap_pseudo_pixels(frames, spec, int(palette_budget))
    output = list(snapped.frames)
    width, height = int(target_width), int(target_height)
    if width or height:
        if width <= 0 or height <= 0:
            raise ValueError("target_width and target_height must both be set")
        if snapped.grid.grid_width > width or snapped.grid.grid_height > height:
            raise ValueError("recovered native grid does not fit target canvas")
        placed: list[np.ndarray] = []
        offset_x = (width - snapped.grid.grid_width) // 2
        offset_y = (height - snapped.grid.grid_height) // 2
        for frame in output:
            canvas = np.zeros((height, width, 4), dtype=np.uint8)
            canvas[offset_y : offset_y + frame.shape[0], offset_x : offset_x + frame.shape[1]] = frame
            placed.append(canvas)
        output = placed
    return _outputs(output)
