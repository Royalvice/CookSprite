"""CookSprite control-plane prompt compilation."""

from __future__ import annotations

from typing import Any

from .prompt_compiler import COMPILER_VERSION, PromptCompiler


def compile_action_values(
    action_id: str,
    mode: str,
    values: dict[str, Any],
    compiler: PromptCompiler | None = None,
) -> tuple[str, dict[str, Any]]:
    """Compile one Action prompt or pass through the user's exact text."""

    raw_prompt = str(values.get("prompt") or "")
    if not bool(values.get("prompt_compile", True)):
        return raw_prompt, {
            "compiler_enabled": False,
            "task": "video" if mode in {"i2v", "t2v"} else "image",
            "mode": mode,
        }
    result = (compiler or PromptCompiler()).compile(action_id, mode, values)
    return result.prompt, {"compiler_enabled": True, **result.metadata}


__all__ = ["COMPILER_VERSION", "PromptCompiler", "compile_action_values"]
