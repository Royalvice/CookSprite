"""Stable, replayable geometry metadata for long pixel sequences.

This module intentionally uses only stdlib and NumPy-adjacent value shapes so
it can ship inside the isolated ComfyUI node pack.  The API validates the
public domain model; the node uses this small representation at execution
time.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .types import TargetGrid

if TYPE_CHECKING:
    from .geometry import GeometryTransform

SCHEMA = "cooksprite.pixel-geometry-plan/v1"
ALGORITHM = "cooksprite.pixel-compiler/v2"
SAMPLING = "cooksprite.cell-sampling/v1"


@dataclass(frozen=True)
class PixelGeometryPlanValue:
    """Opaque ComfyUI value carried only between CookSprite bridge nodes."""

    payload: dict[str, Any]


def resolve_temporal_mode(requested: str, temporal: dict[str, object] | None) -> str:
    """Choose optical flow only for explicitly continuous sampled video.

    This lives with the plan metadata rather than the OpenCV compiler so API
    and lightweight tests can validate the product rule without importing any
    media-compute dependency.
    """

    requested = str(requested)
    sampled_video = (
        isinstance(temporal, dict)
        and temporal.get("source") == "sampled_video"
        and isinstance(temporal.get("sample_fps"), (int, float))
        and float(temporal["sample_fps"]) >= 8.0
    )
    if requested == "auto":
        return "flow" if sampled_video else "shared"
    if requested not in {"shared", "flow", "independent"}:
        raise ValueError("temporal_mode must be auto, shared, flow, or independent")
    if requested == "flow" and not sampled_video:
        raise ValueError(
            "continuous video flow requires a FrameSeq sampled from video at at least 8 FPS"
        )
    return requested


def source_order_sha256(frames: Iterable[dict[str, Any]]) -> str:
    """Digest source order and identity without embedding image bytes twice."""

    canonical = [
        {
            "artifact": str(frame["artifact"]),
            "sha256": str(frame["sha256"]),
            "canvas": [int(frame["canvas"][0]), int(frame["canvas"][1])],
        }
        for frame in frames
    ]
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def plan_payload(
    *,
    frames: list[dict[str, Any]],
    canvas: tuple[int, int],
    transform: GeometryTransform | None = None,
    transforms: list[GeometryTransform] | None = None,
    target: TargetGrid,
    supersample: int,
    temporal_mode: str,
    profile: str,
    outline: bool,
    outline_color: str,
) -> dict[str, Any]:
    """Create the canonical v1 plan payload used by API and custom nodes."""

    if (transform is None) == (transforms is None):
        raise ValueError("pixel plan needs exactly one shared or per-frame transform")
    if temporal_mode not in {"shared", "flow", "independent"}:
        raise ValueError(f"unsupported temporal pixel mode: {temporal_mode}")
    plan_frames = [
        {
            "source_artifact": str(frame["artifact"]),
            "source_sha256": str(frame["sha256"]),
            "canvas": [int(frame["canvas"][0]), int(frame["canvas"][1])],
        }
        for frame in frames
    ]
    source_frames = [
        {"artifact": item["source_artifact"], "sha256": item["source_sha256"], "canvas": item["canvas"]}
        for item in plan_frames
    ]
    transform_payload: dict[str, Any]
    if transform is not None:
        transform_payload = {"mode": "shared", "value": transform.as_dict()}
    else:
        assert transforms is not None
        if len(transforms) != len(frames):
            raise ValueError("per-frame pixel plan transforms must match source frames")
        transform_payload = {
            "mode": "per_frame",
            "values": [item.as_dict() for item in transforms],
        }
    return {
        "schema": SCHEMA,
        "algorithm": ALGORITHM,
        "source_order_sha256": source_order_sha256(source_frames),
        "frames": plan_frames,
        "canvas": [int(canvas[0]), int(canvas[1])],
        "transform": transform_payload,
        "target": [int(target.width), int(target.height)],
        "padding": [int(target.resolved_padding_x), int(target.resolved_padding_y)],
        "supersample": int(supersample),
        "sampling": SAMPLING,
        "temporal_mode": temporal_mode,
        "profile": str(profile),
        "palette_budget": int(target.resolved_palette_budget),
        "outline": bool(outline),
        "outline_color": str(outline_color) if outline else None,
    }


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate enough at the Comfy boundary to fail before image compute."""

    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError("unsupported PixelGeometryPlan schema")
    if payload.get("algorithm") != ALGORITHM:
        raise ValueError("unsupported PixelGeometryPlan algorithm")
    frames = payload.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 240:
        raise ValueError("PixelGeometryPlan must describe 1 to 240 source frames")
    target = payload.get("target")
    padding = payload.get("padding")
    if not (
        isinstance(target, list)
        and len(target) == 2
        and all(isinstance(item, int) and 1 <= item <= 512 for item in target)
        and isinstance(padding, list)
        and len(padding) == 2
    ):
        raise ValueError("PixelGeometryPlan has invalid target geometry")
    transform = payload.get("transform")
    if not isinstance(transform, dict) or transform.get("mode") not in {"shared", "per_frame"}:
        raise ValueError("PixelGeometryPlan has invalid transform")
    if transform["mode"] == "shared" and not isinstance(transform.get("value"), dict):
        raise ValueError("PixelGeometryPlan shared transform is missing")
    if transform["mode"] == "per_frame" and (
        not isinstance(transform.get("values"), list) or len(transform["values"]) != len(frames)
    ):
        raise ValueError("PixelGeometryPlan per-frame transforms do not match sources")
    return payload


def transform_for_frame(
    payload: dict[str, Any], frame_index: int
) -> tuple[GeometryTransform, TargetGrid, int]:
    """Rehydrate exactly the transform used for a source frame."""

    # Geometry imports OpenCV, so defer it until a ComfyUI node actually needs
    # to replay a plan.  The API only validates/stores canonical JSON.
    from .geometry import GeometryTransform

    plan = validate_payload(payload)
    frames = plan["frames"]
    if not 0 <= int(frame_index) < len(frames):
        raise ValueError("PixelGeometryPlan frame index is outside the source sequence")
    raw = plan["transform"]
    source = raw["value"] if raw["mode"] == "shared" else raw["values"][int(frame_index)]
    try:
        transform = GeometryTransform(
            tuple(int(item) for item in source["source_bbox_xyxy"]),
            int(source["target_size"][0]),
            int(source["target_size"][1]),
            tuple(int(item) for item in source["padding_xy"]),
            float(source["scale"]),
            tuple(int(item) for item in source["draw_size_wh"]),
            tuple(float(item) for item in source["offset_xy"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("PixelGeometryPlan transform is malformed") from exc
    target = TargetGrid(
        int(plan["target"][0]),
        int(plan["target"][1]),
        int(plan["padding"][0]),
        int(plan["padding"][1]),
        int(plan["palette_budget"]) or None,
    )
    return transform, target, int(plan["supersample"])


__all__ = [
    "ALGORITHM",
    "SAMPLING",
    "SCHEMA",
    "PixelGeometryPlanValue",
    "plan_payload",
    "resolve_temporal_mode",
    "source_order_sha256",
    "transform_for_frame",
    "validate_payload",
]
