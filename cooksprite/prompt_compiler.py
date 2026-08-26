"""Small Registry-driven prompt renderer for stable Actions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .registry import CookSpriteRegistry

COMPILER_VERSION = "cooksprite.prompt-profile/v2"


@dataclass(frozen=True)
class CompiledPrompt:
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


class PromptCompiler:
    """Bind validated Action values to declarative prompt profiles."""

    def __init__(self, registry: CookSpriteRegistry | None = None):
        self.registry = registry or CookSpriteRegistry()

    def compile(
        self,
        action_id: str,
        mode: str,
        values: dict[str, Any],
    ) -> CompiledPrompt:
        action = self.registry.get(action_id)
        if action is None:
            raise ValueError(f"unknown Action: {action_id}")
        profile = self.registry.execution(action_id).get("prompt") or {}
        caption = _clean(values.get("prompt") or values.get("category") or "game sprite asset")
        if not profile:
            return CompiledPrompt(caption, self._metadata(action_id, mode, values, None))

        template_key = "edit_template" if mode in {"i2i", "i2v"} and profile.get("edit_template") else "template"
        template = str(profile.get(template_key) or "{caption}")
        category = str(values.get("category") or "character")
        action_id_value = str(values.get("action") or values.get("animation") or "idle")
        action_prompts = profile.get("action_prompts") or {}
        action_prompt = str(action_prompts.get(action_id_value) or "").strip()
        custom_prompt = _clean(values.get("prompt") or "")
        if action_prompt and custom_prompt:
            action_prompt = f"{action_prompt} Additional requirements: {custom_prompt}."
        elif not action_prompt:
            action_prompt = custom_prompt
        style_id = self._style_id(action_id, category, str(values.get("style") or ""), profile)
        style = self._option_description(action_id, "style", style_id)
        contract = str((profile.get("contracts") or {}).get(category) or "Show one complete asset")
        bindings = {
            "caption": caption,
            "action_prompt": action_prompt,
            "category": category,
            "style": style or style_id,
            "contract": contract,
            "action": action_id_value.replace("_", " "),
            "view": str(values.get("view") or "level").replace("_", " "),
            "direction": str(values.get("direction") or "s").replace("_", " "),
        }
        prompt = template.format_map(bindings).strip()
        return CompiledPrompt(
            prompt,
            self._metadata(action_id, mode, values, style_id or None, template),
        )

    def _style_id(
        self,
        action_id: str,
        category: str,
        selected: str,
        profile: dict[str, Any],
    ) -> str:
        aliases = profile.get("style_aliases") or {}
        mapped = str((aliases.get(category) or {}).get(selected) or selected)
        action = self.registry.get(action_id)
        if not action:
            return mapped
        control = next((item for item in action.controls if item.id == "style"), None)
        if not control:
            return mapped
        allowed = [
            item.id
            for item in control.options
            if not item.categories or category in item.categories
        ]
        if mapped in allowed:
            return mapped
        return allowed[0] if allowed else mapped

    def _option_description(self, action_id: str, control_id: str, option_id: str) -> str:
        action = self.registry.get(action_id)
        control = next((item for item in action.controls if item.id == control_id), None) if action else None
        option = next((item for item in control.options if item.id == option_id), None) if control else None
        if not option:
            return option_id.replace("_", " ")
        localized = option.i18n.get("en")
        return localized.description or localized.name if localized else option_id

    @staticmethod
    def _metadata(
        action_id: str,
        mode: str,
        values: dict[str, Any],
        style: str | None,
        template: str = "",
    ) -> dict[str, Any]:
        identity = hashlib.sha256(
            json.dumps(
                {"action": action_id, "mode": mode, "values": values, "template": template},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        return {
            "compiler_version": COMPILER_VERSION,
            "profile_sha256": identity,
            "action_id": action_id,
            "task": "video" if mode in {"i2v", "t2v"} else "image",
            "mode": mode,
            "category": values.get("category"),
            "style": style,
        }


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).rstrip(".")


__all__ = ["COMPILER_VERSION", "CompiledPrompt", "PromptCompiler"]
