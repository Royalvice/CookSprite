"""Read-only Action registry shared by API, Web, CLI, and agent harnesses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .domain import ActionDescriptor, ArtifactRef, ModelOption, ToolDescriptor
from .recipes import Recipe, supports
from .tool_packages import ToolPackageRegistry, tool_packages

ACTION_IDS = (
    "image.generate",
    "animation.generate",
    "frame.redraw",
    "sheet.slice",
    "video.sample",
    "normal.generate",
    "image.views",
    "image.pixelize",
    "image.cutout",
)


class RegistryError(ValueError):
    pass


class CookSpriteRegistry:
    """Single source of truth for product Actions and built-in Tool packages."""

    def __init__(
        self,
        path: str | Path | None = None,
        packages: ToolPackageRegistry = tool_packages,
    ):
        self.path = Path(path or Path(__file__).with_name("actions.yaml"))
        self.packages = packages
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if raw.get("schema") != "cooksprite.actions/v1":
            raise RegistryError("unsupported Action registry schema")
        actions = [ActionDescriptor.model_validate(item) for item in raw.get("actions", [])]
        ids = tuple(action.id for action in actions)
        if len(ids) != len(set(ids)):
            raise RegistryError("Action ids must be unique")
        if ids != ACTION_IDS:
            raise RegistryError(f"Action ids/order must remain stable: {ACTION_IDS}")
        for action in actions:
            if set(action.i18n) != {"zh-CN", "en"}:
                raise RegistryError(f"{action.id}: bilingual labels are required")
            for control in action.controls:
                if set(control.i18n) != {"zh-CN", "en"}:
                    raise RegistryError(f"{action.id}.{control.id}: bilingual labels are required")
                option_ids = [option.id for option in control.options]
                if len(option_ids) != len(set(option_ids)):
                    raise RegistryError(f"{action.id}.{control.id}: duplicate option id")
                if control.options_range:
                    start, stop, step = control.options_range
                    if step <= 0 or stop < start:
                        raise RegistryError(f"{action.id}.{control.id}: invalid options_range")
        self._actions = {action.id: action for action in actions}
        self._examples: dict[str, ArtifactRef] = {}

    def set_examples(self, examples: dict[str, ArtifactRef]) -> None:
        """Bind registry example keys to normal, draggable Artifacts."""

        self._examples = dict(examples)

    def get(self, action_id: str) -> ActionDescriptor | None:
        return self._actions.get(action_id)

    def tools(self) -> list[ToolDescriptor]:
        return self.packages.tools()

    def package_manifests(self) -> list[dict[str, Any]]:
        return self.packages.dump()

    def list(
        self,
        runtime: dict[str, Any] | None = None,
        runtime_tools: set[str] | None = None,
        recipes: list[Recipe] | None = None,
    ) -> list[ActionDescriptor]:
        return [
            self._view(action, runtime, runtime_tools or set(), recipes or [])
            for action in self._actions.values()
        ]

    def view(
        self,
        action_id: str,
        runtime: dict[str, Any] | None = None,
        runtime_tools: set[str] | None = None,
        recipes: list[Recipe] | None = None,
    ) -> ActionDescriptor | None:
        action = self.get(action_id)
        return (
            self._view(action, runtime, runtime_tools or set(), recipes or []) if action else None
        )

    def _view(
        self,
        action: ActionDescriptor,
        runtime: dict[str, Any] | None,
        runtime_tools: set[str],
        recipes: list[Recipe],
    ) -> ActionDescriptor:
        data = action.model_dump(mode="json")
        for control_index, control in enumerate(action.controls):
            for option_index, option in enumerate(control.options):
                if option.example_key and option.example_key in self._examples:
                    data["controls"][control_index]["options"][option_index]["example"] = (
                        self._examples[option.example_key].model_dump(mode="json")
                    )
        ready = bool(runtime and runtime.get("snapshot"))
        compatible = [recipe for recipe in recipes if supports(recipe, action.id)]
        available = ready and bool(compatible)
        if not ready:
            reason = "runtime_not_ready"
        elif not compatible:
            reason = "compatible_recipe_missing"
        else:
            reason = None
        models = []
        if available and runtime:
            groups: dict[tuple[str, str, str], list[Recipe]] = {}
            for recipe in compatible:
                identity = (
                    str(recipe.checkpoint or recipe.id),
                    str(recipe.family),
                    _model_identity_label(recipe.label),
                )
                groups.setdefault(identity, []).append(recipe)
            for group in groups.values():
                representative = group[0]
                models.append(
                    ModelOption(
                        # Keep a recipe-backed id for compatibility.  The API
                        # resolves another recipe in this identity group when
                        # the input mode requires it.
                        id=f"{runtime['id']}:{representative.id}",
                        label=f"{runtime['label']} · {_model_identity_label(representative.label)}",
                        runtime_id=runtime["id"],
                        family=representative.family,
                        modes=list(dict.fromkeys(mode for item in group for mode in item.modes)),
                    ).model_dump(mode="json")
                )
        data.update(available=available, unavailable_reason=reason, models=models)
        return ActionDescriptor.model_validate(data)

    @staticmethod
    def validate_request(
        action: ActionDescriptor, inputs: dict[str, str | list[str]], values: dict[str, Any]
    ) -> None:
        unknown_inputs = set(inputs) - set(action.accepts)
        if unknown_inputs:
            raise RegistryError(f"unsupported input slots: {sorted(unknown_inputs)}")
        for slot, rule in action.accepts.items():
            supplied = inputs.get(slot)
            ids = supplied if isinstance(supplied, list) else ([supplied] if supplied else [])
            if rule.required and not ids:
                raise RegistryError(f"missing required input: {slot}")
            if len(ids) > rule.max:
                raise RegistryError(f"{slot} accepts at most {rule.max} artifact(s)")
        controls = {control.id: control for control in action.controls}
        unknown_values = set(values) - set(controls) - {"model", "runtime"}
        if unknown_values:
            raise RegistryError(f"unsupported values: {sorted(unknown_values)}")
        for key, value in values.items():
            control = controls.get(key)
            if not control:
                continue
            if control.options:
                allowed = {option.id for option in control.options}
                selected = value if isinstance(value, list) else [value]
                if any(str(item) not in allowed for item in selected):
                    raise RegistryError(f"{key} contains an unsupported option")
            if control.options_range:
                start, stop, step = control.options_range
                selected = value if isinstance(value, list) else [value]
                try:
                    numeric = [int(item) for item in selected]
                except (TypeError, ValueError) as exc:
                    raise RegistryError(f"{key} must be an integer option") from exc
                if any(item < start or item > stop or (item - start) % step for item in numeric):
                    raise RegistryError(f"{key} contains an unsupported option")
            if isinstance(value, (int, float)):
                if control.min is not None and value < control.min:
                    raise RegistryError(f"{key} is below minimum {control.min}")
                if control.max is not None and value > control.max:
                    raise RegistryError(f"{key} is above maximum {control.max}")

    @staticmethod
    def defaults(action: ActionDescriptor) -> dict[str, Any]:
        return {control.id: control.default for control in action.controls}


def _model_identity_label(label: str) -> str:
    """Remove workflow-mode suffixes from the user-facing model identity."""

    value = str(label or "").strip()
    for suffix in (" · T2I", " · I2I", " · T2V", " · I2V"):
        if value.endswith(suffix):
            return value[: -len(suffix)].rstrip()
    return value


# Backwards-compatible import name for contributors using the v0.1 API.
ActionRegistry = CookSpriteRegistry

__all__ = [
    "ACTION_IDS",
    "ActionRegistry",
    "CookSpriteRegistry",
    "RegistryError",
]
