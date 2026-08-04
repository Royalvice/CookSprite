"""Declarative schema for the two composition layers.

A **workflow** is a flat DAG of tool-nodes — one minimal end-to-end route. It
never contains another workflow. It may declare external artifact `inputs` so a
task can feed it upstream results:

    id: spritify
    description: clean a raw image into a sprite pair.
    inputs:                      # external artifact ports, by kind
      src: image
    params:
      target: { type: int, default: 64 }
    nodes:
      - id: px
        tool: pixelize
        inputs: { image: $in.src }        # $in.<name> = a workflow input
        params: { target_width: ${target}, target_height: ${target} }
      - id: pair
        tool: make_sprite_pair
        inputs: { diffuse: px.image }     # node.port = an upstream node output
    output: pair.pair            # node_id.port that is the workflow result

A **task** is a DAG of workflow-nodes — the unit a frontend triggers. Any goal
that needs two or more workflows is a task; a simple one mounts a single
workflow. Each node runs one workflow chosen from `candidates` (default first),
and nodes wire one workflow's output into another's input:

    id: single_sprite
    description: one prompt -> a finished sprite pair.
    params:
      prompt: { type: string, default: "" }
    nodes:
      - id: gen
        workflow: generate_image          # the chosen (default) workflow
        params: { prompt: ${prompt} }     # task param -> workflow param
      - id: spr
        workflow: spritify
        inputs: { src: gen }              # feed node `gen`'s output into `src`
    output: spr                  # task result = a node's workflow output

`${param}` references a param of the enclosing spec. Topology is authored here
(by devs/agents), never by end users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from .tool import ToolRegistry


@dataclass
class NodeSpec:
    id: str
    tool: str
    inputs: dict[str, str] = field(default_factory=dict)  # port -> "node.port" | "$in.name"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowSpec:
    id: str
    nodes: list[NodeSpec]
    output: str  # "node_id.port"
    inputs: dict[str, str] = field(default_factory=dict)  # external port name -> artifact kind
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    description: str = ""

    @property
    def output_node(self) -> str:
        return self.output.split(".", 1)[0]

    @property
    def output_port(self) -> str:
        return self.output.split(".", 1)[1]


@dataclass
class TaskNodeSpec:
    id: str
    candidates: list[str]  # workflow ids; [0] is the default choice
    inputs: dict[str, str] = field(default_factory=dict)  # workflow-input name -> upstream node id
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def workflow(self) -> str:
        return self.candidates[0]


@dataclass
class TaskSpec:
    id: str
    nodes: list[TaskNodeSpec]
    output: str  # a task node id (its workflow's output is the task result)
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    description: str = ""


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
                tool=n["tool"],
                inputs=dict(n.get("inputs", {})),
                params=dict(n.get("params", {})),
            )
            for n in data["nodes"]
        ]
        spec = WorkflowSpec(
            id=data["id"],
            nodes=nodes,
            output=data["output"],
            inputs=dict(data.get("inputs", {})),
            params=dict(data.get("params", {})),
            description=data.get("description", ""),
        )
    except KeyError as e:
        raise WorkflowValidationError(f"missing required key: {e}") from e
    return spec


def load_task_yaml(text: str) -> TaskSpec:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise WorkflowValidationError("task must be a mapping")
    try:
        nodes = []
        for n in data["nodes"]:
            # `workflow:` (one) or `candidates:` (list, [0] default) — one required.
            if "candidates" in n:
                cands = list(n["candidates"])
            elif "workflow" in n:
                cands = [n["workflow"]]
            else:
                raise WorkflowValidationError(
                    f"task node {n.get('id')}: needs `workflow` or `candidates`"
                )
            if not cands:
                raise WorkflowValidationError(f"task node {n['id']}: empty candidates")
            nodes.append(
                TaskNodeSpec(
                    id=n["id"],
                    candidates=cands,
                    inputs=dict(n.get("inputs", {})),
                    params=dict(n.get("params", {})),
                )
            )
        spec = TaskSpec(
            id=data["id"],
            nodes=nodes,
            output=data["output"],
            params=dict(data.get("params", {})),
            description=data.get("description", ""),
        )
    except KeyError as e:
        raise WorkflowValidationError(f"missing required key: {e}") from e
    return spec


def validate_workflow(spec: WorkflowSpec, registry: ToolRegistry) -> None:
    """Static checks: tools exist, ports resolve, types are compatible, the
    graph is a DAG, and the declared output exists."""
    node_by_id = {n.id: n for n in spec.nodes}
    if len(node_by_id) != len(spec.nodes):
        raise WorkflowValidationError("duplicate node id")

    for node in spec.nodes:
        if not registry.has(node.tool):
            raise WorkflowValidationError(f"node {node.id}: unknown tool {node.tool}")
        comp = registry.get(node.tool)

        # Every declared input port must exist on the tool; every required
        # input port must be connected (unless it can come from an upstream).
        for port_name, ref in node.inputs.items():
            if comp.input_port(port_name) is None:
                raise WorkflowValidationError(
                    f"node {node.id}: tool {node.tool} has no input '{port_name}'"
                )
            _validate_ref(spec, node, port_name, ref, node_by_id, registry, comp)

        for port in comp.inputs:
            if port.required and port.name not in node.inputs:
                raise WorkflowValidationError(
                    f"node {node.id}: required input '{port.name}' is not connected"
                )

    # Output must resolve.
    if spec.output_node not in node_by_id:
        raise WorkflowValidationError(f"output references unknown node {spec.output_node}")
    out_comp = registry.get(node_by_id[spec.output_node].tool)
    if out_comp.output_port(spec.output_port) is None:
        raise WorkflowValidationError(
            f"output port {spec.output} does not exist on {spec.output_node}"
        )

    _check_acyclic(spec, node_by_id)


def _validate_ref(spec, node, port_name, ref, node_by_id, registry, comp) -> None:
    want = comp.input_port(port_name)
    # `$in.<name>` — a workflow external input.
    if ref.startswith("$in."):
        in_name = ref[len("$in."):]
        if in_name not in spec.inputs:
            raise WorkflowValidationError(
                f"node {node.id}: '{port_name}' references undeclared workflow input '{in_name}'"
            )
        up_kind = spec.inputs[in_name]
        if want and want.kind != "any" and up_kind != "any" and want.kind != up_kind:
            raise WorkflowValidationError(
                f"node {node.id}: type mismatch on '{port_name}': $in.{in_name} ({up_kind}) -> {want.kind}"
            )
        return
    # `<node>.<port>` — an upstream node output.
    if "." not in ref:
        raise WorkflowValidationError(f"node {node.id}: input ref '{ref}' must be node.port or $in.name")
    up_id, up_port = ref.split(".", 1)
    if up_id not in node_by_id:
        raise WorkflowValidationError(f"node {node.id}: input from unknown node {up_id}")
    up_comp = registry.get(node_by_id[up_id].tool)
    up = up_comp.output_port(up_port)
    if up is None:
        raise WorkflowValidationError(f"node {node.id}: {up_id} has no output {up_port}")
    if want and want.kind != "any" and up.kind != "any" and want.kind != up.kind:
        raise WorkflowValidationError(
            f"node {node.id}: type mismatch on '{port_name}': {up.kind} -> {want.kind}"
        )


def _check_acyclic(spec: WorkflowSpec, node_by_id: dict[str, NodeSpec]) -> None:
    color: dict[str, int] = {}  # 0=unvisited,1=in-stack,2=done

    def visit(nid: str) -> None:
        color[nid] = 1
        for ref in node_by_id[nid].inputs.values():
            if ref.startswith("$in."):
                continue
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
            if ref.startswith("$in."):
                continue
            visit(ref.split(".", 1)[0])
        seen.add(nid)
        order.append(nid)

    for node in spec.nodes:
        visit(node.id)
    return order


def validate_task(
    task: TaskSpec, workflows: dict[str, WorkflowSpec], registry: ToolRegistry
) -> None:
    """Static checks for a task: every candidate workflow exists and validates,
    node wiring targets declared workflow inputs from real upstream nodes, the
    graph is a DAG, and the output node exists."""
    node_by_id = {n.id: n for n in task.nodes}
    if len(node_by_id) != len(task.nodes):
        raise WorkflowValidationError(f"task {task.id}: duplicate node id")

    for node in task.nodes:
        if not node.candidates:
            raise WorkflowValidationError(f"task node {node.id}: no workflow candidate")
        for wf_id in node.candidates:
            if wf_id not in workflows:
                raise WorkflowValidationError(
                    f"task node {node.id}: unknown workflow '{wf_id}'"
                )
            validate_workflow(workflows[wf_id], registry)
        wf = workflows[node.workflow]  # wiring is checked against the default choice
        for in_name, up_id in node.inputs.items():
            if in_name not in wf.inputs:
                raise WorkflowValidationError(
                    f"task node {node.id}: workflow '{wf.id}' has no input '{in_name}'"
                )
            if up_id not in node_by_id:
                raise WorkflowValidationError(
                    f"task node {node.id}: input '{in_name}' from unknown node '{up_id}'"
                )
        # Every workflow input must be wired from an upstream task node.
        for in_name in wf.inputs:
            if in_name not in node.inputs:
                raise WorkflowValidationError(
                    f"task node {node.id}: workflow input '{in_name}' is not connected"
                )

    if task.output not in node_by_id:
        raise WorkflowValidationError(
            f"task {task.id}: output references unknown node '{task.output}'"
        )
    _check_acyclic_task(task, node_by_id)


def topo_order_task(task: TaskSpec) -> list[str]:
    """Task node ids in dependency order (upstream before consumers)."""
    node_by_id = {n.id: n for n in task.nodes}
    order: list[str] = []
    seen: set[str] = set()

    def visit(nid: str) -> None:
        if nid in seen:
            return
        for up_id in node_by_id[nid].inputs.values():
            visit(up_id)
        seen.add(nid)
        order.append(nid)

    for node in task.nodes:
        visit(node.id)
    return order


def _check_acyclic_task(task: TaskSpec, node_by_id: dict[str, TaskNodeSpec]) -> None:
    color: dict[str, int] = {}

    def visit(nid: str) -> None:
        color[nid] = 1
        for up_id in node_by_id[nid].inputs.values():
            if color.get(up_id, 0) == 1:
                raise WorkflowValidationError(f"task cycle detected at {nid} -> {up_id}")
            if color.get(up_id, 0) == 0:
                visit(up_id)
        color[nid] = 2

    for node in task.nodes:
        if color.get(node.id, 0) == 0:
            visit(node.id)
