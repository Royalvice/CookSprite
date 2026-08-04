"""Component model: the typed nodes a workflow is built from.

Two kinds of component:
  - Tool: deterministic, local, model-free (pixelize, crop, normal-estimate...).
  - Op:   an inference atom that calls the backend /infer API.

Both share the same Component interface so the runner treats them uniformly.
Topology is authored by developers/agents (YAML), never by humans in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .types import Artifact


@dataclass
class Port:
    """A typed input or output port on a component."""

    name: str
    kind: str  # one of ARTIFACT_KINDS, or "any"
    required: bool = True


@dataclass
class ParamSpec:
    """A user/agent-settable parameter for a component."""

    name: str
    type: str  # "string" | "int" | "bool" | "float"
    default: Any = None
    label: str | None = None


class RunContext(Protocol):
    """What a component may use while running: progress reporting + the
    inference client (for Ops). Tools only touch progress."""

    def progress(self, fraction: float, message: str) -> None: ...

    def infer(self, op: str, model_id: str, inputs: dict, params: dict) -> Any: ...


ComponentFn = Callable[[dict[str, Artifact], dict[str, Any], RunContext], dict[str, Artifact]]


@dataclass
class Component:
    """A registered, reusable node. `fn` maps (typed inputs, params, ctx) to
    typed outputs."""

    id: str
    category: str  # "tool" | "op"
    inputs: list[Port]
    outputs: list[Port]
    params: list[ParamSpec]
    fn: ComponentFn
    description: str = ""

    def output_port(self, name: str) -> Port | None:
        for p in self.outputs:
            if p.name == name:
                return p
        return None

    def input_port(self, name: str) -> Port | None:
        for p in self.inputs:
            if p.name == name:
                return p
        return None


class ComponentRegistry:
    """Holds every known Component. Tools and Ops register into one registry so
    workflows can reference them by id."""

    def __init__(self) -> None:
        self._components: dict[str, Component] = {}

    def register(self, component: Component) -> Component:
        if component.id in self._components:
            raise ValueError(f"component already registered: {component.id}")
        self._components[component.id] = component
        return component

    def get(self, component_id: str) -> Component:
        if component_id not in self._components:
            raise KeyError(f"unknown component: {component_id}")
        return self._components[component_id]

    def has(self, component_id: str) -> bool:
        return component_id in self._components

    def all(self) -> list[Component]:
        return list(self._components.values())


# Module-level default registry. Tool/Op modules populate it on import.
REGISTRY = ComponentRegistry()


def tool(
    id: str,
    inputs: list[Port],
    outputs: list[Port],
    params: list[ParamSpec] | None = None,
    description: str = "",
) -> Callable[[ComponentFn], ComponentFn]:
    """Decorator: register a deterministic Tool component."""

    def wrap(fn: ComponentFn) -> ComponentFn:
        REGISTRY.register(
            Component(
                id=id,
                category="tool",
                inputs=inputs,
                outputs=outputs,
                params=params or [],
                fn=fn,
                description=description,
            )
        )
        return fn

    return wrap


def op(
    id: str,
    inputs: list[Port],
    outputs: list[Port],
    params: list[ParamSpec] | None = None,
    description: str = "",
) -> Callable[[ComponentFn], ComponentFn]:
    """Decorator: register an inference Op component (calls backend /infer)."""

    def wrap(fn: ComponentFn) -> ComponentFn:
        REGISTRY.register(
            Component(
                id=id,
                category="op",
                inputs=inputs,
                outputs=outputs,
                params=params or [],
                fn=fn,
                description=description,
            )
        )
        return fn

    return wrap
