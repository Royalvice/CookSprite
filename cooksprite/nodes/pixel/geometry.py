"""Shared sequence geometry for arbitrary rectangular logical grids."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .color import linear_to_srgb, srgb_to_linear
from .types import TargetGrid


@dataclass(frozen=True)
class GeometryTransform:
    source_bbox_xyxy: tuple[int, int, int, int]
    target_width: int
    target_height: int
    padding_xy: tuple[int, int]
    scale: float
    draw_size_wh: tuple[int, int]
    offset_xy: tuple[float, float]

    def as_dict(self) -> dict[str, object]:
        return {
            "source_bbox_xyxy": list(self.source_bbox_xyxy),
            "target_size": [self.target_width, self.target_height],
            "padding_xy": list(self.padding_xy),
            "scale": self.scale,
            "draw_size_wh": list(self.draw_size_wh),
            "offset_xy": list(self.offset_xy),
        }


def foreground_bbox(alpha: np.ndarray, threshold: float = 1.0 / 255.0) -> tuple[int, int, int, int]:
    yy, xx = np.nonzero(alpha > threshold)
    if not xx.size:
        raise ValueError("empty foreground Alpha")
    return int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1


def union_bbox(alphas: list[np.ndarray], threshold: float = 1.0 / 255.0) -> tuple[int, int, int, int]:
    boxes = [foreground_bbox(alpha, threshold) for alpha in alphas]
    return min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes)


def build_transform(bbox: tuple[int, int, int, int], target: TargetGrid) -> GeometryTransform:
    x0, y0, x1, y1 = bbox
    source_width = x1 - x0
    source_height = y1 - y0
    available_width = target.width - 2 * target.resolved_padding_x
    available_height = target.height - 2 * target.resolved_padding_y
    scale = min(available_width / source_width, available_height / source_height)
    draw_width = max(1, round(source_width * scale))
    draw_height = max(1, round(source_height * scale))
    offset_x = (target.width - draw_width) / 2.0
    offset_y = (target.height - draw_height) / 2.0
    return GeometryTransform(
        bbox,
        target.width,
        target.height,
        (target.resolved_padding_x, target.resolved_padding_y),
        scale,
        (draw_width, draw_height),
        (offset_x, offset_y),
    )


def render_supersampled(rgba_u8: np.ndarray, transform: GeometryTransform, supersample: int) -> np.ndarray:
    """Render straight RGBA through a premultiplied linear-light affine transform."""

    x0, y0, x1, y1 = transform.source_bbox_xyxy
    crop = rgba_u8[y0:y1, x0:x1].astype(np.float32) / 255.0
    source_height, source_width = crop.shape[:2]
    output_width = transform.target_width * supersample
    output_height = transform.target_height * supersample
    matrix = np.array(
        [
            [transform.draw_size_wh[0] * supersample / source_width, 0.0, transform.offset_xy[0] * supersample],
            [0.0, transform.draw_size_wh[1] * supersample / source_height, transform.offset_xy[1] * supersample],
        ],
        dtype=np.float32,
    )
    alpha = crop[..., 3]
    premult = srgb_to_linear(crop[..., :3]) * alpha[..., None]
    warped_alpha = cv2.warpAffine(alpha, matrix, (output_width, output_height), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
    warped_premult = cv2.warpAffine(premult, matrix, (output_width, output_height), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
    warped_alpha = np.clip(warped_alpha, 0.0, 1.0)
    linear = np.divide(
        warped_premult,
        np.maximum(warped_alpha[..., None], 1e-7),
        out=np.zeros_like(warped_premult),
        where=warped_alpha[..., None] > 1e-7,
    )
    rgba = np.concatenate((linear_to_srgb(np.clip(linear, 0.0, 1.0)), warped_alpha[..., None]), axis=2)
    rgba[warped_alpha <= 0.0, :3] = 0.0
    return rgba.astype(np.float32)


def transform_mask(mask: np.ndarray, transform: GeometryTransform, supersample: int) -> np.ndarray:
    x0, y0, x1, y1 = transform.source_bbox_xyxy
    crop = mask[y0:y1, x0:x1].astype(np.uint8)
    source_height, source_width = crop.shape
    matrix = np.array(
        [
            [transform.draw_size_wh[0] * supersample / source_width, 0.0, transform.offset_xy[0] * supersample],
            [0.0, transform.draw_size_wh[1] * supersample / source_height, transform.offset_xy[1] * supersample],
        ],
        dtype=np.float32,
    )
    return cv2.warpAffine(
        crop,
        matrix,
        (transform.target_width * supersample, transform.target_height * supersample),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
    ) > 0


def render_normal_supersampled(
    normal: np.ndarray,
    alpha: np.ndarray,
    transform: GeometryTransform,
    supersample: int,
) -> np.ndarray:
    """Warp encoded tangent normals as alpha-weighted vectors."""

    x0, y0, x1, y1 = transform.source_bbox_xyxy
    vectors = np.clip(normal[y0:y1, x0:x1, :3], 0.0, 1.0).astype(np.float32) * 2.0 - 1.0
    source_alpha = alpha[y0:y1, x0:x1].astype(np.float32)
    if np.issubdtype(alpha.dtype, np.integer):
        source_alpha /= float(np.iinfo(alpha.dtype).max)
    source_alpha = np.clip(source_alpha, 0.0, 1.0)
    source_height, source_width = source_alpha.shape
    output_width = transform.target_width * supersample
    output_height = transform.target_height * supersample
    matrix = np.array(
        [
            [transform.draw_size_wh[0] * supersample / source_width, 0.0, transform.offset_xy[0] * supersample],
            [0.0, transform.draw_size_wh[1] * supersample / source_height, transform.offset_xy[1] * supersample],
        ],
        dtype=np.float32,
    )
    premultiplied = vectors * source_alpha[..., None]
    warped_alpha = cv2.warpAffine(
        source_alpha,
        matrix,
        (output_width, output_height),
        flags=cv2.INTER_LANCZOS4,
        borderMode=cv2.BORDER_CONSTANT,
    )
    warped_vectors = cv2.warpAffine(
        premultiplied,
        matrix,
        (output_width, output_height),
        flags=cv2.INTER_LANCZOS4,
        borderMode=cv2.BORDER_CONSTANT,
    )
    warped_alpha = np.clip(warped_alpha, 0.0, 1.0)
    vectors = np.divide(
        warped_vectors,
        np.maximum(warped_alpha[..., None], 1e-7),
        out=np.zeros_like(warped_vectors),
        where=warped_alpha[..., None] > 1e-7,
    )
    length = np.linalg.norm(vectors, axis=2, keepdims=True)
    neutral = np.zeros_like(vectors)
    neutral[..., 2] = 1.0
    return np.where(length > 1e-7, vectors / np.maximum(length, 1e-7), neutral).astype(np.float32)


def geometry_metrics(alpha: np.ndarray, target: TargetGrid) -> dict[str, float | int | list[int]]:
    x0, y0, x1, y1 = foreground_bbox(alpha)
    center_x = (x0 + x1 - 1) / 2.0
    center_y = (y0 + y1 - 1) / 2.0
    return {
        "bbox_xyxy": [x0, y0, x1, y1],
        "center_error_x": abs(center_x - (target.width - 1) / 2.0),
        "center_error_y": abs(center_y - (target.height - 1) / 2.0),
        "padding_left": x0,
        "padding_right": target.width - x1,
        "padding_top": y0,
        "padding_bottom": target.height - y1,
    }
