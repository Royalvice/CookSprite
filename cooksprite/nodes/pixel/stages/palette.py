"""Deterministic role-aware OKLab palette construction and mapping."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..color import oklab_to_srgb, srgb_to_oklab
from .evidence import CellEvidence
from .tones import ToneRole, ToneRoleMap


@dataclass(frozen=True)
class PaletteBuildResult:
    srgb: np.ndarray
    lab: np.ndarray
    outline_index: int
    fixed_count: int
    inertia: float
    receipt: dict[str, object]


def _medoid(values: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    if len(values) == 1:
        return values[0]
    if weights is None:
        weights = np.ones(len(values), dtype=np.float64)
    center = np.average(values, axis=0, weights=np.maximum(weights, 1e-9))
    return values[int(np.argmin(np.sum((values - center) ** 2, axis=1)))]


def _initial_centers(values: np.ndarray, weights: np.ndarray, count: int, fixed: np.ndarray) -> np.ndarray:
    centers = [item.copy() for item in fixed]
    if not centers:
        centers.append(values[int(np.argmax(weights))].copy())
    existing = np.stack(centers)
    min_distance = np.min(np.sum((values[:, None, :] - existing[None, :, :]) ** 2, axis=2), axis=1)
    while len(centers) < count:
        center = values[int(np.argmax(min_distance * weights))].copy()
        centers.append(center)
        distance = np.sum((values - center) ** 2, axis=1)
        min_distance = np.minimum(min_distance, distance)
    return np.stack(centers[:count]).astype(np.float32)


def _nearest(values: np.ndarray, centers: np.ndarray, block_size: int = 65_536) -> tuple[np.ndarray, np.ndarray]:
    labels = np.empty(len(values), dtype=np.int32)
    minimum = np.empty(len(values), dtype=np.float32)
    for start in range(0, len(values), block_size):
        stop = min(len(values), start + block_size)
        distance = np.sum((values[start:stop, None, :] - centers[None, :, :]) ** 2, axis=2)
        block_labels = np.argmin(distance, axis=1)
        labels[start:stop] = block_labels
        minimum[start:stop] = distance[np.arange(stop - start), block_labels]
    return labels, minimum


def _weighted_kmeans(values: np.ndarray, weights: np.ndarray, count: int, fixed: np.ndarray) -> tuple[np.ndarray, float]:
    centers = _initial_centers(values, weights, count, fixed)
    fixed_count = len(fixed)
    positive_weights = np.maximum(weights, 1e-9)
    for _ in range(32):
        labels, _ = _nearest(values, centers)
        updated = centers.copy()
        totals = np.bincount(labels, weights=positive_weights, minlength=count)
        channels = values.shape[1]
        offsets = np.arange(channels, dtype=labels.dtype)[None, :] * count
        weighted_values = np.bincount(
            (labels[:, None] + offsets).ravel(),
            weights=(values * positive_weights[:, None]).ravel(),
            minlength=count * channels,
        ).reshape(channels, count).T
        movable = totals[fixed_count:] > 0.0
        movable_indices = np.flatnonzero(movable) + fixed_count
        updated[movable_indices] = weighted_values[movable_indices] / totals[movable_indices, None]
        if float(np.max(np.abs(updated - centers))) < 1e-6:
            centers = updated
            break
        centers = updated
    distance = np.sum((values - centers[labels]) ** 2, axis=1)
    inertia = float(np.average(distance, weights=positive_weights))
    return centers, inertia


def _weighted_kmeans_legacy(
    values: np.ndarray,
    weights: np.ndarray,
    count: int,
    fixed: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Retain the original numerical path for explicit continuous mode."""

    centers = [item.copy() for item in fixed]
    if not centers:
        centers.append(values[int(np.argmax(weights))].copy())
    while len(centers) < count:
        existing = np.stack(centers)
        distance = np.min(
            np.sum((values[:, None, :] - existing[None, :, :]) ** 2, axis=2), axis=1
        )
        centers.append(values[int(np.argmax(distance * weights))].copy())
    centers_array = np.stack(centers[:count]).astype(np.float32)
    fixed_count = len(fixed)
    positive_weights = np.maximum(weights, 1e-9)
    for _ in range(32):
        distance = np.sum((values[:, None, :] - centers_array[None, :, :]) ** 2, axis=2)
        labels = np.argmin(distance, axis=1)
        updated = centers_array.copy()
        totals = np.bincount(labels, weights=positive_weights, minlength=count)
        channels = values.shape[1]
        offsets = np.arange(channels, dtype=labels.dtype)[None, :] * count
        weighted_values = np.bincount(
            (labels[:, None] + offsets).ravel(),
            weights=(values * positive_weights[:, None]).ravel(),
            minlength=count * channels,
        ).reshape(channels, count).T
        movable = totals[fixed_count:] > 0.0
        movable_indices = np.flatnonzero(movable) + fixed_count
        updated[movable_indices] = weighted_values[movable_indices] / totals[movable_indices, None]
        if float(np.max(np.abs(updated - centers_array))) < 1e-6:
            centers_array = updated
            break
        centers_array = updated
    distance = np.sum((values - centers_array[labels]) ** 2, axis=1)
    return centers_array, float(np.average(distance, weights=positive_weights))


