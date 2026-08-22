"""CookSprite control-plane prompt compilation.

Action clients send user intent. The API deterministically compiles that intent
once, then every ComfyUI workflow receives only the final model prompt.
"""

from __future__ import annotations

from typing import Any

from .prompt_compiler import *


def compile_action_values(
    action_id: str,
    mode: str,
    values: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Compile one Action's user text into the final workflow prompt."""

    raw_prompt = str(values.get("prompt") or "")
    enabled = bool(values.get("prompt_compile", True))
    task = "video" if action_id.startswith("animation.") or mode in {"i2v", "t2v"} else "image"
    if not enabled or action_id not in {"image.generate", "frame.redraw", "animation.generate"}:
        return raw_prompt, {
            "compiler_enabled": False,
            "task": task,
            "mode": mode,
        }

    resolution = int(values.get("resolution", 512))
    caption = raw_prompt.strip() or str(values.get("category") or "game sprite asset")
    compiler = SpritePromptCompiler()
    if task == "video":
        result = compiler.compile_video(
            VideoPromptRequest(
                caption=caption,
                action=str(values.get("action") or "idle"),
                mode=mode or "i2v",
                orientation="front",
                facing="right",
                camera_preset="top45" if values.get("view") == "top45" else "level",
                direction={"n": "away_from_camera", "s": "in_place"}.get(
                    str(values.get("direction") or "s"), "in_place"
                ),
                model=ModelFamily.GENERIC.value,
                resolution=(resolution, resolution),
                background=DEFAULT_GREEN_SCREEN_BACKGROUND,
            )
        )
    else:
        result = compiler.compile_image(
            ImagePromptRequest(
                caption=caption,
                mode=mode or "t2i",
                style=str(values.get("style") or "2d_action_game"),
                category=str(values.get("category") or "character"),
                camera_option="front_eye_level",
                camera_preset="eye_level",
                orientation="front",
                facing="right",
                resolution=(resolution, resolution),
                background=DEFAULT_GREEN_SCREEN_BACKGROUND,
            )
        )
    metadata = result.to_dict()
    metadata.pop("prompt", None)
    return result.prompt, {"compiler_enabled": True, **metadata}


__all__ = [
    "CHARACTER_CAMERA_OPTIONS",
    "CHARACTER_COMPOSITION_CORE",
    "CHARACTER_COMPOSITION_GENERAL",
    "CHARACTER_STYLE_OPTIONS",
    "COMPILER_VERSION",
    "DEFAULT_GREEN_SCREEN_BACKGROUND",
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
    "compile_action_values",
    "compile_image_prompt",
    "compile_legacy_image",
    "compile_video_prompt",
]
