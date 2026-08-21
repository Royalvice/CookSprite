"""Continuous high-resolution artwork to deliberate logical pixels."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

import cv2
import numpy as np

from ..geometry import (
    GeometryTransform,
    build_transform,
    geometry_metrics,
    render_normal_supersampled,
    union_bbox,
)
from ..stages.contour import (
    InternalStructureStroke,
    SilhouetteContour,
    compile_internal_strokes,
    compile_silhouette,
)
from ..stages.evidence import CellEvidence, analyse_frame, compile_cell_evidence
from ..stages.normals import normals_to_rgb, reduce_normal_cells
from ..stages.palette import (
    PaletteBuildResult,
    build_palette,
    clean_label_clusters,
    labels_to_rgba,
    map_palette,
)
from ..stages.tones import ToneRole, ToneRoleMap, extract_chunk_tone_roles, extract_tone_roles
from ..types import TargetGrid


@dataclass(frozen=True)
class CompiledFrameDiagnostics:
    coverage: np.ndarray
    silhouette: np.ndarray
    strokes: np.ndarray
    highlights: np.ndarray
    roles: np.ndarray
    feature: np.ndarray
    interior_ink: np.ndarray
    protect: np.ndarray
    thin_support: np.ndarray


@dataclass(frozen=True)
class CompiledSequence:
    frames: tuple[np.ndarray, ...]
    palette: PaletteBuildResult
    transform: GeometryTransform
    diagnostics: tuple[CompiledFrameDiagnostics, ...]
    metrics: dict[str, Any]
    profile: str
    normals: tuple[np.ndarray, ...] | None = None


def _semantic_mask(record_path: str | None, shape: tuple[int, int]) -> np.ndarray | None:
    if record_path is None:
        return None
    from PIL import Image

    value = np.asarray(Image.open(record_path).convert("L"), dtype=np.uint8) > 0
    if value.shape != shape:
        raise ValueError("semantic transparency mask canvas mismatch")
    return value


def _supersample(target: TargetGrid, frame_count: int) -> int:
    # Single masters receive the full detail budget. Sequences already share
    # geometry and palette, so a common 7x analysis preserves temporal detail
    # while keeping the locked eight-frame end-to-end latency deterministic.
    quality_limit = 8 if frame_count == 1 else 7
    return max(1, min(quality_limit, 768 // max(target.width, target.height)))


def _selective_outer_stroke(
    evidence: CellEvidence,
    silhouette: np.ndarray,
    dark_limit: float | None = None,
) -> np.ndarray:
    eroded = cv2.erode(silhouette.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    boundary = silhouette & ~eroded
    if dark_limit is None:
        dark_limit = float(np.quantile(evidence.lab[..., 0][silhouette], 0.42)) if np.any(silhouette) else 0.0
    return boundary & (
        ((evidence.edge >= 0.43) & (evidence.source_dark >= 0.08))
        | ((evidence.feature >= 0.84) & (evidence.lab[..., 0] <= dark_limit))
    )


def _micro_detail_mask(evidence: CellEvidence, tones: ToneRoleMap, silhouette: np.ndarray) -> np.ndarray:
    """Keep bright interior cells from being overwritten by the outline pass."""

    eroded = cv2.erode(silhouette.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    interior = silhouette & eroded
    highlight = tones.highlight_mask & (evidence.feature >= 0.20)
    return interior & highlight


def _role_adjusted_evidence(
    evidence: CellEvidence,
    tones: ToneRoleMap,
    *,
    preserve_highlights: bool = False,
) -> CellEvidence:
    lab = evidence.lab.copy()
    roles = tones.roles
    highlight = np.isin(roles, (int(ToneRole.SPECULAR), int(ToneRole.RIM_HIGHLIGHT), int(ToneRole.EMISSION)))
    deep = np.isin(roles, (int(ToneRole.OUTLINE), int(ToneRole.DEEP_SHADOW)))
    if preserve_highlights:
        deep &= ~tones.highlight_mask
    lab[..., 0][highlight] = np.clip(lab[..., 0][highlight] + evidence.highlight[highlight] * 0.065, 0.0, 1.0)
    lab[..., 0][deep] = np.clip(lab[..., 0][deep] - evidence.edge[deep] * 0.035, 0.0, 1.0)
    return replace(evidence, lab=lab)


def _alpha_from_semantics(evidence: CellEvidence, silhouette: np.ndarray) -> np.ndarray:
    alpha = silhouette.astype(np.float32)
    semantic = evidence.semantic_coverage >= 0.02
    if np.any(semantic):
        quantized = np.zeros_like(alpha)
        quantized[(evidence.semantic_coverage > 0.0) & (evidence.semantic_coverage < 0.42)] = 1.0 / 3.0
        quantized[(evidence.semantic_coverage >= 0.42) & (evidence.semantic_coverage < 0.78)] = 2.0 / 3.0
        quantized[evidence.semantic_coverage >= 0.78] = 1.0
        alpha[semantic] = np.maximum(alpha[semantic], quantized[semantic])
    return alpha


def _stabilize_labels(
    labels: list[np.ndarray],
    evidences: list[CellEvidence],
    foregrounds: list[np.ndarray],
    palette: PaletteBuildResult,
) -> list[np.ndarray]:
    if len(labels) < 2:
        return labels
    output = [labels[0].copy()]
    for index in range(1, len(labels)):
        output.append(
            stabilize_label_step(
                output[-1],
                evidences[index - 1],
                labels[index],
                evidences[index],
                foregrounds[index],
                palette,
            )
        )
    return output


def stabilize_label_step(
    previous_labels: np.ndarray,
    previous_evidence: CellEvidence,
    labels: np.ndarray,
    evidence: CellEvidence,
    foreground: np.ndarray,
    palette: PaletteBuildResult,
) -> np.ndarray:
    """Apply the legacy Farneback inheritance gate to one next frame.

    Keeping this public helper lets the long sequence compiler keep only the
    previous logical frame in RAM while preserving the exact continuous-mode
    optical-flow operation and its color/edge/foreground protection gates.
    """

    previous_l = previous_evidence.lab[..., 0].astype(np.float32)
    current_l = evidence.lab[..., 0].astype(np.float32)
    flow_buffer = np.empty((*previous_l.shape, 2), dtype=np.float32)
    flow = cv2.calcOpticalFlowFarneback(previous_l, current_l, flow_buffer, 0.5, 2, 9, 2, 5, 1.1, 0)
    height, width = current_l.shape
    xx, yy = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    warped = cv2.remap(
        previous_labels.astype(np.float32),
        xx - flow[..., 0],
        yy - flow[..., 1],
        cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=-1,
    ).astype(np.int16)
    magnitude = np.linalg.norm(flow, axis=2)
    current = labels.copy()
    valid = (warped >= 0) & foreground & (magnitude <= 1.20)
    if np.any(valid):
        warped_lab = np.zeros_like(evidence.lab)
        warped_lab[valid] = palette.lab[warped[valid]]
        color_delta = np.linalg.norm(warped_lab - evidence.lab, axis=2)
        inherit = valid & (color_delta <= 0.028) & (evidence.edge <= 0.35)
        current[inherit] = warped[inherit]
    return current


def compile_continuous(
    rgba_frames: list[np.ndarray],
    semantic_mask_paths: list[str | None],
    target: TargetGrid,
    profile: str = "production",
    outline: bool = True,
    outline_color: str = "#000000",
    *,
    normal_frames: list[tuple[np.ndarray, np.ndarray]] | None = None,
    sequence_mode: str = "auto",
) -> CompiledSequence:
    started = time.perf_counter()
    if sequence_mode not in {"auto", "chunk", "continuous"}:
        raise ValueError(f"unsupported sequence mode: {sequence_mode}")
    resolved_mode = "chunk" if sequence_mode == "auto" and len(rgba_frames) > 1 else (
        "continuous" if sequence_mode == "auto" else sequence_mode
    )
    if resolved_mode == "chunk" and len(rgba_frames) > 32:
        raise ValueError("chunk pixelization accepts at most 32 frames")
    if normal_frames is not None and len(normal_frames) != len(rgba_frames):
        raise ValueError("normal batch must match diffuse batch")
    bbox = union_bbox([frame[..., 3] for frame in rgba_frames], threshold=1.0)
    transform = build_transform(bbox, target)
    supersample = _supersample(target, len(rgba_frames))
    def compile_frame(index: int) -> tuple[CellEvidence, SilhouetteContour, InternalStructureStroke, np.ndarray | None]:
        rgba = rgba_frames[index]
        semantic_path = semantic_mask_paths[index]
        semantic = _semantic_mask(semantic_path, rgba.shape[:2])
        analysis = analyse_frame(
            rgba,
            transform,
            semantic,
            supersample,
            fast_regions=resolved_mode == "chunk",
        )
        if normal_frames is None:
            evidence = compile_cell_evidence(analysis, target.width, target.height)
            cell_normal = None
        else:
            evidence, sampling = compile_cell_evidence(
                analysis,
                target.width,
                target.height,
                include_sampling=True,
            )
            normal, normal_alpha = normal_frames[index]
            rendered_normal = render_normal_supersampled(normal, normal_alpha, transform, supersample)
            cell_normal = reduce_normal_cells(rendered_normal, sampling, target.width, target.height)
        silhouette = compile_silhouette(evidence)
        internal = compile_internal_strokes(evidence, silhouette.mask)
        return evidence, silhouette, internal, cell_normal

    worker_limit = (
        1 if resolved_mode == "continuous" else 2 if len(rgba_frames) > 8 else 4
    )
    workers = min(len(rgba_frames), worker_limit, max(1, (os.cpu_count() or 2) // 2))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cooksprite-pixel") as executor:
            compiled = list(executor.map(compile_frame, range(len(rgba_frames))))
    else:
        compiled = [compile_frame(index) for index in range(len(rgba_frames))]
    evidences = [item[0] for item in compiled]
    silhouettes = [item[1] for item in compiled]
    internal_strokes = [item[2] for item in compiled]
    cell_normals = [item[3] for item in compiled] if normal_frames is not None else None
    shared_dark_limit = None
    if resolved_mode == "chunk":
        dark_values = [evidence.lab[..., 0][silhouette.mask] for evidence, silhouette in zip(evidences, silhouettes, strict=True)]
        populated = [value for value in dark_values if value.size]
        if populated:
            shared_dark_limit = float(np.quantile(np.concatenate(populated), 0.42))
    stroke_masks = [
        internal.mask | _selective_outer_stroke(evidence, silhouette.mask, shared_dark_limit)
        for evidence, silhouette, internal in zip(evidences, silhouettes, internal_strokes, strict=True)
    ]
    tone_maps = (
        extract_chunk_tone_roles(evidences, [item.mask for item in silhouettes], stroke_masks)
        if resolved_mode == "chunk"
        else [
            extract_tone_roles(evidence, silhouette.mask, strokes)
            for evidence, silhouette, strokes in zip(evidences, silhouettes, stroke_masks, strict=True)
        ]
    )
    detail_masks = [
        _micro_detail_mask(evidence, tones, silhouette.mask) if not outline else np.zeros_like(strokes)
        for evidence, tones, silhouette, strokes in zip(evidences, tone_maps, silhouettes, stroke_masks, strict=True)
    ]
    adjusted = [
        _role_adjusted_evidence(evidence, tones, preserve_highlights=not outline)
        for evidence, tones in zip(evidences, tone_maps, strict=True)
    ]
    budget = target.resolved_palette_budget
    if profile == "fidelity":
        budget = min(256, max(budget, round(budget * 1.20)))
    elif profile == "graphic":
        budget = max(8, round(budget * 0.72))
    palette = build_palette(
        adjusted,
        tone_maps,
        [item.mask for item in silhouettes],
        budget,
        preserve_highlights=not outline,
        outline_color=outline_color if outline else None,
        equal_frame_weight=resolved_mode == "chunk",
        canonical_order=resolved_mode == "chunk",
        exact_legacy=resolved_mode == "continuous",
    )
    labels: list[np.ndarray] = []
    alphas_out: list[np.ndarray] = []
    foregrounds: list[np.ndarray] = []
    for evidence, tones, silhouette, strokes, detail in zip(
        adjusted, tone_maps, silhouettes, stroke_masks, detail_masks, strict=True
    ):
        alpha = _alpha_from_semantics(evidence, silhouette.mask)
        foreground = alpha > 0.0
        mapped, _ = map_palette(
            evidence.lab,
            alpha,
            palette,
            strokes,
            detail if not outline else None,
            exact_legacy=resolved_mode == "continuous",
        )
        mapped = clean_label_clusters(mapped, palette.lab, foreground, tones.protect)
        labels.append(mapped)
        alphas_out.append(alpha)
        foregrounds.append(foreground)
    if resolved_mode == "continuous":
        labels = _stabilize_labels(labels, adjusted, foregrounds, palette)
    output_frames = tuple(labels_to_rgba(label, palette, alpha) for label, alpha in zip(labels, alphas_out, strict=True))
    output_normals = None
    if cell_normals is not None:
        output_normals = tuple(
            normals_to_rgb(normal, alpha)
            for normal, alpha in zip(cell_normals, alphas_out, strict=True)
            if normal is not None
        )
    diagnostics = tuple(
        CompiledFrameDiagnostics(
            evidence.coverage,
            silhouette.mask,
            strokes,
            tones.highlight_mask,
            tones.roles,
            evidence.feature,
            evidence.ink_coverage,
            evidence.protect,
            evidence.thin_support,
        )
        for evidence, silhouette, strokes, tones in zip(adjusted, silhouettes, stroke_masks, tone_maps, strict=True)
    )
    geometry = [geometry_metrics(frame[..., 3].astype(np.float32) / 255.0, target) for frame in output_frames]
    metrics = {
        "profile": profile,
        "outline": outline,
        "outline_color": outline_color if outline else None,
        "frame_count": len(output_frames),
        "sequence_mode": resolved_mode,
        "workers": workers,
        "supersample": supersample,
        "palette_budget": target.resolved_palette_budget,
        "palette_actual": len(palette.srgb),
        "transform": transform.as_dict(),
        "geometry": geometry,
        "contours": [
            {
                "source_energy": item.source_energy,
                "components": item.component_count,
                "holes": item.hole_count,
                "dangling_cells_removed": item.dangling_cells_removed,
                "thin_cells_restored": item.thin_cells_restored,
                "irregularity": item.irregularity,
            }
            for item in silhouettes
        ],
        "internal_strokes": [{"components": item.component_count, "cells": item.cell_count} for item in internal_strokes],
        "detail_evidence": [
            {
                "interior_ink_cells": int(np.count_nonzero(item.ink_coverage >= 0.10)),
                "protected_cells": int(np.count_nonzero(item.protect)),
                "contour_protected_cells": int(np.count_nonzero(item.contour_protect)),
                "thin_support_cells": int(np.count_nonzero(item.thin_support)),
            }
            for item in adjusted
        ],
        "tone_role_counts": [item.counts for item in tone_maps],
        "wall_seconds": time.perf_counter() - started,
    }
    return CompiledSequence(output_frames, palette, transform, diagnostics, metrics, profile, output_normals)
