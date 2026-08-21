"""Array adapters for the deterministic pixel Tool nodes."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .geometry import render_normal_supersampled
from .pipelines.continuous import compile_continuous
from .pipelines.pseudo_pixel import snap_pseudo_pixels
from .plan import PixelGeometryPlanValue, resolve_temporal_mode, transform_for_frame
from .sequence import PixelSequenceResult, compile_sequence
from .stages.contour import compile_silhouette
from .stages.evidence import analyse_frame, compile_cell_evidence
from .stages.normals import normals_to_rgb, reduce_normal_cells
from .types import GridSpec, TargetGrid


def _rgba_frames(image: np.ndarray, mask: np.ndarray | None) -> list[np.ndarray]:
    value = np.asarray(image, dtype=np.float32)
    if value.ndim != 4 or value.shape[-1] < 3:
        raise ValueError("IMAGE must have shape [batch,height,width,channels]")
    if mask is None:
        alpha = None
    else:
        alpha = np.asarray(mask, dtype=np.float32)
        if alpha.ndim == 4 and alpha.shape[-1] == 1:
            alpha = alpha[..., 0]
        if alpha.ndim == 2:
            alpha = alpha[None, ...]
        if alpha.ndim != 3:
            raise ValueError("MASK must have shape [batch,height,width]")
        if alpha.shape[1:] != value.shape[1:3] or alpha.shape[0] not in {1, value.shape[0]}:
            raise ValueError("MASK batch and canvas must match IMAGE")

    frames: list[np.ndarray] = []
    for index in range(value.shape[0]):
        rgb = np.clip(value[index, ..., :3], 0.0, 1.0).copy()
        frame_alpha = (
            np.ones(value.shape[1:3], dtype=np.float32)
            if alpha is None
            else np.clip(alpha[0 if alpha.shape[0] == 1 else index], 0.0, 1.0)
        )
        # Chroma-key removal often leaves green in semi-transparent edge
        # pixels. Processing one frame at a time keeps the exact operation
        # order while avoiding several full-batch temporary arrays.
        edge = (frame_alpha > 0.0) & (frame_alpha < 0.999)
        green_spill = (
            edge
            & (rgb[..., 1] > 0.55)
            & (rgb[..., 1] - np.maximum(rgb[..., 0], rgb[..., 2]) > 0.12)
        )
        rgb[..., 1] = np.where(green_spill, np.maximum(rgb[..., 0], rgb[..., 2]), rgb[..., 1])
        rgb[frame_alpha <= 0.0] = 0.0
        rgba = np.empty((*value.shape[1:3], 4), dtype=np.uint8)
        rgba[..., :3] = np.rint(rgb * 255.0).astype(np.uint8)
        rgba[..., 3] = np.rint(frame_alpha * 255.0).astype(np.uint8)
        frames.append(rgba)
    return frames


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


def _target_dimensions_from_canvas(canvas: tuple[int, int], target_size: int) -> tuple[int, int]:
    width, height = (int(canvas[0]), int(canvas[1]))
    longest = max(width, height)
    if longest <= 0:
        raise ValueError("FrameSeq canvas must be non-empty")
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


def _normal_inputs(
    normal: np.ndarray,
    normal_mask: np.ndarray | None,
    rgba_frames: list[np.ndarray],
) -> list[tuple[np.ndarray, np.ndarray]]:
    value = np.asarray(normal, dtype=np.float32)
    if value.ndim != 4 or value.shape[-1] < 3:
        raise ValueError("NORMAL must have shape [batch,height,width,channels]")
    if value.shape[0] != len(rgba_frames) or any(frame.shape[:2] != value.shape[1:3] for frame in rgba_frames):
        raise ValueError("NORMAL batch and canvas must match IMAGE")
    if normal_mask is None:
        return [(frame[..., :3], rgba[..., 3]) for frame, rgba in zip(value, rgba_frames, strict=True)]
    else:
        alpha = np.asarray(normal_mask, dtype=np.float32)
        if alpha.ndim == 4 and alpha.shape[-1] == 1:
            alpha = alpha[..., 0]
        if alpha.ndim == 2:
            alpha = alpha[None, ...]
        if alpha.shape != value.shape[:3]:
            raise ValueError("NORMAL mask batch and canvas must match NORMAL")
    # Geometry sampling clips values at the point of use. Keep views here so a
    # 32-frame normal batch is not duplicated before compilation starts.
    return [(frame[..., :3], frame_alpha) for frame, frame_alpha in zip(value, alpha, strict=True)]


def _compile_frames(
    frames: list[np.ndarray],
    target: TargetGrid,
    profile: str,
    outline: bool,
    outline_color: str,
    sequence_mode: str,
    normal_frames: list[tuple[np.ndarray, np.ndarray]] | None = None,
):
    if sequence_mode == "independent" and len(frames) > 1:
        compiled = [
            compile_continuous(
                [frame],
                [None],
                target,
                profile,
                outline,
                outline_color,
                normal_frames=[normal_frames[index]] if normal_frames is not None else None,
                sequence_mode="continuous",
            )
            for index, frame in enumerate(frames)
        ]
        output_frames = tuple(item.frames[0] for item in compiled)
        output_normals = (
            tuple(item.normals[0] for item in compiled if item.normals is not None)
            if normal_frames is not None
            else None
        )
        return output_frames, output_normals
    result = compile_continuous(
        frames,
        [None] * len(frames),
        target,
        profile,
        outline,
        outline_color,
        normal_frames=normal_frames,
        sequence_mode=sequence_mode,
    )
    return result.frames, result.normals


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
    outline: bool = True,
    outline_color: str = "#000000",
    sequence_mode: str = "auto",
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
    if sequence_mode not in {"auto", "independent", "chunk", "continuous"}:
        raise ValueError(f"unknown sequence mode: {sequence_mode}")
    output_frames, _ = _compile_frames(
        frames, target, profile, bool(outline), str(outline_color), str(sequence_mode)
    )
    return _outputs(output_frames)


def pixelize_pair_batch(
    image: np.ndarray,
    normal: np.ndarray,
    mask: np.ndarray | None,
    normal_mask: np.ndarray | None,
    target_width: int,
    target_height: int,
    profile: str = "production",
    palette_budget: int = 0,
    padding_x: int = -1,
    padding_y: int = -1,
    variants: bool = False,
    target_size: int | None = None,
    outline: bool = True,
    outline_color: str = "#000000",
    sequence_mode: str = "auto",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frames = _rgba_frames(image, mask)
    if len(frames) > 32:
        raise ValueError("sprite-pair pixelization accepts at most 32 frames")
    normals = _normal_inputs(normal, normal_mask, frames)
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
    if sequence_mode not in {"auto", "independent", "chunk", "continuous"}:
        raise ValueError(f"unknown sequence mode: {sequence_mode}")
    del variants
    output_frames, output_normals = _compile_frames(
        frames,
        target,
        profile,
        bool(outline),
        str(outline_color),
        str(sequence_mode),
        normals,
    )
    if output_normals is None or len(output_normals) != len(output_frames):
        raise RuntimeError("pixel compiler did not return one normal per diffuse frame")
    output, output_mask = _outputs(output_frames)
    return output, output_mask, np.stack(output_normals).astype(np.float32)


def pixelize_sequence_reader(
    reader,
    target_width: int,
    target_height: int,
    profile: str = "production",
    palette_budget: int = 0,
    padding_x: int = -1,
    padding_y: int = -1,
    target_size: int | None = None,
    outline: bool = True,
    outline_color: str = "#000000",
    temporal_mode: str = "auto",
    progress=None,
) -> PixelSequenceResult:
    """Compile a lazy bridge ``FrameSeq`` without an IMAGE batch allocation."""

    frames = list(getattr(reader, "frames", ()) or ())
    if not 1 <= len(frames) <= 240:
        raise ValueError("long sequence pixelization accepts 1 to 240 frames")
    canvas = getattr(reader, "canvas", None)
    # Older FrameSeq manifests do not carry decoded canvas metadata.  Resolve
    # the first frame lazily in the ComfyUI node and keep it cached on the
    # bridge reader; the API never decodes image pixels just to fill this in.
    if not isinstance(canvas, (tuple, list)) or len(canvas) != 2:
        resolve_canvas = getattr(reader, "resolve_canvas", None)
        canvas = resolve_canvas() if callable(resolve_canvas) else None
    if not isinstance(canvas, (tuple, list)) or len(canvas) != 2:
        raise ValueError("FrameSeq bridge manifest is missing a shared canvas")
    if target_size is not None:
        target_width, target_height = _target_dimensions_from_canvas(
            (int(canvas[0]), int(canvas[1])), int(target_size)
        )
    target = TargetGrid(
        int(target_width),
        int(target_height),
        None if int(padding_x) < 0 else int(padding_x),
        None if int(padding_y) < 0 else int(padding_y),
        None if int(palette_budget) <= 0 else int(palette_budget),
    )
    if profile not in {"production", "fidelity", "balanced", "graphic"}:
        raise ValueError(f"unknown pixel profile: {profile}")
    resolved_mode = resolve_temporal_mode(str(temporal_mode), getattr(reader, "temporal", None))
    return compile_sequence(
        reader.iter_rgba,
        frames,
        target,
        profile=str(profile),
        outline=bool(outline),
        outline_color=str(outline_color),
        temporal_mode=resolved_mode,
        progress=progress,
    )


def project_normal_to_pixel_plan(
    source: np.ndarray,
    normal: np.ndarray,
    mask: np.ndarray | None,
    plan: PixelGeometryPlanValue | dict,
    frame_index: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Reduce a Lotus normal using the exact diffuse sampling geometry plan."""

    payload = plan.payload if isinstance(plan, PixelGeometryPlanValue) else plan
    if not isinstance(payload, dict):
        raise TypeError("PixelGeometryPlan value is missing")
    transform, target, supersample = transform_for_frame(payload, int(frame_index))
    frames = _rgba_frames(source, mask)
    if len(frames) != 1:
        raise ValueError("PixelGeometryPlan normal projection accepts one selected source frame")
    rgba = frames[0]
    height, width = rgba.shape[:2]
    expected_canvas = tuple(int(item) for item in payload["frames"][int(frame_index)]["canvas"])
    if (width, height) != expected_canvas:
        raise ValueError("source canvas does not match the selected PixelGeometryPlan frame")
    value = np.asarray(normal, dtype=np.float32)
    if value.ndim != 4 or value.shape[0] != 1 or value.shape[1:3] != (height, width) or value.shape[-1] < 3:
        raise ValueError("normal canvas must match the selected PixelGeometryPlan source frame")
    source_alpha = rgba[..., 3].astype(np.float32) / 255.0
    analysis = analyse_frame(
        rgba,
        transform,
        None,
        supersample,
        fast_regions=payload.get("temporal_mode") in {"shared", "flow"},
    )
    evidence, sampling = compile_cell_evidence(
        analysis, target.width, target.height, include_sampling=True
    )
    silhouette = compile_silhouette(evidence)
    # Keep this expression in lock-step with the compiler: the same source
    # alpha/semantic reduction defines the logical transparent boundary.
    from .pipelines.continuous import _alpha_from_semantics

    alpha = _alpha_from_semantics(evidence, silhouette.mask)
    rendered = render_normal_supersampled(value[0, ..., :3], source_alpha, transform, supersample)
    reduced = reduce_normal_cells(rendered, sampling, target.width, target.height)
    return normals_to_rgb(reduced, alpha)[None, ...], alpha[None, ...]


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
