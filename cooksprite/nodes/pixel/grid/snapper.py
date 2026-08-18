"""Strict uniform-grid detector inspired by SpriteFusion Pixel Snapper."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.signal import find_peaks  # type: ignore[import-untyped]

from ..exceptions import GridDetectionError
from ..types import GridSpec


@dataclass(frozen=True)
class AxisGridFit:
    pitch: float
    phase: float
    confidence: float
    profile_score: float
    peak_count: int


@dataclass(frozen=True)
class GridFit:
    x: AxisGridFit
    y: AxisGridFit
    cuts_x: np.ndarray
    cuts_y: np.ndarray
    grid_width: int
    grid_height: int
    confidence: float
    constrained_warp: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "pitch_xy": [self.x.pitch, self.y.pitch],
            "phase_xy": [self.x.phase, self.y.phase],
            "axis_confidence": [self.x.confidence, self.y.confidence],
            "confidence": self.confidence,
            "grid_size": [self.grid_width, self.grid_height],
            "cuts_x": self.cuts_x.tolist(),
            "cuts_y": self.cuts_y.tolist(),
            "constrained_warp": self.constrained_warp,
        }


def _edge_profiles(frames: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    profiles_x: list[np.ndarray] = []
    profiles_y: list[np.ndarray] = []
    for rgba in frames:
        rgb = rgba[..., :3].astype(np.float32) / 255.0
        alpha = rgba[..., 3].astype(np.float32) / 255.0
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        gx = np.abs(cv2.Scharr(gray, cv2.CV_32F, 1, 0)) + np.abs(cv2.Scharr(alpha, cv2.CV_32F, 1, 0)) * 0.85
        gy = np.abs(cv2.Scharr(gray, cv2.CV_32F, 0, 1)) + np.abs(cv2.Scharr(alpha, cv2.CV_32F, 0, 1)) * 0.85
        profiles_x.append(np.mean(gx, axis=0))
        profiles_y.append(np.mean(gy, axis=1))
    x = np.median(np.stack(profiles_x), axis=0)
    y = np.median(np.stack(profiles_y), axis=0)
    x = cv2.GaussianBlur(x.reshape(1, -1), (0, 0), 0.55).reshape(-1)
    y = cv2.GaussianBlur(y.reshape(1, -1), (0, 0), 0.55).reshape(-1)
    return x.astype(np.float64), y.astype(np.float64)


def _candidate_pitches(profile: np.ndarray) -> list[float]:
    if len(profile) < 16:
        return []
    normalized = profile - np.median(profile)
    threshold = np.median(profile) + max(np.std(profile) * 0.45, np.max(profile) * 0.06)
    peaks, _ = find_peaks(profile, height=threshold, distance=2)
    candidates: list[float] = []
    if len(peaks) >= 3:
        differences = np.diff(peaks)
        for value in differences[(differences >= 2) & (differences <= min(64, len(profile) // 2))]:
            candidates.append(float(value))
        # Multiple missing grid lines produce harmonic distances. Add divisors.
        for value in list(candidates):
            for divisor in (2, 3, 4):
                if value / divisor >= 2:
                    candidates.append(value / divisor)
    fft = np.fft.rfft(normalized, n=2 * len(normalized))
    autocorrelation = np.fft.irfft(fft * np.conj(fft))[: len(normalized)]
    autocorrelation /= max(float(autocorrelation[0]), 1e-9)
    ac_peaks, _ = find_peaks(autocorrelation[2 : min(65, len(profile) // 2 + 1)], height=0.04)
    for index in ac_peaks:
        candidates.append(float(index + 2))
    # Include common integer pixel scales so phase scoring can win even when
    # content edges omit many implicit grid cuts.
    candidates.extend(float(value) for value in range(2, min(33, len(profile) // 2 + 1)))
    return sorted({round(value * 4.0) / 4.0 for value in candidates if 1.75 <= value <= 64.0})


def _phase_score(profile: np.ndarray, pitch: float) -> tuple[float, float]:
    phases = np.linspace(0.0, pitch, max(16, round(pitch * 8)), endpoint=False)
    positions = np.arange(len(profile), dtype=np.float64)
    baseline = float(np.mean(profile))
    spread = float(np.std(profile))
    best_phase = 0.0
    best_score = -float("inf")
    for phase in phases:
        cuts = phase + np.arange(-1, int(np.ceil((len(profile) - phase) / pitch)) + 2) * pitch
        cuts = cuts[(cuts >= 0.0) & (cuts <= len(profile) - 1)]
        if len(cuts) < 3:
            continue
        evidence = np.interp(cuts, positions, profile)
        score = float((np.mean(evidence) - baseline) / max(spread, 1e-8))
        if score > best_score:
            best_score = score
            best_phase = float(phase)
    return best_phase, best_score


def _robust_lattice_fit(profile: np.ndarray) -> AxisGridFit | None:
    """Fit one uniform cut lattice while rejecting harmonic grid guesses.

    A large harmonic can score well when it happens to cross only the
    strongest colour boundaries.  Conversely, a sub-harmonic crosses many
    empty locations.  Jointly scoring observed-edge alignment and predicted
    cut coverage makes the fundamental win in both cases.
    """

    prominence_floor = max(float(np.std(profile)) * 0.10, float(np.max(profile)) * 0.01, 1e-7)
    peaks, properties = find_peaks(profile, prominence=prominence_floor, distance=2)
    if len(peaks) < 4:
        return None
    prominences = np.asarray(properties["prominences"], dtype=np.float64)
    weights = prominences / max(float(np.sum(prominences)), 1e-9)
    first, last = float(peaks[0]), float(peaks[-1])
    adjacent = np.diff(peaks).astype(np.float64)
    dense_pitch: float | None = None
    if len(peaks) >= 8:
        dense_candidate = float((peaks[-1] - peaks[0]) / (len(peaks) - 1))
        if (
            dense_candidate >= 1.75
            and float(np.min(adjacent)) >= dense_candidate * 0.55
            and float(np.max(adjacent)) <= dense_candidate * 1.55
        ):
            dense_pitch = dense_candidate
    maximum_pitch = min(64.0, len(profile) / 3.0, max(2.0, (last - first) / 2.0))
    if maximum_pitch < 2.0:
        return None

    scored: list[tuple[float, float, float, float, float]] = []
    for candidate_pitch in np.arange(2.0, maximum_pitch + 0.0001, 0.025, dtype=np.float64):
        phases = np.linspace(0.0, candidate_pitch, 32, endpoint=False, dtype=np.float64)
        delta = np.abs(
            (peaks[:, None] - phases[None, :] + candidate_pitch * 0.5) % candidate_pitch
            - candidate_pitch * 0.5
        )
        kernel = np.exp(-0.5 * (delta / max(candidate_pitch * 0.14, 1e-6)) ** 2)
        phase_alignment = np.sum(kernel * weights[:, None], axis=0)
        phase_index = int(np.argmax(phase_alignment))
        phase = float(phases[phase_index])
        alignment = float(phase_alignment[phase_index])
        indices = np.arange(
            np.floor((first - phase) / candidate_pitch),
            np.ceil((last - phase) / candidate_pitch) + 1,
            dtype=np.float64,
        )
        predicted = phase + indices * candidate_pitch
        predicted = predicted[
            (predicted >= first - candidate_pitch * 0.2) & (predicted <= last + candidate_pitch * 0.2)
        ]
        if len(predicted) < 3:
            continue
        nearest = np.min(np.abs(predicted[:, None] - peaks[None, :]), axis=1)
        coverage = float(np.mean(nearest <= candidate_pitch * 0.23))
        score = alignment * 0.62 + coverage * 0.38
        scored.append((score, float(candidate_pitch), phase, alignment, coverage))
    if not scored:
        return None
    scored.sort(reverse=True)
    score, pitch, phase, _alignment, coverage = scored[0]
    if score < 0.52 or coverage < 0.50:
        return None
    if dense_pitch is not None and abs(pitch - dense_pitch) / dense_pitch > 0.05:
        pitch = dense_pitch
        phase = float(peaks[0] % pitch)
        score = max(score, 0.72)
        coverage = 1.0

    # Refine the winning lattice with Huber IRLS.  Peak coordinates mark the
    # left pixel of a two-sample derivative plateau, hence the +1 cut offset.
    lattice_indices = np.rint((peaks - phase) / pitch)
    design = np.column_stack((lattice_indices, np.ones(len(lattice_indices), dtype=np.float64)))
    robust_weights = weights.copy()
    coefficients = np.asarray((pitch, phase), dtype=np.float64)
    for _ in range(5):
        weighted_design = design * np.sqrt(robust_weights[:, None])
        weighted_observed = peaks.astype(np.float64) * np.sqrt(robust_weights)
        coefficients = np.linalg.lstsq(weighted_design, weighted_observed, rcond=None)[0]
        residual = peaks.astype(np.float64) - design @ coefficients
        scale = max(float(np.median(np.abs(residual - np.median(residual)))) * 1.4826, 0.20)
        huber_delta = max(scale * 1.5, float(coefficients[0]) * 0.08)
        huber = np.minimum(1.0, huber_delta / np.maximum(np.abs(residual), 1e-9))
        robust_weights = weights * huber
    refined_pitch = float(coefficients[0])
    if not (1.75 <= refined_pitch <= 64.0):
        return None
    nearest_integer = float(round(refined_pitch))
    if nearest_integer >= 2.0 and abs(nearest_integer - refined_pitch) / refined_pitch <= 0.012:
        refined_pitch = nearest_integer
    else:
        native_cells = max(3, round(len(profile) / refined_pitch))
        canvas_pitch = len(profile) / native_cells
        if abs(canvas_pitch - refined_pitch) / refined_pitch <= 0.02:
            refined_pitch = float(canvas_pitch)

    # Scharr places a two-sample plateau around an ideal cell cut.  Its
    # circular weighted midpoint is stable whether find_peaks selects the
    # left or right sample and remains meaningful for a shifted canvas.
    angles = 2.0 * np.pi * ((peaks.astype(np.float64) + 0.5) % refined_pitch) / refined_pitch
    resultant = np.sum(prominences * np.exp(1j * angles))
    refined_phase = float((np.angle(resultant) % (2.0 * np.pi)) * refined_pitch / (2.0 * np.pi))
    if min(refined_phase, refined_pitch - refined_phase) <= refined_pitch * 0.10:
        refined_phase = 0.0

    separated = [item[0] for item in scored[1:] if abs(item[1] - pitch) >= max(0.20, pitch * 0.04)]
    runner_up = max(separated) if separated else score - 0.10
    margin = max(0.0, score - runner_up)
    confidence = float(np.clip(0.38 + score * 0.42 + coverage * 0.12 + margin * 0.50, 0.0, 1.0))
    return AxisGridFit(refined_pitch, refined_phase, confidence, float(score), len(peaks))


def _fit_axis(profile: np.ndarray, manual_pitch: float | None, manual_phase: float | None) -> AxisGridFit:
    if manual_pitch is not None:
        phase = float(manual_phase or 0.0) % manual_pitch
        _, score = _phase_score(profile, manual_pitch)
        return AxisGridFit(float(manual_pitch), phase, 1.0, score, 0)
    lattice = _robust_lattice_fit(profile)
    if lattice is not None:
        return lattice
    candidates = _candidate_pitches(profile)
    if not candidates:
        raise GridDetectionError("not enough edge evidence to estimate a pixel grid")
    normalized = profile - np.mean(profile)
    autocorrelation = np.correlate(normalized, normalized, mode="full")[len(profile) - 1 :]
    autocorrelation /= max(float(autocorrelation[0]), 1e-9)
    scored: list[tuple[float, float, float, float, float]] = []
    for pitch in candidates:
        phase, phase_score = _phase_score(profile, pitch)
        lag = round(pitch)
        periodicity = float(autocorrelation[lag]) if lag < len(autocorrelation) else 0.0
        integer_prior = 0.08 if abs(pitch - round(pitch)) <= 0.08 else 0.0
        # Prefer the shortest fundamental whose evidence is close to its
        # harmonics, avoiding 2x/3x grid estimates.
        complexity_penalty = max(0.0, pitch - 16.0) * 0.006
        total = phase_score * 0.72 + periodicity * 0.48 + integer_prior - complexity_penalty
        scored.append((total, pitch, phase, phase_score, periodicity))
    scored.sort(reverse=True)
    total, pitch, phase, phase_score, periodicity = scored[0]
    # Strong content edges often make 2x/3x harmonics score higher than the
    # actual logical grid. Select the shortest divisor with at least 55% of the
    # harmonic score and positive autocorrelation evidence.
    fundamental = []
    for candidate in scored[1:]:
        candidate_total, candidate_pitch, _candidate_phase, _candidate_phase_score, candidate_periodicity = candidate
        ratio = pitch / candidate_pitch
        near_integer = 2 <= round(ratio) <= 8 and abs(ratio - round(ratio)) <= 0.08
        if near_integer and candidate_total >= total * 0.55 and candidate_periodicity >= 0.10:
            fundamental.append(candidate)
    if fundamental:
        total, pitch, phase, phase_score, periodicity = min(fundamental, key=lambda item: item[1])
    runner_up = scored[1][0] if len(scored) > 1 else total - 0.5
    confidence = float(np.clip(0.42 + total * 0.16 + max(0.0, total - runner_up) * 0.20, 0.0, 1.0))
    peaks, _ = find_peaks(profile, height=np.median(profile) + np.std(profile) * 0.45, distance=2)
    return AxisGridFit(float(pitch), float(phase), confidence, float(phase_score), len(peaks))


def _uniform_cuts(length: int, fit: AxisGridFit) -> np.ndarray:
    phase = fit.phase
    while phase > 0.5:
        phase -= fit.pitch
    start = phase
    while start + fit.pitch <= 0:
        start += fit.pitch
    cuts = start + np.arange(0, int(np.ceil((length - start) / fit.pitch)) + 1) * fit.pitch
    cuts = cuts[(cuts >= -0.5) & (cuts <= length + 0.5)]
    cuts = np.clip(cuts, 0.0, float(length))
    cuts = np.unique(np.concatenate(([0.0], cuts, [float(length)])))
    # A phase-shifted, cropped pseudo-pixel canvas contains partial border
    # cells.  They belong to the adjacent native cell and must not inflate the
    # recovered logical grid size.
    expected_cells = max(1, round(length / fit.pitch))
    while len(cuts) > expected_cells + 1 and len(cuts) > 2:
        left_width = float(cuts[1] - cuts[0])
        right_width = float(cuts[-1] - cuts[-2])
        cuts = np.delete(cuts, 1 if left_width <= right_width else -2)
    if len(cuts) < expected_cells + 1:
        cuts = np.linspace(0.0, float(length), expected_cells + 1, dtype=np.float64)
    return cuts.astype(np.float32)


def _constrain_cuts(cuts: np.ndarray, profile: np.ndarray, pitch: float) -> np.ndarray:
    output = cuts.copy()
    radius = max(1, round(pitch * 0.30))
    for index in range(1, len(cuts) - 1):
        center = round(cuts[index])
        start = max(1, center - radius)
        end = min(len(profile) - 1, center + radius + 1)
        if end > start:
            output[index] = float(start + int(np.argmax(profile[start:end])))
    if np.any(np.diff(output) < pitch * 0.42):
        return cuts
    return output


def detect_grid(frames: list[np.ndarray], spec: GridSpec) -> GridFit:
    if spec.constrained_warp and len(frames) != 1:
        raise GridDetectionError("constrained_warp is static-only; sequences require one strict uniform grid")
    profile_x, profile_y = _edge_profiles(frames)
    fit_x = _fit_axis(profile_x, spec.pixel_size_x if spec.mode == "manual" else None, spec.phase_x)
    fit_y = _fit_axis(profile_y, spec.pixel_size_y if spec.mode == "manual" else None, spec.phase_y)
    confidence = min(fit_x.confidence, fit_y.confidence)
    if spec.mode == "auto" and confidence < 0.48:
        raise GridDetectionError(f"grid confidence {confidence:.3f} is below 0.48; provide a manual pixel size")
    height, width = frames[0].shape[:2]
    cuts_x = _uniform_cuts(width, fit_x)
    cuts_y = _uniform_cuts(height, fit_y)
    if spec.constrained_warp:
        cuts_x = _constrain_cuts(cuts_x, profile_x, fit_x.pitch)
        cuts_y = _constrain_cuts(cuts_y, profile_y, fit_y.pitch)
    return GridFit(fit_x, fit_y, cuts_x, cuts_y, len(cuts_x) - 1, len(cuts_y) - 1, confidence, spec.constrained_warp)
