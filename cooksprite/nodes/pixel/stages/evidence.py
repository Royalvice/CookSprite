"""High-resolution structural evidence and region-first cell sampling."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy import ndimage  # type: ignore[import-untyped]
from skimage.morphology import skeletonize
from skimage.segmentation import slic

from ..color import srgb_to_oklab
from ..geometry import GeometryTransform, render_supersampled, transform_mask


@dataclass(frozen=True)
class FrameEvidence:
    rgba: np.ndarray
    alpha: np.ndarray
    lab: np.ndarray
    edge: np.ndarray
    feature: np.ndarray
    luminance_residual: np.ndarray
    interior_ink: np.ndarray
    regions: np.ndarray
    protect: np.ndarray
    contour_protect: np.ndarray
    thin_support: np.ndarray
    semantic_transparency: np.ndarray
    signed_distance: np.ndarray
    supersample: int


@dataclass(frozen=True)
class CellEvidence:
    coverage: np.ndarray
    semantic_coverage: np.ndarray
    lab: np.ndarray
    edge: np.ndarray
    feature: np.ndarray
    highlight: np.ndarray
    source_dark: np.ndarray
    ink_coverage: np.ndarray
    protect: np.ndarray
    contour_protect: np.ndarray
    thin_support: np.ndarray
    region: np.ndarray
    material: np.ndarray
    signed_distance: np.ndarray


@dataclass(frozen=True)
class CellSamplingPlan:
    """Compact source-sample weights shared by diffuse and normal reduction."""

    weights: np.ndarray
    active: np.ndarray


def _normalise(values: np.ndarray, mask: np.ndarray, quantile: float = 0.985) -> np.ndarray:
    valid = values[mask]
    scale = float(np.quantile(valid, quantile)) if valid.size else 1.0
    return np.clip(values / max(scale, 1e-7), 0.0, 1.0).astype(np.float32)


def _gradient(lab: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    energy = np.zeros(alpha.shape, dtype=np.float32)
    for index, weight in enumerate((1.0, 0.72, 0.72)):
        channel = cv2.GaussianBlur(lab[..., index], (0, 0), 0.55)
        gx = cv2.Scharr(channel, cv2.CV_32F, 1, 0)
        gy = cv2.Scharr(channel, cv2.CV_32F, 0, 1)
        energy += (gx * gx + gy * gy) * weight
    alpha_gx = cv2.Scharr(alpha, cv2.CV_32F, 1, 0)
    alpha_gy = cv2.Scharr(alpha, cv2.CV_32F, 0, 1)
    energy += (alpha_gx * alpha_gx + alpha_gy * alpha_gy) * 0.65
    return _normalise(np.sqrt(np.maximum(energy, 0.0)), alpha > 0.01)


def _luminance_residual(luminance: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    source = luminance.astype(np.float32)
    if min(source.shape) >= 128:
        small = cv2.resize(source, (source.shape[1] // 2, source.shape[0] // 2), interpolation=cv2.INTER_AREA)
        base_small = cv2.bilateralFilter(small, 0, 0.08, 4.5)
        base = cv2.resize(base_small, (source.shape[1], source.shape[0]), interpolation=cv2.INTER_LINEAR)
    else:
        base = cv2.bilateralFilter(source, 0, 0.08, 9.0)
    base = cv2.GaussianBlur(base, (0, 0), 1.25)
    residual = np.maximum(luminance - base, 0.0)
    small = cv2.GaussianBlur(residual, (0, 0), 0.55)
    large = cv2.GaussianBlur(residual, (0, 0), 1.45)
    persistent = np.minimum(residual, np.maximum(small, large * 0.85))
    return _normalise(persistent, alpha > 0.05, 0.97) * (alpha > 0.02)


def _interior_ink(lab: np.ndarray, alpha: np.ndarray, edge: np.ndarray, distance_inside: np.ndarray, supersample: int) -> np.ndarray:
    """Recover compact source ink such as eyes and mouths before cell reduction."""

    mask = alpha > 0.02
    if not np.any(mask):
        return np.zeros_like(mask)
    luminance = lab[..., 0]
    dark_threshold = float(np.quantile(luminance[mask], 0.14))
    hard_threshold = float(np.quantile(edge[mask], 0.78))
    luminance_u8 = np.rint(np.clip(luminance, 0.0, 1.0) * 255.0).astype(np.uint8)
    canny = cv2.Canny(luminance_u8, 32, 96, apertureSize=3, L2gradient=True) > 0
    dark_edge = mask & (luminance <= dark_threshold) & ((edge >= hard_threshold * 0.58) | canny)
    # Keep true interior marks separate from the exterior outline. The distance
    # scales with supersampling so the rule has stable logical-pixel semantics.
    return dark_edge & (distance_inside > max(1.25, supersample * 0.40))


def _thin_source_support(alpha: np.ndarray, distance_inside: np.ndarray, supersample: int) -> np.ndarray:
    """Return source-backed centerlines for appendages thinner than two cells."""

    mask = alpha > 0.08
    if not np.any(mask):
        return np.zeros_like(mask)
    skeleton = skeletonize(mask)
    # A radius below 0.90 logical cells corresponds to a structure under
    # roughly two cells wide. This captures legs, cables, antennae and tips but
    # does not turn the main torso into a blanket protection mask.
    return skeleton & (distance_inside <= max(1.5, supersample * 0.90))


def analyse_frame(
    rgba_u8: np.ndarray,
    transform: GeometryTransform,
    semantic_mask: np.ndarray | None,
    supersample: int,
) -> FrameEvidence:
    rendered = render_supersampled(rgba_u8, transform, supersample)
    alpha = rendered[..., 3]
    mask = alpha > 0.02
    lab = srgb_to_oklab(rendered[..., :3])
    edge = _gradient(lab, alpha)
    residual = _luminance_residual(lab[..., 0], alpha)
    distance_inside = ndimage.distance_transform_edt(mask)
    distance_outside = ndimage.distance_transform_edt(~mask)
    signed_distance = (distance_inside - distance_outside).astype(np.float32) / max(supersample, 1)
    dog = np.abs(cv2.GaussianBlur(lab[..., 0], (0, 0), 0.65) - cv2.GaussianBlur(lab[..., 0], (0, 0), 2.0))
    dog = _normalise(dog, mask, 0.975)
    feature = np.clip(edge * 0.42 + dog * 0.38 + residual * 0.35, 0.0, 1.0) * mask
    interior_ink = _interior_ink(lab, alpha, edge, distance_inside, supersample)
    feature[interior_ink] = np.maximum(feature[interior_ink], 0.88)
    foreground_area = int(mask.sum())
    if foreground_area >= 32:
        segments = int(np.clip(foreground_area / max((supersample * 2.2) ** 2, 4.0), 24, 1400))
        regions = slic(
            lab,
            n_segments=segments,
            compactness=0.16,
            sigma=0.0,
            start_label=0,
            mask=mask,
            channel_axis=-1,
            convert2lab=False,
            enforce_connectivity=True,
            min_size_factor=0.35,
            max_size_factor=3.5,
            max_num_iter=5,
        ).astype(np.int32)
        regions[~mask] = -1
    else:
        regions = np.full(mask.shape, -1, dtype=np.int32)
    # Protect high-resolution local marks independently from silhouette
    # cleanup. Interior source ink receives the same deliberate protection that
    # made the v2 face compiler effective, without preserving every dark rim.
    protect = (feature >= 0.62) & (distance_inside >= max(0.8, supersample * 0.20))
    protect |= interior_ink
    contour_protect = (feature >= 0.80) & (distance_inside >= max(0.8, supersample * 0.20))
    thin_support = _thin_source_support(alpha, distance_inside, supersample)
    if semantic_mask is None:
        semantic = np.zeros(alpha.shape, dtype=bool)
    else:
        semantic = transform_mask(semantic_mask, transform, supersample)
    protect |= semantic
    contour_protect |= semantic
    return FrameEvidence(
        rendered,
        alpha,
        lab,
        edge,
        feature,
        residual,
        interior_ink,
        regions,
        protect,
        contour_protect,
        thin_support,
        semantic,
        signed_distance,
        supersample,
    )


def compile_cell_evidence(
    frame: FrameEvidence,
    width: int,
    height: int,
    *,
    include_sampling: bool = False,
) -> CellEvidence | tuple[CellEvidence, CellSamplingPlan]:
    ss = frame.supersample
    if frame.alpha.shape != (height * ss, width * ss):
        raise ValueError("analysis dimensions do not match target grid")
    scalar_axes = (0, 2, 1, 3)
    alpha_blocks = frame.alpha.reshape(height, ss, width, ss).transpose(scalar_axes)
    semantic_blocks = frame.semantic_transparency.reshape(height, ss, width, ss).transpose(scalar_axes)
    edge_blocks = frame.edge.reshape(height, ss, width, ss).transpose(scalar_axes)
    feature_blocks = frame.feature.reshape(height, ss, width, ss).transpose(scalar_axes)
    highlight_blocks = frame.luminance_residual.reshape(height, ss, width, ss).transpose(scalar_axes)
    ink_blocks = frame.interior_ink.reshape(height, ss, width, ss).transpose(scalar_axes)
    protect_blocks = frame.protect.reshape(height, ss, width, ss).transpose(scalar_axes)
    contour_protect_blocks = frame.contour_protect.reshape(height, ss, width, ss).transpose(scalar_axes)
    thin_blocks = frame.thin_support.reshape(height, ss, width, ss).transpose(scalar_axes)
    sdf_blocks = frame.signed_distance.reshape(height, ss, width, ss).transpose(scalar_axes)
    region_blocks = frame.regions.reshape(height, ss, width, ss).transpose(scalar_axes)
    lab_blocks = frame.lab.reshape(height, ss, width, ss, 3).transpose(0, 2, 1, 3, 4)
    coverage = alpha_blocks.mean(axis=(2, 3)).astype(np.float32)
    semantic_coverage = semantic_blocks.mean(axis=(2, 3)).astype(np.float32)
    cell_edge = np.quantile(edge_blocks, 0.86, axis=(2, 3)).astype(np.float32)
    cell_feature = np.quantile(feature_blocks, 0.90, axis=(2, 3)).astype(np.float32)
    cell_highlight = np.quantile(highlight_blocks, 0.84, axis=(2, 3)).astype(np.float32)
    cell_ink = ink_blocks.mean(axis=(2, 3)).astype(np.float32)
    cell_protect = protect_blocks.max(axis=(2, 3)) > 0
    cell_contour_protect = contour_protect_blocks.max(axis=(2, 3)) > 0
    cell_thin_support = (thin_blocks.max(axis=(2, 3)) > 0) & (coverage >= 0.035)
    cell_sdf = np.median(sdf_blocks, axis=(2, 3)).astype(np.float32)

    sample_count = ss * ss
    alpha_flat = alpha_blocks.reshape(height, width, sample_count)
    edge_flat = edge_blocks.reshape(height, width, sample_count)
    feature_flat = feature_blocks.reshape(height, width, sample_count)
    region_flat = region_blocks.reshape(height, width, sample_count)
    lab_flat = lab_blocks.reshape(height, width, sample_count, 3)
    active = alpha_flat > 0.02
    has_active = np.any(active, axis=2)
    coverage[~has_active] = 0.0

    # Use a weighted area vote rather than one anchor sample. A single dark eye
    # or highlight must not claim the whole logical cell merely because its
    # centre has low gradient. Composite bincount keys keep this vectorized.
    valid_regions = active & (region_flat >= 0)
    cell_region = np.full((height, width), -1, dtype=np.int32)
    if np.any(valid_regions):
        region_count = int(np.max(region_flat[valid_regions])) + 1
        cell_ids = np.arange(height * width, dtype=np.int64).reshape(height, width, 1)
        keys = cell_ids * region_count + np.maximum(region_flat, 0).astype(np.int64)
        vote_weights = alpha_flat * (1.0 + feature_flat * 0.45)
        votes = np.bincount(
            keys[valid_regions],
            weights=vote_weights[valid_regions],
            minlength=height * width * region_count,
        ).reshape(height * width, region_count)
        winning_weight = np.max(votes, axis=1)
        winners = np.argmax(votes, axis=1).astype(np.int32)
        winners[winning_weight <= 0.0] = -1
        cell_region = winners.reshape(height, width)
    chosen = active & ((region_flat == cell_region[..., None]) | (cell_region[..., None] < 0))
    chosen_any = np.any(chosen, axis=2)
    chosen = np.where(chosen_any[..., None], chosen, active)

    weights = alpha_flat * (1.0 + feature_flat * 0.9 + edge_flat * 0.35) * chosen
    weight_sum = np.maximum(np.sum(weights, axis=2, keepdims=True), 1e-7)
    center = np.sum(lab_flat * weights[..., None], axis=2) / weight_sum
    distance = np.sum((lab_flat - center[..., None, :]) ** 2, axis=3)
    distance[~chosen] = np.inf
    medoid_index = np.argmin(distance, axis=2)
    cell_lab = np.take_along_axis(lab_flat, medoid_index[..., None, None], axis=2)[..., 0, :].astype(np.float32)
    cell_lab[~has_active] = 0.0

    # Vectorized masked order statistics replace thousands of tiny quantile
    # calls while retaining the same per-cell dark-detail evidence.
    luminance = np.where(active, lab_flat[..., 0], np.inf)
    ordered_luminance = np.sort(luminance, axis=2)
    active_count = np.count_nonzero(active, axis=2)
    median_index = np.maximum(0, (active_count - 1) // 2)
    low_index = np.maximum(0, np.floor((active_count - 1) * 0.18).astype(np.int32))
    median = np.take_along_axis(ordered_luminance, median_index[..., None], axis=2)[..., 0]
    low = np.take_along_axis(ordered_luminance, low_index[..., None], axis=2)[..., 0]
    darkness_delta = np.zeros_like(median, dtype=np.float32)
    np.subtract(median, low, out=darkness_delta, where=has_active)
    source_dark = np.clip(darkness_delta / 0.16, 0.0, 1.0).astype(np.float32)
    source_dark[~has_active] = 0.0
    hue = (np.arctan2(cell_lab[..., 2], cell_lab[..., 1]) + np.pi) / (2.0 * np.pi)
    hue_bin = np.clip(np.floor(hue * 10.0), 0, 9).astype(np.int16)
    chroma_bin = np.clip(np.floor(np.hypot(cell_lab[..., 1], cell_lab[..., 2]) / 0.055), 0, 3).astype(np.int16)
    material = (hue_bin * 4 + chroma_bin).astype(np.int16)
    material[coverage <= 0.002] = -1
    evidence = CellEvidence(
        coverage,
        semantic_coverage,
        cell_lab,
        cell_edge,
        cell_feature,
        cell_highlight,
        source_dark,
        cell_ink,
        cell_protect,
        cell_contour_protect,
        cell_thin_support,
        cell_region,
        material,
        cell_sdf,
    )
    if include_sampling:
        return evidence, CellSamplingPlan(weights.astype(np.float32), has_active)
    return evidence
