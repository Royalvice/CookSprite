"""Clean-room, deterministic CookSprite prompt compiler.

This module is intentionally independent from every image/video model.  It is
copied beside the installable ComfyUI node so the node can run without the
CookSprite API or its browser.  The public request/result classes mirror the
small ``sprite_prompt_package`` contract while accepting CookSprite's UI
spelling (``pixel``, ``smooth``, ``level`` and ``top45``).
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class RenderStyle(_ValueEnum):
    PIXEL = "pixel"
    HIGH_RES = "smooth"


class CameraPreset(_ValueEnum):
    EYE_LEVEL = "eye_level"
    ELEVATED = "elevated"


class Orientation(_ValueEnum):
    FRONT = "front"
    RIGHT = "right"
    BACK = "back"


class PromptMode(_ValueEnum):
    T2I = "t2i"
    I2I = "i2i"
    I2V = "i2v"
    T2V = "t2v"


class MotionDirection(_ValueEnum):
    IN_PLACE = "in_place"
    SCREEN_LEFT = "screen_left"
    SCREEN_RIGHT = "screen_right"
    TOWARD_CAMERA = "toward_camera"
    AWAY_FROM_CAMERA = "away_from_camera"


class Action(_ValueEnum):
    IDLE = "idle"
    WALK = "walk"
    RUN = "run"
    JUMP = "jump"
    ATTACK = "attack"
    HIT = "hit"
    DEATH = "death"
    SKILL = "skill"
    TURN_360 = "turn_360"
    # ``cast`` is kept as a compatibility spelling used by the current UI.
    CAST = "cast"


class ModelFamily(_ValueEnum):
    GENERIC = "generic"
    H3 = "h3"
    LTX = "ltx"
    WAN = "wan"
    FLUX = "flux"
    Z_IMAGE = "z_image"
    KREA = "krea"
    MINIMAX = "minimax"


_ALIASES: dict[type[Enum], dict[str, str]] = {
    RenderStyle: {"px": "pixel", "hi": "smooth", "high_res": "smooth"},
    CameraPreset: {"level": "eye_level", "top45": "elevated"},
    ModelFamily: {"z-image": "z_image", "z-image-turbo": "z_image", "krea-2": "krea"},
}


def coerce_enum(value: Any, enum_type: type[_ValueEnum], field_name: str) -> _ValueEnum:
    if isinstance(value, enum_type):
        return value
    raw = str(value or "")
    raw = _ALIASES.get(enum_type, {}).get(raw, raw)
    try:
        return enum_type(raw)
    except ValueError as exc:
        choices = ", ".join(member.value for member in enum_type)
        raise ValueError(f"{field_name} must be one of ({choices}), got {value!r}") from exc


@dataclass(frozen=True)
class CameraContract:
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    projection: str = "orthographic"
    fixed: bool = True

    def __post_init__(self) -> None:
        for name, value in (
            ("yaw_deg", self.yaw_deg),
            ("pitch_deg", self.pitch_deg),
            ("roll_deg", self.roll_deg),
        ):
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.projection != "orthographic":
            raise ValueError("only orthographic projection is supported for Sprite prompts")
        if not self.fixed:
            raise ValueError("Sprite prompts require a fixed camera")

    @classmethod
    def from_view(
        cls,
        orientation: Orientation | str = Orientation.FRONT,
        preset: CameraPreset | str = CameraPreset.EYE_LEVEL,
    ) -> "CameraContract":
        view = coerce_enum(orientation, Orientation, "orientation")
        camera_preset = coerce_enum(preset, CameraPreset, "camera_preset")
        yaw = {Orientation.FRONT: 0.0, Orientation.RIGHT: 90.0, Orientation.BACK: 180.0}[view]
        pitch = {CameraPreset.EYE_LEVEL: 0.0, CameraPreset.ELEVATED: 25.0}[camera_preset]
        return cls(yaw_deg=yaw, pitch_deg=pitch)

    @property
    def preset(self) -> CameraPreset:
        return CameraPreset.ELEVATED if self.pitch_deg > 0 else CameraPreset.EYE_LEVEL

    def phrase(self) -> str:
        return (
            "orthographic projection, "
            f"yaw={self.yaw_deg:g} degrees, pitch={self.pitch_deg:+g} degrees, "
            f"roll={self.roll_deg:g} degrees, fixed camera"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection": self.projection,
            "yaw_deg": self.yaw_deg,
            "pitch_deg": self.pitch_deg,
            "roll_deg": self.roll_deg,
            "camera_motion": "forbidden",
            "fixed": self.fixed,
        }


DEFAULT_IMAGE_NEGATIVE = "extra characters, unrelated objects, cast shadow, floor, text, logo, watermark"
DEFAULT_VIDEO_NEGATIVE = "camera movement, scene change, extra characters, text, watermark"

CATEGORY_TEXT = {
    "character": "one complete full-body game character with a clear silhouette",
    "weapon": "one complete game weapon, fully visible and uncropped",
    "prop": "one complete game prop, fully visible and isolated",
    "terrain": "one orthographic, seamlessly tileable game terrain block",
    "scene": "one isolated game environment element",
    "vfx": "one centered, isolated game visual effect",
}


@dataclass(frozen=True)
class ImagePromptRequest:
    caption: str
    mode: PromptMode | str = PromptMode.T2I
    style: RenderStyle | str = RenderStyle.HIGH_RES
    category: str = "character"
    camera_preset: CameraPreset | str = CameraPreset.EYE_LEVEL
    orientation: Orientation | str = Orientation.FRONT
    facing: str = "right"
    resolution: tuple[int, int] = (512, 512)
    background: str = "bright fluorescent green near #00FF00"
    edit_instruction: str | None = None
    negative_terms: tuple[str, ...] = field(default_factory=tuple)
    camera: CameraContract | None = None

    def validate(self) -> None:
        if not str(self.caption).strip():
            raise ValueError("caption cannot be empty")
        mode = coerce_enum(self.mode, PromptMode, "mode")
        if mode not in (PromptMode.T2I, PromptMode.I2I):
            raise ValueError("ImagePromptRequest.mode must be t2i or i2i")
        if mode == PromptMode.T2I and self.edit_instruction and self.edit_instruction.strip():
            raise ValueError("edit_instruction requires i2i mode")
        coerce_enum(self.style, RenderStyle, "style")
        coerce_enum(self.camera_preset, CameraPreset, "camera_preset")
        coerce_enum(self.orientation, Orientation, "orientation")
        if self.facing not in {"left", "right"}:
            raise ValueError("facing must be 'left' or 'right'")
        _validate_resolution(self.resolution)
        if not str(self.background).strip():
            raise ValueError("background cannot be empty")

    def resolved_camera(self) -> CameraContract:
        return self.camera or CameraContract.from_view(self.orientation, self.camera_preset)


@dataclass(frozen=True)
class VideoPromptRequest:
    caption: str
    action: Action | str
    mode: PromptMode | str = PromptMode.I2V
    orientation: Orientation | str = Orientation.FRONT
    facing: str = "right"
    camera_preset: CameraPreset | str = CameraPreset.EYE_LEVEL
    direction: MotionDirection | str = MotionDirection.IN_PLACE
    model: ModelFamily | str = ModelFamily.GENERIC
    resolution: tuple[int, int] = (512, 512)
    duration_seconds: float | None = 5.0
    background: str = "bright fluorescent green near #00FF00"
    action_detail: str | None = None
    negative_terms: tuple[str, ...] = field(default_factory=tuple)
    camera: CameraContract | None = None

    def validate(self) -> None:
        if not str(self.caption).strip():
            raise ValueError("caption cannot be empty")
        action = coerce_enum(self.action, Action, "action")
        mode = coerce_enum(self.mode, PromptMode, "mode")
        if mode not in (PromptMode.I2V, PromptMode.T2V):
            raise ValueError("VideoPromptRequest.mode must be i2v or t2v")
        coerce_enum(self.orientation, Orientation, "orientation")
        if self.facing not in {"left", "right"}:
            raise ValueError("facing must be 'left' or 'right'")
        coerce_enum(self.camera_preset, CameraPreset, "camera_preset")
        direction = coerce_enum(self.direction, MotionDirection, "direction")
        coerce_enum(self.model, ModelFamily, "model")
        _validate_resolution(self.resolution)
        if self.duration_seconds is not None and (
            not math.isfinite(float(self.duration_seconds)) or self.duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be positive and finite")
        if action == Action.TURN_360 and direction != MotionDirection.IN_PLACE:
            raise ValueError("turn_360 is always an in-place rotation")

    def resolved_camera(self) -> CameraContract:
        return self.camera or CameraContract.from_view(self.orientation, self.camera_preset)


@dataclass(frozen=True)
class CompiledPrompt:
    request_id: str
    task: str
    mode: str
    prompt: str
    negative_prompt: str
    reference_required: bool
    camera_contract: CameraContract
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.request_id,
            "task": self.task,
            "mode": self.mode,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "reference_required": self.reference_required,
            "camera_contract": self.camera_contract.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PromptSpec:
    """Legacy 24-combination request shape retained for callers."""

    style: str
    camera_preset: str
    orientation: str
    mode: str
    facing: str = "right"
    resolution: tuple[int, int] = (512, 512)

    @property
    def id(self) -> str:
        return f"{self.style}-{self.camera_preset}-{self.orientation}-{self.mode}"


COMPILER_VERSION = "sprite_prompt_package_v1"

IMAGE_STYLE_TEXT = {
    RenderStyle.PIXEL: (
        "pixel-native game Sprite art with dense hard-edged pixel clusters, deliberate stepped diagonals, "
        "selective dark outlines, a limited palette and controlled highlights"
    ),
    RenderStyle.HIGH_RES: (
        "high-resolution non-pixel 2D game illustration with clean anti-aliased contours, "
        "flat cel-shaded material planes, controlled highlights and detailed surface construction"
    ),
}
IMAGE_VIEW_TEXT = {
    Orientation.FRONT: "front view with the face and chest toward the camera",
    Orientation.RIGHT: "right-side profile with the side silhouette readable",
    Orientation.BACK: "rear view showing only the back of the head and rear outfit; no face visible",
}
ACTION_TEXT = {
    Action.IDLE: "Hold a calm ready stance with subtle breathing and restrained cloth movement.",
    Action.WALK: "Complete one natural walk cycle in place with alternating steps and a gentle arm swing.",
    Action.RUN: "Complete one fast run cycle in place with alternating strides and compact arm drive.",
    Action.JUMP: "Crouch, spring straight up, reach a clear apex, then land and recover in place.",
    Action.ATTACK: "Perform one fast melee attack toward the facing direction: wind-up, one decisive strike, impact beat, then return to guard.",
    Action.HIT: "Take one sharp hit from the facing direction, recoil and stagger, then regain the ready stance.",
    Action.DEATH: "Suffer one decisive hit, stagger, fall in place and settle into a clear final pose.",
    Action.SKILL: "Charge a compact signature energy effect, release it toward the facing direction, then return to stance.",
    Action.CAST: "Charge and release a compact spell effect toward the facing direction, then return to stance.",
    Action.TURN_360: "Rotate smoothly once around the body's center through a complete 360-degree turn, ending in the original pose.",
}
LOOP_ACTIONS = frozenset({Action.IDLE, Action.WALK, Action.RUN})


class SpritePromptCompiler:
    """Compile deterministic image and fixed-camera video prompt packets."""

    version = COMPILER_VERSION

    def compile_image(self, request: ImagePromptRequest) -> CompiledPrompt:
        request.validate()
        mode = coerce_enum(request.mode, PromptMode, "mode")
        style = coerce_enum(request.style, RenderStyle, "style")
        orientation = coerce_enum(request.orientation, Orientation, "orientation")
        camera = request.resolved_camera()
        width, height = request.resolution
        caption = _clean_caption(request.caption)
        category = CATEGORY_TEXT.get(str(request.category), f"one complete {request.category} game asset")
        mode_line = (
            "Create one complete asset from the identity description."
            if mode == PromptMode.T2I
            else "Use the reference image as the exact identity anchor; preserve face, hair, proportions, costume, materials, colors and silhouette."
        )
        edit_line = (
            f"Edit only this requested detail: {_clean_caption(request.edit_instruction)}. Keep all non-target details unchanged."
            if request.edit_instruction and request.edit_instruction.strip()
            else None
        )
        view_line = IMAGE_VIEW_TEXT[orientation]
        if orientation == Orientation.RIGHT:
            view_line += f" The profile faces screen {request.facing}."
        prompt_parts = [
            mode_line,
            f"Asset contract: {category}.",
            f"Camera: {camera.phrase()}; {view_line}.",
            f"Rendering: {IMAGE_STYLE_TEXT[style]}.",
            f"Description: {caption}.",
        ]
        if edit_line:
            prompt_parts.append(edit_line)
        prompt_parts.append(
            f"Composition: one complete subject centered with a stable baseline on {request.background}; no floor or scene."
        )
        negative = _negative_prompt(request.negative_terms, DEFAULT_IMAGE_NEGATIVE)
        request_id = f"image-{style.value}-{camera.preset.value}-{orientation.value}-{mode.value}-{width}x{height}"
        return CompiledPrompt(
            request_id=request_id,
            task="image",
            mode=mode.value,
            prompt="\n\n".join(prompt_parts),
            negative_prompt=negative,
            reference_required=mode == PromptMode.I2I,
            camera_contract=camera,
            metadata={
                "compiler_version": self.version,
                "task": "image",
                "mode": mode.value,
                "category": request.category,
                "style": style.value,
                "camera_preset": camera.preset.value,
                "orientation": orientation.value,
                "screen_facing": request.facing if orientation == Orientation.RIGHT else None,
                "resolution": [int(width), int(height)],
                "edit_instruction": request.edit_instruction,
            },
        )

    def compile_video(self, request: VideoPromptRequest) -> CompiledPrompt:
        request.validate()
        action = coerce_enum(request.action, Action, "action")
        mode = coerce_enum(request.mode, PromptMode, "mode")
        orientation = coerce_enum(request.orientation, Orientation, "orientation")
        direction = coerce_enum(request.direction, MotionDirection, "direction")
        model = coerce_enum(request.model, ModelFamily, "model")
        camera = request.resolved_camera()
        width, height = request.resolution
        caption = _clean_caption(request.caption)
        identity_line = (
            "Use the reference image as the exact identity anchor; preserve the character, outfit, body proportions, colors, silhouette and facing direction."
            if mode == PromptMode.I2V
            else "Keep one consistent character, outfit, body proportions, colors and silhouette throughout the take."
        )
        action_line = request.action_detail.strip() if request.action_detail and request.action_detail.strip() else ACTION_TEXT[action]
        prompt = "\n\n".join(
            (
                identity_line,
                f"Character identity: {caption}.",
                f"View: {_video_view_line(orientation, request.facing)}.",
                f"Locked camera: {camera.phrase()}; fixed distance and full-body framing; the camera never pans, tilts, zooms, dollies or orbits.",
                f"Motion contract: {_direction_line(direction)}.",
                f"Action: {_action_motion_prefix(action, direction)} {action_line}",
                "Single continuous Sprite take, centered subject, stable background and shared foot pivot; all motion stays inside the frame.",
                f"Background: {request.background}; no floor or scene change.",
            )
        )
        duration = None if request.duration_seconds is None else round(float(request.duration_seconds), 3)
        return CompiledPrompt(
            request_id=f"video-{action.value}-{orientation.value}-{direction.value}-{mode.value}-{width}x{height}",
            task="video",
            mode=mode.value,
            prompt=prompt,
            negative_prompt=_negative_prompt(request.negative_terms, DEFAULT_VIDEO_NEGATIVE),
            reference_required=mode == PromptMode.I2V,
            camera_contract=camera,
            metadata={
                "compiler_version": self.version,
                "task": "video",
                "mode": mode.value,
                "action": action.value,
                "loop": action in LOOP_ACTIONS,
                "orientation": orientation.value,
                "screen_facing": request.facing if orientation == Orientation.RIGHT else None,
                "direction": direction.value,
                "model": model.value,
                "resolution": [int(width), int(height)],
                "duration_seconds": duration,
            },
        )

    def compile(self, request: ImagePromptRequest | VideoPromptRequest) -> CompiledPrompt:
        if isinstance(request, ImagePromptRequest):
            return self.compile_image(request)
        if isinstance(request, VideoPromptRequest):
            return self.compile_video(request)
        raise TypeError("request must be ImagePromptRequest or VideoPromptRequest")

    def image_matrix(self, caption: str, resolution: tuple[int, int] = (512, 512)) -> list[CompiledPrompt]:
        return [
            self.compile_image(
                ImagePromptRequest(
                    caption=caption,
                    style=style,
                    camera_preset=preset,
                    orientation=orientation,
                    mode=mode,
                    resolution=resolution,
                )
            )
            for style, preset, orientation, mode in itertools.product(
                RenderStyle, CameraPreset, Orientation, (PromptMode.T2I, PromptMode.I2I)
            )
        ]

    def video_actions(
        self,
        caption: str,
        mode: PromptMode | str = PromptMode.I2V,
        orientation: Orientation | str = Orientation.FRONT,
        camera_preset: CameraPreset | str = CameraPreset.EYE_LEVEL,
        direction: MotionDirection | str = MotionDirection.IN_PLACE,
        model: ModelFamily | str = ModelFamily.GENERIC,
        facing: str = "right",
        resolution: tuple[int, int] = (512, 512),
    ) -> list[CompiledPrompt]:
        return [
            self.compile_video(
                VideoPromptRequest(
                    caption=caption,
                    action=action,
                    mode=mode,
                    orientation=orientation,
                    facing=facing,
                    camera_preset=camera_preset,
                    direction=direction,
                    model=model,
                    resolution=resolution,
                )
            )
            for action in Action
            if action != Action.CAST
        ]


def compile_image_prompt(request: ImagePromptRequest) -> CompiledPrompt:
    return SpritePromptCompiler().compile_image(request)


def compile_video_prompt(request: VideoPromptRequest) -> CompiledPrompt:
    return SpritePromptCompiler().compile_video(request)


def compile_legacy_image(spec: PromptSpec, character_caption: str) -> dict[str, Any]:
    result = SpritePromptCompiler().compile_image(
        ImagePromptRequest(
            caption=character_caption,
            style=spec.style,
            camera_preset=spec.camera_preset,
            orientation=spec.orientation,
            mode=spec.mode,
            facing=spec.facing,
            resolution=spec.resolution,
        )
    )
    output = result.to_dict()
    output.update(
        {
            "id": spec.id,
            "style": spec.style,
            "camera_preset": spec.camera_preset,
            "orientation": spec.orientation,
            "mode": spec.mode,
            "screen_facing": spec.facing if spec.orientation in {"right"} else None,
            "resolution": list(spec.resolution),
        }
    )
    return output


def compile_action_prompt(action: Action | str) -> str:
    action_value = coerce_enum(action, Action, "action")
    return " ".join(
        (
            "Keep the exact character, outfit, armor, colors, proportions and silhouette from the reference.",
            "The character stays centered and keeps facing the same direction as the reference.",
            "Locked orthographic camera, fixed distance and fixed full-body framing; the camera never moves or changes viewpoint.",
            _action_motion_prefix(action_value, MotionDirection.IN_PLACE),
            ACTION_TEXT[action_value],
        )
    )


def as_jsonable(prompts: Iterable[CompiledPrompt]) -> list[dict[str, Any]]:
    return [prompt.to_dict() for prompt in prompts]


def _validate_resolution(resolution: tuple[int, int]) -> None:
    if len(resolution) != 2 or any(int(value) <= 0 for value in resolution):
        raise ValueError(f"resolution must contain two positive integers, got {resolution!r}")


def _action_motion_prefix(action: Action, direction: MotionDirection) -> str:
    if action == Action.TURN_360 or direction == MotionDirection.IN_PLACE:
        return "In place:"
    return f"Move {direction.value.replace('_', ' ')} while animating:"


def _direction_line(direction: MotionDirection) -> str:
    return {
        MotionDirection.IN_PLACE: "animate entirely in place; feet stay on the same ground point and the subject remains centered",
        MotionDirection.SCREEN_LEFT: "move laterally toward screen left while keeping the camera fixed",
        MotionDirection.SCREEN_RIGHT: "move laterally toward screen right while keeping the camera fixed",
        MotionDirection.TOWARD_CAMERA: "move toward the camera without changing the camera viewpoint",
        MotionDirection.AWAY_FROM_CAMERA: "move away from the camera without changing the camera viewpoint",
    }[direction]


def _video_view_line(orientation: Orientation, facing: str) -> str:
    return {
        Orientation.FRONT: "front-facing character",
        Orientation.RIGHT: f"right-side profile character facing screen {facing}",
        Orientation.BACK: "rear-facing character; show only the back of the head and outfit",
    }[orientation]


def _clean_caption(caption: str | None) -> str:
    return re.sub(r"\s+", " ", str(caption or "").strip()).rstrip(".")


def _negative_prompt(extra: Iterable[str], base: str) -> str:
    terms: list[str] = []
    for value in (*base.split(", "), *extra):
        clean = re.sub(r"\s+", " ", str(value).strip()).strip(" ,")
        if clean and clean.lower() not in {item.lower() for item in terms}:
            terms.append(clean)
    return ", ".join(terms)


__all__ = [
    "COMPILER_VERSION",
    "Action",
    "CameraContract",
    "CameraPreset",
    "CompiledPrompt",
    "ImagePromptRequest",
    "ModelFamily",
    "MotionDirection",
    "Orientation",
    "PromptMode",
    "PromptSpec",
    "RenderStyle",
    "SpritePromptCompiler",
    "VideoPromptRequest",
    "as_jsonable",
    "compile_action_prompt",
    "compile_image_prompt",
    "compile_legacy_image",
    "compile_video_prompt",
]
