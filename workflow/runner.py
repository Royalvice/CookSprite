"""Workflow runner: execute a validated WorkflowSpec.

Resolves workflow-level params (with caller overrides + workspace config
defaults), substitutes `${param}` references in node params, runs nodes in
topological order (deterministic tools run locally, inference tools call the
inference client via the RunContext), and returns the declared output artifact.

Progress is reported per node so the frontend can show a live bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .tool import Tool, ToolRegistry, RunContext
from .schema import (
    TaskSpec,
    WorkflowSpec,
    topo_order,
    topo_order_task,
    validate_task,
    validate_workflow,
)
from .types import Artifact


class InferenceClient:
    """Abstract inference transport. `infer` submits an op and returns the
    result dict `{"outputs": [...], "meta": {...}}` synchronously (the backend's
    async job/poll is handled inside the concrete client)."""

    def infer(self, op: str, model_id: str, inputs: dict, params: dict) -> dict:
        raise NotImplementedError


ProgressFn = Callable[[float, str], None]


@dataclass
class _Ctx(RunContext):
    client: InferenceClient
    _report: ProgressFn
    _node_base: float = 0.0
    _node_span: float = 1.0

    def progress(self, fraction: float, message: str) -> None:
        overall = self._node_base + self._node_span * max(0.0, min(1.0, fraction))
        self._report(overall, message)

    def infer(self, op: str, model_id: str, inputs: dict, params: dict) -> Any:
        return self.client.infer(op, model_id, inputs, params)


def resolve_params(
    spec: WorkflowSpec | TaskSpec, overrides: dict[str, Any], config_defaults: dict[str, Any]
) -> dict[str, Any]:
    """Params, layered: schema default < workspace config < caller. Works for a
    workflow or a task (both declare `.params`)."""
    resolved: dict[str, Any] = {}
    for name, decl in spec.params.items():
        if name in overrides:
            resolved[name] = overrides[name]
        elif name in config_defaults:
            resolved[name] = config_defaults[name]
        else:
            resolved[name] = decl.get("default")
    # Allow overrides not declared in schema (e.g. model_id passthrough).
    for name, value in overrides.items():
        resolved.setdefault(name, value)
    return resolved


def _substitute(value: Any, params: dict[str, Any]) -> Any:
    """Replace a `${param}` string with the param value; recurse into dict/list."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        key = value[2:-1]
        if key not in params:
            raise KeyError(f"workflow references undefined param '{key}'")
        return params[key]
    if isinstance(value, dict):
        return {k: _substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params) for v in value]
    return value


def run_workflow(
    spec: WorkflowSpec,
    registry: ToolRegistry,
    client: InferenceClient,
    *,
    params: dict[str, Any] | None = None,
    inputs: dict[str, Artifact] | None = None,
    config_defaults: dict[str, Any] | None = None,
    on_progress: ProgressFn | None = None,
) -> Artifact:
    validate_workflow(spec, registry)
    wf_params = resolve_params(spec, params or {}, config_defaults or {})
    ext_inputs = inputs or {}
    missing = [name for name in spec.inputs if name not in ext_inputs]
    if missing:
        raise KeyError(f"workflow {spec.id}: missing external inputs {missing}")
    report: ProgressFn = on_progress or (lambda f, m: None)

    order = topo_order(spec)
    node_by_id = {n.id: n for n in spec.nodes}
    outputs: dict[str, dict[str, Artifact]] = {}

    total = len(order)
    for idx, nid in enumerate(order):
        node = node_by_id[nid]
        comp: Tool = registry.get(node.tool)

        # Gather typed inputs: `$in.<name>` = external input, else upstream output.
        node_inputs: dict[str, Artifact] = {}
        for port_name, ref in node.inputs.items():
            if ref.startswith("$in."):
                node_inputs[port_name] = ext_inputs[ref[len("$in."):]]
            else:
                up_id, up_port = ref.split(".", 1)
                node_inputs[port_name] = outputs[up_id][up_port]

        node_params = _substitute(node.params, wf_params)

        ctx = _Ctx(
            client=client,
            _report=report,
            _node_base=idx / total,
            _node_span=1.0 / total,
        )
        ctx.progress(0.0, f"{comp.id} ({comp.kind})")
        result = comp.fn(node_inputs, node_params, ctx)
        outputs[nid] = result

    return outputs[spec.output_node][spec.output_port]


def run_task(
    task: TaskSpec,
    workflows: dict[str, WorkflowSpec],
    registry: ToolRegistry,
    client: InferenceClient,
    *,
    params: dict[str, Any] | None = None,
    choices: dict[str, str] | None = None,
    config_defaults: dict[str, Any] | None = None,
    on_progress: ProgressFn | None = None,
) -> Artifact:
    """Run a task: execute each workflow-node in dependency order, wiring one
    node's workflow output into a downstream node's declared input. `choices`
    picks a non-default candidate per node id."""
    validate_task(task, workflows, registry)
    task_params = resolve_params(task, params or {}, config_defaults or {})
    picks = choices or {}
    report: ProgressFn = on_progress or (lambda f, m: None)

    order = topo_order_task(task)
    node_by_id = {n.id: n for n in task.nodes}
    node_output: dict[str, Artifact] = {}

    total = len(order)
    for idx, nid in enumerate(order):
        node = node_by_id[nid]
        wf_id = picks.get(nid, node.workflow)
        if wf_id not in node.candidates:
            raise KeyError(f"task node {nid}: '{wf_id}' is not a candidate")
        wf = workflows[wf_id]

        # Feed upstream workflow outputs into this workflow's external inputs.
        wf_inputs = {name: node_output[up_id] for name, up_id in node.inputs.items()}
        # Task params flow down to workflow params by name.
        wf_pass = {k: v for k, v in task_params.items()}
        wf_pass.update(_substitute(node.params, task_params))

        base, span = idx / total, 1.0 / total
        node_output[nid] = run_workflow(
            wf,
            registry,
            client,
            params=wf_pass,
            inputs=wf_inputs,
            config_defaults=config_defaults,
            on_progress=lambda f, m, b=base, s=span: report(b + s * f, m),
        )

    return node_output[task.output]
