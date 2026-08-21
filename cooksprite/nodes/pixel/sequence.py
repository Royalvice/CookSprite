"""Disk-backed long FrameSeq lowering for the Pixel compiler.

The existing batch compiler remains the compatibility path for saved graphs and
Sprite chunks.  This module deliberately never materializes a high-resolution
sequence in RAM: source frames and final logical-pixel frames are spooled one
at a time, while the bounded in-memory working set contains only compact cell
evidence needed for one deterministic shared palette.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .geometry import build_transform, foreground_bbox
from .pipelines.continuous import (
    _alpha_from_semantics,
    _micro_detail_mask,
    _role_adjusted_evidence,
    _selective_outer_stroke,
    _supersample,
    compile_continuous,
    stabilize_label_step,
)
from .plan import PixelGeometryPlanValue, plan_payload, resolve_temporal_mode
from .stages.contour import compile_internal_strokes, compile_silhouette
from .stages.evidence import CellEvidence, analyse_frame, compile_cell_evidence
from .stages.palette import build_palette, clean_label_clusters, labels_to_rgba, map_palette
from .stages.tones import extract_chunk_tone_roles
from .types import TargetGrid

Progress = Callable[[str, int, int], None]


@dataclass
class PixelSequenceResult:
    """Ephemeral result passed directly to ``CS_StoreArtifact``.

    It owns its temporary directory.  The Comfy graph only keeps this object
    long enough for the bridge output node to upload each frame in order.
    """

    temporary: tempfile.TemporaryDirectory[str]
    frame_paths: tuple[Path, ...]
    plan: PixelGeometryPlanValue
    metrics: dict[str, object]

    def iter_frames(self) -> Iterator[np.ndarray]:
        for path in self.frame_paths:
            yield np.load(path, allow_pickle=False)

    def close(self) -> None:
        self.temporary.cleanup()


def _normalise_rgba(frame: np.ndarray) -> np.ndarray:
    value = np.asarray(frame)
    if value.ndim != 3 or value.shape[-1] != 4:
        raise ValueError("FrameSeq reader must yield RGBA frames")
    if value.dtype != np.uint8:
        value = np.rint(np.clip(value, 0.0, 1.0) * 255.0).astype(np.uint8)
    return np.ascontiguousarray(value)


def _union_box(boxes: Iterable[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    values = list(boxes)
    if not values:
        raise ValueError("FrameSeq contains no frames")
    return (
        min(item[0] for item in values),
        min(item[1] for item in values),
        max(item[2] for item in values),
        max(item[3] for item in values),
    )


def _sequence_shared(
    source_paths: list[Path],
    target: TargetGrid,
    profile: str,
    outline: bool,
    outline_color: str,
    temporal_mode: str,
    transform,
    supersample: int,
    output_dir: Path,
    progress: Progress | None,
) -> tuple[tuple[Path, ...], dict[str, object]]:
    """Build one shared palette then emit frames with only prior-flow state."""

    compiled: list[tuple[CellEvidence, object, object]] = []
    count = len(source_paths)
    for index, source_path in enumerate(source_paths):
        rgba = np.load(source_path, mmap_mode="r", allow_pickle=False)
        analysis = analyse_frame(
            rgba,
            transform,
            None,
            supersample,
            # This is the same sequence fast-region path used by the v2
            # chunk compiler, but one full-resolution image exists at a time.
            fast_regions=True,
        )
        evidence = compile_cell_evidence(analysis, target.width, target.height)
        silhouette = compile_silhouette(evidence)
        internal = compile_internal_strokes(evidence, silhouette.mask)
        compiled.append((evidence, silhouette, internal))
        if progress:
            progress("分析全局几何与调色板", index + 1, count)

    dark_values = [
        evidence.lab[..., 0][silhouette.mask]
        for evidence, silhouette, _ in compiled
        if np.any(silhouette.mask)
    ]
    shared_dark_limit = (
        float(np.quantile(np.concatenate(dark_values), 0.42)) if dark_values else None
    )
    stroke_masks = [
        internal.mask | _selective_outer_stroke(evidence, silhouette.mask, shared_dark_limit)
        for evidence, silhouette, internal in compiled
    ]
    evidences = [item[0] for item in compiled]
    silhouettes = [item[1].mask for item in compiled]
    tones = extract_chunk_tone_roles(evidences, silhouettes, stroke_masks)
    details = [
        _micro_detail_mask(evidence, tone, silhouette) if not outline else np.zeros_like(strokes)
        for evidence, tone, silhouette, strokes in zip(evidences, tones, silhouettes, stroke_masks, strict=True)
    ]
    adjusted = [
        _role_adjusted_evidence(evidence, tone, preserve_highlights=not outline)
        for evidence, tone in zip(evidences, tones, strict=True)
    ]
    budget = target.resolved_palette_budget
    if profile == "fidelity":
        budget = min(256, max(budget, round(budget * 1.20)))
    elif profile == "graphic":
        budget = max(8, round(budget * 0.72))
    palette = build_palette(
        adjusted,
        tones,
        silhouettes,
        budget,
        preserve_highlights=not outline,
        outline_color=outline_color if outline else None,
        equal_frame_weight=True,
        canonical_order=True,
    )

    paths: list[Path] = []
    previous_labels: np.ndarray | None = None
    previous_evidence: CellEvidence | None = None
    for index, (evidence, tone, silhouette, strokes, detail) in enumerate(
        zip(adjusted, tones, silhouettes, stroke_masks, details, strict=True)
    ):
        alpha = _alpha_from_semantics(evidence, silhouette)
        foreground = alpha > 0.0
        labels, _ = map_palette(
            evidence.lab,
            alpha,
            palette,
            strokes,
            detail if not outline else None,
        )
        labels = clean_label_clusters(labels, palette.lab, foreground, tone.protect)
        if temporal_mode == "flow" and previous_labels is not None and previous_evidence is not None:
            labels = stabilize_label_step(previous_labels, previous_evidence, labels, evidence, foreground, palette)
        output = labels_to_rgba(labels, palette, alpha)
        path = output_dir / f"frame-{index:04d}.npy"
        np.save(path, output, allow_pickle=False)
        paths.append(path)
        previous_labels = labels
        previous_evidence = evidence
        if progress:
            progress("连续像素化", index + 1, count)
    return tuple(paths), {
        "palette_actual": len(palette.srgb),
        "palette_inertia": float(palette.inertia),
        "logical_frames": count,
    }


def _sequence_independent(
    source_paths: list[Path],
    target: TargetGrid,
    profile: str,
    outline: bool,
    outline_color: str,
    output_dir: Path,
    progress: Progress | None,
) -> tuple[tuple[Path, ...], list[object]]:
    paths: list[Path] = []
    transforms: list[object] = []
    count = len(source_paths)
    for index, source_path in enumerate(source_paths):
        rgba = np.load(source_path, mmap_mode="r", allow_pickle=False)
        result = compile_continuous(
            [np.asarray(rgba)],
            [None],
            target,
            profile,
            outline,
            outline_color,
            sequence_mode="continuous",
        )
        path = output_dir / f"frame-{index:04d}.npy"
        np.save(path, result.frames[0], allow_pickle=False)
        paths.append(path)
        transforms.append(result.transform)
        if progress:
            progress("逐帧像素化", index + 1, count)
    return tuple(paths), transforms


def compile_sequence(
    frame_factory: Callable[[], Iterator[np.ndarray]],
    source_frames: list[dict[str, object]],
    target: TargetGrid,
    *,
    profile: str,
    outline: bool,
    outline_color: str,
    temporal_mode: str,
    progress: Progress | None = None,
) -> PixelSequenceResult:
    """Compile up to 240 equal-canvas frames without high-res batch buildup."""

    if not 1 <= len(source_frames) <= 240:
        raise ValueError("long sequence pixelization accepts 1 to 240 frames")
    started = time.perf_counter()
    temporary = tempfile.TemporaryDirectory(prefix="cooksprite-pixel-sequence-")
    root = Path(temporary.name)
    source_dir = root / "source"
    output_dir = root / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    source_paths: list[Path] = []
    boxes: list[tuple[int, int, int, int]] = []
    canvas: tuple[int, int] | None = None
    for index, raw in enumerate(frame_factory()):
        if index >= 240:
            temporary.cleanup()
            raise ValueError("long sequence pixelization accepts at most 240 frames")
        rgba = _normalise_rgba(raw)
        height, width = rgba.shape[:2]
        current_canvas = (width, height)
        if canvas is None:
            canvas = current_canvas
        elif canvas != current_canvas:
            temporary.cleanup()
            raise ValueError("all FrameSeq frames must use the same canvas")
        if index >= len(source_frames):
            temporary.cleanup()
            raise ValueError("FrameSeq reader returned more frames than its manifest")
        declared = source_frames[index].get("canvas")
        if isinstance(declared, (tuple, list)) and len(declared) == 2 and tuple(int(item) for item in declared) != current_canvas:
            temporary.cleanup()
            raise ValueError("FrameSeq manifest canvas does not match an uploaded frame")
        # Older FrameSeq manifests predate dimensions.  We discover this once
        # at the compute boundary and write it into the immutable Plan, rather
        # than teaching the API to decode image pixels.
        source_frames[index]["canvas"] = current_canvas
        try:
            boxes.append(foreground_bbox(rgba[..., 3].astype(np.float32) / 255.0))
        except ValueError as exc:
            temporary.cleanup()
            raise ValueError(f"FrameSeq frame {index + 1} has empty foreground Alpha") from exc
        source_path = source_dir / f"frame-{index:04d}.npy"
        np.save(source_path, rgba, allow_pickle=False)
        source_paths.append(source_path)
    if len(source_paths) != len(source_frames):
        temporary.cleanup()
        raise ValueError("FrameSeq reader did not return every manifest frame")
    assert canvas is not None

    if temporal_mode == "independent":
        paths, transforms = _sequence_independent(
            source_paths, target, profile, outline, outline_color, output_dir, progress
        )
        plan = plan_payload(
            frames=source_frames,
            canvas=canvas,
            transforms=transforms,
            target=target,
            supersample=_supersample(target, 1),
            temporal_mode="independent",
            profile=profile,
            outline=outline,
            outline_color=outline_color,
        )
        metrics: dict[str, object] = {"frame_count": len(paths), "temporal_mode": temporal_mode}
    else:
        transform = build_transform(_union_box(boxes), target)
        supersample = _supersample(target, len(source_paths))
        paths, shared_metrics = _sequence_shared(
            source_paths,
            target,
            profile,
            outline,
            outline_color,
            temporal_mode,
            transform,
            supersample,
            output_dir,
            progress,
        )
        plan = plan_payload(
            frames=source_frames,
            canvas=canvas,
            transform=transform,
            target=target,
            supersample=supersample,
            temporal_mode=temporal_mode,
            profile=profile,
            outline=outline,
            outline_color=outline_color,
        )
        metrics = {"frame_count": len(paths), "temporal_mode": temporal_mode, **shared_metrics}
    metrics["wall_seconds"] = time.perf_counter() - started
    return PixelSequenceResult(temporary, paths, PixelGeometryPlanValue(plan), metrics)


__all__ = ["PixelSequenceResult", "compile_sequence", "resolve_temporal_mode"]
