"""Tool model: the typed nodes a workflow is built from.

A **tool** is the smallest component that satisfies one minimal function
end-to-end. Every node in a workflow is a tool. A tool is one of two `kind`s:

  - "deterministic": local, model-free (pixelize, crop, normal-estimate, ...).
  - "inference":     calls the backend /infer API for a model op (text2img, ...).

Both kinds share this one interface, so the runner treats them uniformly.
Topology is authored by developers/agents (YAML), never by humans in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .types import Artifact

# The two kinds of tool. Deterministic tools never touch the network; inference
# tools call ctx.infer(). This is a property of a tool, not a separate concept.
KINDS = ("deterministic", "inference")


@dataclass
class Port:
    """A typed input or output port on a tool."""

    name: str
    kind: str  # artifact type: one of ARTIFACT_KINDS, or "any"
    required: bool = True


@dataclass
class ParamSpec:
    """A user/agent-settable parameter for a tool."""

    name: str
    type: str  # "string" | "int" | "bool" | "float"
    default: Any = None
    label: str | None = None


class RunContext(Protocol):
    """What a tool may use while running: progress reporting + the inference
    client. Deterministic tools only touch progress; inference tools call
    infer()."""

    def progress(self, fraction: float, message: str) -> None: ...

    def infer(self, op: str, model_id: str, inputs: dict, params: dict) -> Any: ...


ToolFn = Callable[[dict[str, Artifact], dict[str, Any], RunContext], dict[str, Artifact]]


@dataclass
class Tool:
    """A registered, reusable node. `fn` maps (typed inputs, params, ctx) to
    typed outputs. `kind` is "deterministic" or "inference"."""

    id: str
    kind: str  # one of KINDS
    inputs: list[Port]
    outputs: list[Port]
    params: list[ParamSpec]
    fn: ToolFn
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


class ToolRegistry:
    """Holds every known Tool. Deterministic and inference tools register into
    one registry so workflows can reference them by id."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.id in self._tools:
            raise ValueError(f"tool already registered: {tool.id}")
        self._tools[tool.id] = tool
        return tool

    def get(self, tool_id: str) -> Tool:
        if tool_id not in self._tools:
            raise KeyError(f"unknown tool: {tool_id}")
        return self._tools[tool_id]

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def all(self) -> list[Tool]:
        return list(self._tools.values())


# Module-level default registry. Tool modules populate it on import.
REGISTRY = ToolRegistry()


def tool(
    id: str,
    inputs: list[Port],
    outputs: list[Port],
    params: list[ParamSpec] | None = None,
    kind: str = "deterministic",
    description: str = "",
) -> Callable[[ToolFn], ToolFn]:
    """Decorator: register a tool. `kind` defaults to "deterministic"; pass
    kind="inference" for a tool that calls the backend /infer API."""

    if kind not in KINDS:
        raise ValueError(f"tool {id}: kind must be one of {KINDS}, got {kind!r}")

    def wrap(fn: ToolFn) -> ToolFn:
        REGISTRY.register(
            Tool(
                id=id,
                kind=kind,
                inputs=inputs,
                outputs=outputs,
                params=params or [],
                fn=fn,
                description=description,
            )
        )
        return fn

    return wrap
