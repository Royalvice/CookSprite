"""Material-relative tone roles and coherent highlight preservation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import cv2
import numpy as np

from .evidence import CellEvidence


class ToneRole(IntEnum):
    BACKGROUND = 0
    OUTLINE = 1
    DEEP_SHADOW = 2
    SHADOW = 3
    MIDTONE = 4
    LIGHT = 5
    SPECULAR = 6
    RIM_HIGHLIGHT = 7
    EMISSION = 8


@dataclass(frozen=True)
class ToneRoleMap:
    roles: np.ndarray
    highlight_mask: np.ndarray
    protect: np.ndarray
    counts: dict[str, int]


def _clean_highlights(candidate: np.ndarray, evidence: CellEvidence) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate.astype(np.uint8), connectivity=8)
    output = np.zeros_like(candidate)
    for index in range(1, count):
        component = labels == index
        area = int(stats[index, cv2.CC_STAT_AREA])
        peak = float(np.max(evidence.highlight[component])) if np.any(component) else 0.0
        feature = float(np.max(evidence.feature[component])) if np.any(component) else 0.0
        # Preserve coherent one-to-three-cell glints only when they are strong
        # multi-scale source evidence; larger highlights must also be regionally
        # coherent and are capped later by palette role quotas.
        if area >= 2 or peak >= 0.88 or feature >= 0.90:
            output |= component
    return output


def _tone_thresholds(evidence: CellEvidence, silhouette: np.ndarray) -> dict[int, tuple[float, float, float, float]]:
    thresholds: dict[int, tuple[float, float, float, float]] = {}
    luminance = evidence.lab[..., 0]
    for material in np.unique(evidence.material[silhouette]):
        if material < 0:
            continue
        values = luminance[silhouette & (evidence.material == material)]
        if values.size >= 3:
            thresholds[int(material)] = tuple(float(item) for item in np.quantile(values, (0.18, 0.38, 0.70, 0.88)))
    return thresholds


def _extract_tone_roles(
    evidence: CellEvidence,
    silhouette: np.ndarray,
    strokes: np.ndarray,
    thresholds: dict[int, tuple[float, float, float, float]],
) -> ToneRoleMap:
    roles = np.full(silhouette.shape, int(ToneRole.BACKGROUND), dtype=np.uint8)
    luminance = evidence.lab[..., 0]
    roles[silhouette] = int(ToneRole.MIDTONE)
    for material in np.unique(evidence.material[silhouette]):
        if material < 0:
            continue
        mask = silhouette & (evidence.material == material)
        values = thresholds.get(int(material))
        if values is None:
            continue
        q18, q38, q70, q88 = values
        roles[mask & (luminance <= q18)] = int(ToneRole.DEEP_SHADOW)
        roles[mask & (luminance > q18) & (luminance <= q38)] = int(ToneRole.SHADOW)
        roles[mask & (luminance >= q70)] = int(ToneRole.LIGHT)
        # A material-relative positive residual is required. A global brightness
        # threshold would incorrectly classify pale cloth as specular.
        highlight = mask & (luminance >= q88) & (evidence.highlight >= 0.52) & (evidence.feature >= 0.20)
        roles[highlight] = int(ToneRole.SPECULAR)
    raw_highlight = roles == int(ToneRole.SPECULAR)
    highlight = _clean_highlights(raw_highlight, evidence)
    roles[raw_highlight & ~highlight] = int(ToneRole.LIGHT)
    boundary = silhouette & ~(cv2.erode(silhouette.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0)
    rim = highlight & boundary & (evidence.edge >= 0.48)
    roles[rim] = int(ToneRole.RIM_HIGHLIGHT)
    chroma = np.hypot(evidence.lab[..., 1], evidence.lab[..., 2])
    emission = highlight & (chroma >= 0.105) & (evidence.highlight >= 0.70)
    roles[emission] = int(ToneRole.EMISSION)
    roles[strokes] = int(ToneRole.OUTLINE)
    protect = evidence.protect | highlight | strokes
    counts = {role.name.lower(): int(np.count_nonzero(roles == int(role))) for role in ToneRole}
    return ToneRoleMap(roles, highlight, protect, counts)


def extract_tone_roles(evidence: CellEvidence, silhouette: np.ndarray, strokes: np.ndarray) -> ToneRoleMap:
    return _extract_tone_roles(evidence, silhouette, strokes, _tone_thresholds(evidence, silhouette))


def extract_chunk_tone_roles(
    evidences: list[CellEvidence],
    silhouettes: list[np.ndarray],
    strokes: list[np.ndarray],
) -> list[ToneRoleMap]:
    """Use one order-independent material-tone policy for a sprite chunk."""

    per_frame = [_tone_thresholds(evidence, silhouette) for evidence, silhouette in zip(evidences, silhouettes, strict=True)]
    shared: dict[int, tuple[float, float, float, float]] = {}
    materials = sorted({material for thresholds in per_frame for material in thresholds})
    for material in materials:
        values = np.asarray([thresholds[material] for thresholds in per_frame if material in thresholds], dtype=np.float64)
        shared[material] = tuple(float(item) for item in np.median(values, axis=0))
    return [
        _extract_tone_roles(evidence, silhouette, stroke, shared)
        for evidence, silhouette, stroke in zip(evidences, silhouettes, strokes, strict=True)
    ]
