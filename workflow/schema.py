"""Declarative workflow schema: parse + validate a YAML workflow into a graph.

A workflow YAML looks like:

    id: single_sprite
    capability: single_sprite
    default: true
    description: One prompt -> a single sprite pair.
    params:                      # workflow-level params, exposed to callers
      prompt:   { type: string, default: "" }
      width:    { type: int,    default: 512 }
      pixelize: { type: bool,   default: true }
      normal:   { type: bool,   default: true }
    nodes:
      - id: gen
        component: text2img
        params: { prompt: ${prompt}, width: ${width}, height: ${width} }
      - id: px
        component: pixelize
        inputs: { image: gen.image }
        params: { target_width: 64, target_height: 64 }
      ...
    output: pair.pair            # node_id.port that is the workflow result

Connections are `node_id.port` strings. `${param}` references a workflow param.
Topology is authored here (by devs/agents), never by end users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from .component import ComponentRegistry


@dataclass
class NodeSpec:
    id: str
    component: str
    inputs: dict[str, str] = field(default_factory=dict)  # port -> "node.port"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowSpec:
    id: str
    capability: str
    nodes: list[NodeSpec]
    output: str  # "node_id.port"
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    default: bool = False
    description: str = ""

    @property
    def output_node(self) -> str:
        return self.output.split(".", 1)[0]

    @property
    def output_port(self) -> str:
        return self.output.split(".", 1)[1]


class WorkflowValidationError(ValueError):
    pass


def load_workflow_yaml(text: str) -> WorkflowSpec:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise WorkflowValidationError("workflow must be a mapping")
    try:
        nodes = [
            NodeSpec(
                id=n["id"],
                component=n["component"],
                inputs=dict(n.get("inputs", {})),
                params=dict(n.get("params", {})),
            )
            for n in data["nodes"]
        ]
        spec = WorkflowSpec(
            id=data["id"],
            capability=data.get("capability", data["id"]),
            nodes=nodes,
            output=data["output"],
            params=dict(data.get("params", {})),
            default=bool(data.get("default", False)),
            description=data.get("description", ""),
        )
    except KeyError as e:
        raise WorkflowValidationError(f"missing required key: {e}") from e
    return spec


def validate_workflow(spec: WorkflowSpec, registry: ComponentRegistry) -> None:
    """Static checks: components exist, ports resolve, types are compatible, the
    graph is a DAG, and the declared output exists."""
    node_by_id = {n.id: n for n in spec.nodes}
    if len(node_by_id) != len(spec.nodes):
        raise WorkflowValidationError("duplicate node id")

    for node in spec.nodes:
        if not registry.has(node.component):
            raise WorkflowValidationError(f"node {node.id}: unknown component {node.component}")
        comp = registry.get(node.component)

        # Every declared input port must exist on the component; every required
        # input port must be connected (unless it can come from an upstream).
        for port_name, ref in node.inputs.items():
            if comp.input_port(port_name) is None:
                raise WorkflowValidationError(
                    f"node {node.id}: component {node.component} has no input '{port_name}'"
                )
            _validate_ref(node, port_name, ref, node_by_id, registry, comp)

        for port in comp.inputs:
            if port.required and port.name not in node.inputs:
                raise WorkflowValidationError(
                    f"node {node.id}: required input '{port.name}' is not connected"
                )

    # Output must resolve.
    if spec.output_node not in node_by_id:
        raise WorkflowValidationError(f"output references unknown node {spec.output_node}")
    out_comp = registry.get(node_by_id[spec.output_node].component)
    if out_comp.output_port(spec.output_port) is None:
        raise WorkflowValidationError(
            f"output port {spec.output} does not exist on {spec.output_node}"
        )

    _check_acyclic(spec, node_by_id)


def _validate_ref(node, port_name, ref, node_by_id, registry, comp) -> None:
    if "." not in ref:
        raise WorkflowValidationError(f"node {node.id}: input ref '{ref}' must be node.port")
    up_id, up_port = ref.split(".", 1)
    if up_id not in node_by_id:
        raise WorkflowValidationError(f"node {node.id}: input from unknown node {up_id}")
    up_comp = registry.get(node_by_id[up_id].component)
    up = up_comp.output_port(up_port)
    if up is None:
        raise WorkflowValidationError(f"node {node.id}: {up_id} has no output {up_port}")
    want = comp.input_port(port_name)
    if want and want.kind != "any" and up.kind != "any" and want.kind != up.kind:
        raise WorkflowValidationError(
            f"node {node.id}: type mismatch on '{port_name}': {up.kind} -> {want.kind}"
        )


def _check_acyclic(spec: WorkflowSpec, node_by_id: dict[str, NodeSpec]) -> None:
    color: dict[str, int] = {}  # 0=unvisited,1=in-stack,2=done

    def visit(nid: str) -> None:
        color[nid] = 1
        for ref in node_by_id[nid].inputs.values():
            up = ref.split(".", 1)[0]
            if color.get(up, 0) == 1:
                raise WorkflowValidationError(f"cycle detected at {nid} -> {up}")
            if color.get(up, 0) == 0:
                visit(up)
        color[nid] = 2

    for node in spec.nodes:
        if color.get(node.id, 0) == 0:
            visit(node.id)


def topo_order(spec: WorkflowSpec) -> list[str]:
    """Return node ids in dependency order (inputs before consumers)."""
    node_by_id = {n.id: n for n in spec.nodes}
    order: list[str] = []
    seen: set[str] = set()

    def visit(nid: str) -> None:
        if nid in seen:
            return
        for ref in node_by_id[nid].inputs.values():
            visit(ref.split(".", 1)[0])
        seen.add(nid)
        order.append(nid)

    for node in spec.nodes:
        visit(node.id)
    return order
