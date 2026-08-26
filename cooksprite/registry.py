"""Read-only Action registry shared by API, Web, CLI, and agent harnesses."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .domain import ActionDescriptor, ArtifactRef, ModelOption, ToolDescriptor
from .recipes import Recipe, supports
from .tool_packages import ToolPackageRegistry, tool_packages

_DEFAULT_REGISTRY_PATH = Path(__file__).with_name("actions.yaml")


def _registered_action_ids(path: Path = _DEFAULT_REGISTRY_PATH) -> tuple[str, ...]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return tuple(str(item["id"]) for item in raw.get("actions", []))


ACTION_IDS = _registered_action_ids()


class RegistryError(ValueError):
    pass


class CookSpriteRegistry:
    """Single source of truth for product Actions and built-in Tool packages."""

    def __init__(
        self,
        path: str | Path | None = None,
        packages: ToolPackageRegistry = tool_packages,
    ):
        self.path = Path(path or _DEFAULT_REGISTRY_PATH)
        self.packages = packages
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if raw.get("schema") != "cooksprite.actions/v1":
            raise RegistryError("unsupported Action registry schema")
        control_sets = raw.get("control_sets") or {}
        action_rows: list[dict[str, Any]] = []
        self._policies: dict[str, dict[str, Any]] = {}
        for item in raw.get("actions", []):
            row = dict(item)
            action_id = str(row["id"])
            self._policies[action_id] = dict(row.pop("execution", {}) or {})
            expanded: list[dict[str, Any]] = []
            for name in row.pop("control_sets", []) or []:
                values = control_sets.get(name)
                if not isinstance(values, list):
                    raise RegistryError(f"{action_id}: unknown control set {name!r}")
                expanded.extend(values)
            row["controls"] = [*expanded, *(row.get("controls") or [])]
            action_rows.append(row)
        actions = [ActionDescriptor.model_validate(item) for item in action_rows]
        ids = tuple(action.id for action in actions)
        if len(ids) != len(set(ids)):
            raise RegistryError("Action ids must be unique")
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

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(self._actions)

    def get(self, action_id: str) -> ActionDescriptor | None:
        return self._actions.get(action_id)

    def execution(self, action_id: str) -> dict[str, Any]:
        return dict(self._policies.get(action_id) or {})

    def recipe_binding(self, action_id: str, values: dict[str, Any]) -> str | None:
        """Return a declared Recipe id for a selector control, if any.

        Recipe selection stays declarative in ``actions.yaml``.  API callers
        do not need model-family branches when a stable Action exposes
        multiple private workflow implementations.
        """

        policy = self.execution(action_id)
        selector = str(policy.get("recipe_selector") or "")
        bindings = policy.get("recipe_bindings") or {}
        if not selector or not isinstance(bindings, dict):
            return None
        selected = values.get(selector)
        if selected is None:
            return None
        recipe_id = bindings.get(str(selected))
        return str(recipe_id) if recipe_id else None

    def mode(self, action_id: str, inputs: dict[str, list[str]]) -> str:
        for rule in self.execution(action_id).get("modes", []):
            if not isinstance(rule, dict) or not rule.get("mode"):
                continue
            input_name = rule.get("when_input")
            if input_name and not inputs.get(str(input_name)):
                continue
            source_kind = rule.get("when_source_kind")
            if source_kind and inputs.get("__source_kind") != [source_kind]:
                continue
            return str(rule["mode"])
        return ""

    def project_type(
        self,
        action_id: str,
        values: dict[str, Any],
        current: str,
    ) -> str:
        policy = self.execution(action_id)
        selected = str(policy.get("project_type") or current)
        for rule in policy.get("project_type_rules", []):
            if not isinstance(rule, dict):
                continue
            conditions = rule.get("when") or {}
            if isinstance(conditions, dict) and all(values.get(key) == value for key, value in conditions.items()):
                selected = str(rule.get("type") or selected)
                break
        return selected

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
        # Prefer a real reference-video adapter whenever the runtime exposes
        # one.  Legacy text/image fallbacks remain visible only on runtimes
        # that have no i2v-capable Recipe at all.
        if action.id == "animation.generate":
            i2v = [recipe for recipe in compatible if "i2v" in recipe.modes]
            if i2v:
                compatible = i2v
        available = ready and bool(compatible)
        if not ready:
            reason = "runtime_not_ready"
        elif not compatible:
            reason = "compatible_recipe_missing"
        else:
            reason = None
        models = []
        if available and runtime:
            groups: dict[str, list[Recipe]] = {}
            for recipe in compatible:
                identity = str(recipe.checkpoint or recipe.id)
                groups.setdefault(identity, []).append(recipe)
            for model_id, group in groups.items():
                representative = group[0]
                models.append(
                    ModelOption(
                        id=f"{runtime['id']}:{model_id}",
                        model_id=model_id,
                        label=f"{runtime['label']} · {_model_identity_label(representative.label)}",
                        runtime_id=runtime["id"],
                        family=representative.family,
                        modes=list(dict.fromkeys(mode for item in group for mode in item.modes)),
                        params_schema=next(
                            (item.params_schema for item in group if item.params_schema), {}
                        ),
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
            if control.type == "color" and (
                not isinstance(value, str) or re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is None
            ):
                raise RegistryError(f"{key} must be a six-digit RGB hex color")
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


__all__ = [
    "ACTION_IDS",
    "CookSpriteRegistry",
    "RegistryError",
]
