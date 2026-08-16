"""Read-only Action registry shared by API, Web, CLI, and agent harnesses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .domain import ActionDescriptor, ArtifactRef, ModelOption
from .recipes import Recipe, supports

ACTION_IDS = (
    "image.generate",
    "animation.generate",
    "frame.redraw",
    "sheet.slice",
    "video.sample",
    "normal.generate",
    "sprite.export",
)


class RegistryError(ValueError):
    pass


class ActionRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or Path(__file__).with_name("actions.yaml"))
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
        self._actions = {action.id: action for action in actions}
        self._examples: dict[str, ArtifactRef] = {}

    def set_examples(self, examples: dict[str, ArtifactRef]) -> None:
        """Bind registry example keys to normal, draggable Artifacts."""

        self._examples = dict(examples)

    def get(self, action_id: str) -> ActionDescriptor | None:
        return self._actions.get(action_id)

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
        if action.id == "sprite.export":
            data.update(available=True, unavailable_reason=None, models=[])
            return ActionDescriptor.model_validate(data)
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
            models = [
                ModelOption(
                    id=f"{runtime['id']}:{recipe.id}",
                    label=f"{runtime['label']} · {recipe.label}",
                    runtime_id=runtime["id"],
                    family=recipe.family,
                    modes=recipe.modes,
                ).model_dump(mode="json")
                for recipe in compatible
            ]
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
                if any(item not in allowed for item in selected):
                    raise RegistryError(f"{key} contains an unsupported option")
            if isinstance(value, (int, float)):
                if control.min is not None and value < control.min:
                    raise RegistryError(f"{key} is below minimum {control.min}")
                if control.max is not None and value > control.max:
                    raise RegistryError(f"{key} is above maximum {control.max}")

    @staticmethod
    def defaults(action: ActionDescriptor) -> dict[str, Any]:
        return {control.id: control.default for control in action.controls}
