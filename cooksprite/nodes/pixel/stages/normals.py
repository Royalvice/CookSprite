"""Geometry-consistent tangent-normal reduction for logical pixels."""

from __future__ import annotations

import numpy as np

from .evidence import CellSamplingPlan


def reduce_normal_cells(
    normal_vectors: np.ndarray,
    sampling: CellSamplingPlan,
    width: int,
    height: int,
) -> np.ndarray:
    sample_count = normal_vectors.shape[0] // height
    if normal_vectors.shape[:2] != (height * sample_count, width * sample_count):
        raise ValueError("normal analysis dimensions do not match target grid")
    blocks = normal_vectors.reshape(height, sample_count, width, sample_count, 3).transpose(0, 2, 1, 3, 4)
    flat = blocks.reshape(height, width, sample_count * sample_count, 3)
    weights = sampling.weights
    total = np.maximum(np.sum(weights, axis=2, keepdims=True), 1e-7)
    vectors = np.sum(flat * weights[..., None], axis=2) / total
    length = np.linalg.norm(vectors, axis=2, keepdims=True)
    neutral = np.zeros_like(vectors)
    neutral[..., 2] = 1.0
    vectors = np.where(length > 1e-7, vectors / np.maximum(length, 1e-7), neutral)
    vectors[~sampling.active] = neutral[~sampling.active]
    return vectors.astype(np.float32)


def normals_to_rgb(vectors: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(vectors, axis=2, keepdims=True)
    neutral = np.zeros_like(vectors)
    neutral[..., 2] = 1.0
    unit = np.where(length > 1e-7, vectors / np.maximum(length, 1e-7), neutral)
    encoded = np.clip(unit * 0.5 + 0.5, 0.0, 1.0).astype(np.float32)
    encoded[alpha <= 0.0] = (0.5, 0.5, 1.0)
    return encoded