def build_palette(
    evidences: list[CellEvidence],
    tone_maps: list[ToneRoleMap],
    silhouettes: list[np.ndarray],
    budget: int,
    *,
    preserve_highlights: bool = False,
    outline_color: str | None = None,
    equal_frame_weight: bool = False,
    canonical_order: bool = False,
    exact_legacy: bool = False,
) -> PaletteBuildResult:
    values: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    roles: list[np.ndarray] = []
    highlights: list[np.ndarray] = []
    highlight_weights: list[np.ndarray] = []
    for evidence, tones, silhouette in zip(evidences, tone_maps, silhouettes, strict=True):
        values.append(evidence.lab[silhouette])
        role_values = tones.roles[silhouette]
        role_weight = np.ones(len(role_values), dtype=np.float64)
        role_weight[np.isin(role_values, (int(ToneRole.OUTLINE), int(ToneRole.DEEP_SHADOW)))] *= 2.4
        role_weight[np.isin(role_values, (int(ToneRole.SPECULAR), int(ToneRole.RIM_HIGHLIGHT), int(ToneRole.EMISSION)))] *= 3.2
        role_weight *= 1.0 + evidence.feature[silhouette] * 1.25
        if equal_frame_weight:
            role_weight /= max(float(np.sum(role_weight)), 1e-9)
        weights.append(role_weight)
        roles.append(role_values)
        if preserve_highlights:
            highlight = tones.highlight_mask & silhouette
            if np.any(highlight):
                highlights.append(evidence.lab[highlight])
                highlight_weights.append((1.0 + evidence.feature[highlight] * 1.25).astype(np.float64))
    all_values = np.concatenate(values).astype(np.float32)
    all_weights = np.concatenate(weights).astype(np.float64)
    all_roles = np.concatenate(roles)
    if canonical_order:
        order = np.lexsort((all_values[:, 2], all_values[:, 1], all_values[:, 0], all_roles))
        all_values = all_values[order]
        all_weights = all_weights[order]
        all_roles = all_roles[order]
    fixed_values: list[np.ndarray] = []
    outline_values = all_values[np.isin(all_roles, (int(ToneRole.OUTLINE), int(ToneRole.DEEP_SHADOW)))]
    if outline_color is not None:
        from ..color import hex_to_srgb

        outline = srgb_to_oklab(hex_to_srgb(outline_color)[None, :])[0]
    elif outline_values.size:
        outline = outline_values[int(np.argmin(outline_values[:, 0]))]
    else:
        outline = all_values[int(np.argmin(all_values[:, 0]))]
    fixed_values.append(outline)
    for role in (ToneRole.EMISSION, ToneRole.SPECULAR, ToneRole.RIM_HIGHLIGHT):
        selected = all_roles == int(role)
        if np.count_nonzero(selected) >= 1 and len(fixed_values) < min(5, budget):
            fixed_values.append(_medoid(all_values[selected], all_weights[selected]))
    if preserve_highlights and highlights and len(fixed_values) < min(5, budget):
        fixed_values.append(_medoid(np.concatenate(highlights), np.concatenate(highlight_weights)))
    fixed = np.stack(fixed_values).astype(np.float32)
    cluster = _weighted_kmeans_legacy if exact_legacy else _weighted_kmeans
    centers, inertia = cluster(all_values, all_weights, budget, fixed)
    srgb = oklab_to_srgb(centers)
    srgb_u8 = np.rint(np.clip(srgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    if outline_color is not None:
        from ..color import hex_to_srgb

        srgb_u8[0] = np.rint(hex_to_srgb(outline_color) * 255.0).astype(np.uint8)
    # Quantized duplicates are removed deterministically. The hard contract is
    # a maximum budget, not a requirement to invent exactly N colours.
    unique: list[np.ndarray] = []
    for item in srgb_u8:
        if not any(np.array_equal(item, existing) for existing in unique):
            unique.append(item)
    srgb_final = np.stack(unique).astype(np.float32) / 255.0
    lab_final = srgb_to_oklab(srgb_final)
    return PaletteBuildResult(
        srgb_final,
        lab_final,
        0,
        len(fixed_values),
        inertia,
        {
            "method": "deterministic_role_weighted_oklab_kmeans",
            "requested_budget": budget,
            "actual_colors": len(srgb_final),
            "fixed_role_colors": len(fixed_values),
            "preserve_highlights": preserve_highlights,
            "outline_color": outline_color,
            "inertia": inertia,
        },
    )


def map_palette(
    cell_lab: np.ndarray,
    alpha: np.ndarray,
    palette: PaletteBuildResult,
    strokes: np.ndarray,
    detail_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Map cells to the palette while protecting compact source details."""
    shape = cell_lab.shape[:2]
    labels, minimum = _nearest(cell_lab.reshape(-1, 3), palette.lab)
    labels = labels.reshape(shape).astype(np.int16)
    labels[alpha <= 0.0] = -1
    if detail_mask is None:
        detail_mask = np.zeros_like(strokes, dtype=bool)
    labels[strokes & ~detail_mask & (alpha > 0.0)] = palette.outline_index
    return labels, minimum.reshape(shape)


def clean_label_clusters(labels: np.ndarray, palette_lab: np.ndarray, foreground: np.ndarray, protect: np.ndarray) -> np.ndarray:
    output = labels.copy()
    for label in range(len(palette_lab)):
        count, components, stats, _ = cv2.connectedComponentsWithStats((foreground & (output == label)).astype(np.uint8), connectivity=8)
        for component_index in range(1, count):
            component = components == component_index
            area = int(stats[component_index, cv2.CC_STAT_AREA])
            if area >= 2 or np.any(component & protect):
                continue
            ring = cv2.dilate(component.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool) & foreground & ~component
            candidates = output[ring]
            candidates = candidates[candidates >= 0]
            if not candidates.size:
                continue
            unique, counts = np.unique(candidates, return_counts=True)
            color_distance = np.linalg.norm(palette_lab[unique] - palette_lab[label], axis=1)
            replacement = int(unique[np.argmin(color_distance - counts / counts.max() * 0.018)])
            output[component] = replacement
    return output


def labels_to_rgba(labels: np.ndarray, palette: PaletteBuildResult, alpha: np.ndarray) -> np.ndarray:
    output = np.zeros((*labels.shape, 4), dtype=np.uint8)
    foreground = labels >= 0
    output[foreground, :3] = np.rint(palette.srgb[labels[foreground]] * 255.0).astype(np.uint8)
    output[..., 3] = np.rint(np.clip(alpha, 0.0, 1.0) * 255.0).astype(np.uint8)
    output[output[..., 3] == 0, :3] = 0
    return output
