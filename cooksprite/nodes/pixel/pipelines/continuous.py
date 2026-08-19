"""Continuous high-resolution artwork to deliberate logical pixels."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any

import cv2
import numpy as np

from ..geometry import GeometryTransform, build_transform, geometry_metrics, union_bbox
from ..stages.contour import (
    InternalStructureStroke,
    SilhouetteContour,
    compile_internal_strokes,
    compile_silhouette,
)
from ..stages.evidence import CellEvidence, FrameEvidence, analyse_frame, compile_cell_evidence
from ..stages.palette import (
    PaletteBuildResult,
    build_palette,
    clean_label_clusters,
    labels_to_rgba,
    map_palette,
)
from ..stages.tones import ToneRole, ToneRoleMap, extract_tone_roles
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


def _selective_outer_stroke(evidence: CellEvidence, silhouette: np.ndarray) -> np.ndarray:
    eroded = cv2.erode(silhouette.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    boundary = silhouette & ~eroded
    dark_limit = float(np.quantile(evidence.lab[..., 0][silhouette], 0.42)) if np.any(silhouette) else 0.0
    return boundary & (
        ((evidence.edge >= 0.43) & (evidence.source_dark >= 0.08))
        | ((evidence.feature >= 0.84) & (evidence.lab[..., 0] <= dark_limit))
    )


def _micro_detail_mask(evidence: CellEvidence, tones: ToneRoleMap, silhouette: np.ndarray) -> np.ndarray:
    """Return interior highlights that must win over contour painting.

    Dark compact ink is intentional sprite structure: eyes, mouths, buckles
    and seams should keep the black contour treatment.  Only bright material
    details bypass the forced outline, and only away from the outside
    silhouette.  This keeps the readable black edge while preserving silver
    glints and other small highlights.
    """

    eroded = cv2.erode(silhouette.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    interior = silhouette & eroded
    highlight = tones.highlight_mask & (evidence.feature >= 0.20)
    return interior & highlight


def _role_adjusted_evidence(evidence: CellEvidence, tones: ToneRoleMap) -> CellEvidence:
    lab = evidence.lab.copy()
    roles = tones.roles
    highlight = np.isin(roles, (int(ToneRole.SPECULAR), int(ToneRole.RIM_HIGHLIGHT), int(ToneRole.EMISSION)))
    deep = np.isin(roles, (int(ToneRole.OUTLINE), int(ToneRole.DEEP_SHADOW)))
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
        previous_l = evidences[index - 1].lab[..., 0].astype(np.float32)
        current_l = evidences[index].lab[..., 0].astype(np.float32)
        flow_buffer = np.empty((*previous_l.shape, 2), dtype=np.float32)
        flow = cv2.calcOpticalFlowFarneback(previous_l, current_l, flow_buffer, 0.5, 2, 9, 2, 5, 1.1, 0)
        height, width = current_l.shape
        xx, yy = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
        warped = cv2.remap(output[-1].astype(np.float32), xx - flow[..., 0], yy - flow[..., 1], cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=-1).astype(np.int16)
        magnitude = np.linalg.norm(flow, axis=2)
        current = labels[index].copy()
        valid = (warped >= 0) & foregrounds[index] & (magnitude <= 1.20)
        if np.any(valid):
            warped_lab = np.zeros_like(evidences[index].lab)
            warped_lab[valid] = palette.lab[warped[valid]]
            color_delta = np.linalg.norm(warped_lab - evidences[index].lab, axis=2)
            inherit = valid & (color_delta <= 0.028) & (evidences[index].edge <= 0.35)
            current[inherit] = warped[inherit]
        output.append(current)
    return output


def compile_continuous(
    rgba_frames: list[np.ndarray],
    semantic_mask_paths: list[str | None],
    target: TargetGrid,
    profile: str = "production",
) -> CompiledSequence:
    started = time.perf_counter()
    alphas = [frame[..., 3].astype(np.float32) / 255.0 for frame in rgba_frames]
    bbox = union_bbox(alphas)
    transform = build_transform(bbox, target)
    supersample = _supersample(target, len(rgba_frames))
    analyses: list[FrameEvidence] = []
    cells: list[CellEvidence] = []
    silhouettes: list[SilhouetteContour] = []
    internal_strokes: list[InternalStructureStroke] = []
    tone_maps: list[ToneRoleMap] = []
    adjusted: list[CellEvidence] = []
    stroke_masks: list[np.ndarray] = []
    detail_masks: list[np.ndarray] = []
    for rgba, semantic_path in zip(rgba_frames, semantic_mask_paths, strict=True):
        semantic = _semantic_mask(semantic_path, rgba.shape[:2])
        analysis = analyse_frame(rgba, transform, semantic, supersample)
        evidence = compile_cell_evidence(analysis, target.width, target.height)
        silhouette = compile_silhouette(evidence)
        internal = compile_internal_strokes(evidence, silhouette.mask)
        strokes = internal.mask | _selective_outer_stroke(evidence, silhouette.mask)
        tones = extract_tone_roles(evidence, silhouette.mask, strokes)
        detail = _micro_detail_mask(evidence, tones, silhouette.mask)
        analyses.append(analysis)
        cells.append(evidence)
        silhouettes.append(silhouette)
        internal_strokes.append(internal)
        tone_maps.append(tones)
        adjusted.append(_role_adjusted_evidence(evidence, tones))
        # Keep the complete contour in diagnostics.  ``map_palette`` applies
        # the narrow interior-highlight exception when it assigns labels.
        stroke_masks.append(strokes)
        detail_masks.append(detail)
    budget = target.resolved_palette_budget
    if profile == "fidelity":
        budget = min(256, max(budget, round(budget * 1.20)))
    elif profile == "graphic":
        budget = max(8, round(budget * 0.72))
    palette = build_palette(adjusted, tone_maps, [item.mask for item in silhouettes], budget)
    labels: list[np.ndarray] = []
    alphas_out: list[np.ndarray] = []
    foregrounds: list[np.ndarray] = []
    for evidence, tones, silhouette, strokes, detail in zip(
        adjusted, tone_maps, silhouettes, stroke_masks, detail_masks, strict=True
    ):
        alpha = _alpha_from_semantics(evidence, silhouette.mask)
        foreground = alpha > 0.0
        mapped, _ = map_palette(evidence.lab, alpha, palette, strokes, detail)
        mapped = clean_label_clusters(mapped, palette.lab, foreground, tones.protect)
        labels.append(mapped)
        alphas_out.append(alpha)
        foregrounds.append(foreground)
    labels = _stabilize_labels(labels, adjusted, foregrounds, palette)
    output_frames = tuple(labels_to_rgba(label, palette, alpha) for label, alpha in zip(labels, alphas_out, strict=True))
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
        "frame_count": len(output_frames),
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
    return CompiledSequence(output_frames, palette, transform, diagnostics, metrics, profile)
