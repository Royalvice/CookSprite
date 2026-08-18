"""Lower typed CookSprite graphs into one private ComfyUI API-format prompt."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .bridge import ArtifactBridge
from .catalog import builtin_tools
from .domain import PersistableType, TaskRevision, ToolDescriptor, ValueRef, WorkflowRevision
from .execution import ExecutionPlan, PlanBuilder
from .tool_packages import tool_packages


class CompileError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactBinding:
    id: str


class Compiler(PlanBuilder):
    def __init__(
        self,
        tools: list[ToolDescriptor],
        bridge: ArtifactBridge | None = None,
        run_id: str | None = None,
        sealed_graphs: dict[str, dict[str, Any]] | None = None,
    ):
        super().__init__(bridge, run_id)
        self.tools = {t.id: t for t in tools}
        self.lowerings = tool_packages.lowerings()
        self.sealed_graphs = sealed_graphs or {}

    def _tool(self, id: str) -> ToolDescriptor:
        if id not in self.tools:
            raise CompileError(f"unknown tool: {id}")
        return self.tools[id]

    def _node(self, class_type: str, inputs: dict[str, Any]) -> str:
        return self.add(class_type, inputs)

    def _sealed(self, tool_id: str, values: dict[str, Any]) -> list[Any]:
        spec = self.sealed_graphs.get(tool_id)
        if not spec:
            raise CompileError(f"{tool_id}: sealed graph implementation is missing")
        graph = copy.deepcopy(spec.get("workflow") or {})
        slots = spec.get("slots") or {}
        output = spec.get("output")
        if not isinstance(graph, dict) or not isinstance(output, list) or len(output) != 2:
            raise CompileError(f"{tool_id}: invalid sealed graph")
        prefix = f"{self.node_prefix}_sealed_{self.sequence + 1}_"
        mapping = {str(node_id): prefix + str(node_id) for node_id in graph}
        for node_id, node in graph.items():
            inputs = node.get("inputs") or {}
            for key, value in list(inputs.items()):
                if isinstance(value, list) and len(value) == 2 and str(value[0]) in mapping:
                    inputs[key] = [mapping[str(value[0])], value[1]]
            self.graph[mapping[str(node_id)]] = node
        for name, value in values.items():
            address = slots.get(name)
            if not address:
                continue
            node_id, separator, input_name = str(address).partition(".")
            if not separator or node_id not in mapping:
                raise CompileError(f"{tool_id}: invalid sealed slot {name}")
            self.graph[mapping[node_id]].setdefault("inputs", {})[input_name] = value
        output_node = mapping.get(str(output[0]))
        if not output_node:
            raise CompileError(f"{tool_id}: sealed output node is missing")
        self.sequence += len(mapping)
        return [output_node, int(output[1])]

    def _value(self, ref: ValueRef, bindings: dict[str, Any]) -> Any:
        if ref.input is not None:
            if ref.input not in bindings:
                raise CompileError(f"unbound input: {ref.input}")
            return bindings[ref.input]
        if ref.node is not None:
            key = f"{ref.node}.{ref.output}"
            if key not in bindings:
                raise CompileError(f"unknown node output: {key}")
            return bindings[key]
        if ref.asset is not None:
            return ref.asset.asset_id
        if ref.artifact is not None:
            return ArtifactBinding(ref.artifact)
        return ref.literal

    def _port_value(self, value: Any, port_type: str) -> Any:
        if not isinstance(value, ArtifactBinding):
            return value
        if not self.bridge or not self.run_id:
            raise CompileError("artifact references require a signed runtime bridge")
        if port_type == "Video":
            return self.bridge.download_url(value.id, self.run_id)
        try:
            image_ref = self.load_artifact(value.id)
            if port_type == "Mask":
                mask_ref = self.mask_for_image(image_ref)
                if mask_ref is None:
                    raise CompileError(f"artifact {value.id} has no alpha mask")
                return mask_ref
            return image_ref
        except ValueError as exc:
            raise CompileError(str(exc)) from exc

    def _workflow_output_types(self, wf: WorkflowRevision) -> dict[str, PersistableType]:
        nodes = {node.id: node for node in wf.nodes}
        result: dict[str, PersistableType] = {}
        for name, ref in wf.outputs.items():
            node = nodes.get(ref.node or "")
            tool = self._tool(node.tool) if node else None
            port = next(
                (item for item in (tool.outputs if tool else []) if item.name == ref.output),
                None,
            )
            if not port or not port.persistable:
                raise CompileError(f"{name}: workflow output is not persistable")
            result[name] = port.type
        return result

    def workflow(self, wf: WorkflowRevision, external: dict[str, Any]) -> dict[str, Any]:
        bindings = dict(external)
        seen = set()
        for n in wf.nodes:
            if n.id in seen:
                raise CompileError(f"duplicate node id: {n.id}")
            seen.add(n.id)
            tool = self._tool(n.tool)
            ports = {p.name: p for p in tool.inputs}
            data = {}
            for name, ref in n.inputs.items():
                if name not in ports:
                    raise CompileError(f"{n.id}.{name}: not a declared input")
                value = self._port_value(self._value(ref, bindings), ports[name].type)
                data[name] = value
                if ports[name].type in {"Image", "ImageBatch"} and "mask" in ports:
                    mask = self.mask_for_image(value)
                    if mask is not None and "mask" not in data:
                        data["mask"] = mask
            for p in tool.inputs:
                if p.required and p.name not in data:
                    raise CompileError(f"{n.id}.{p.name}: missing required input")
            data.update({name: self._value(ref, bindings) for name, ref in n.params.items()})
            if tool.id in self.sealed_graphs:
                value = self._sealed(tool.id, data)
                for index, port in enumerate(tool.outputs):
                    bindings[f"{n.id}.{port.name}"] = value if index == 0 else [value[0], index]
                continue
            lowered = self.lowerings.get(tool.id, tool.id.removeprefix("comfy."))
            if not lowered:
                raise CompileError(f"{tool.id}: no Comfy lowering")
            cid = self._node(lowered, data)
            output_refs: dict[str, list[Any]] = {}
            for index, port in enumerate(tool.outputs):
                output_refs[port.name] = [cid, index]
                bindings[f"{n.id}.{port.name}"] = output_refs[port.name]
            mask_ref = next(
                (output_refs[port.name] for port in tool.outputs if port.type == "Mask"),
                None,
            )
            if mask_ref is not None:
                for port in tool.outputs:
                    if port.type in {"Image", "ImageBatch"}:
                        self.register_image_mask(output_refs[port.name], mask_ref)
        result = {name: self._value(ref, bindings) for name, ref in wf.outputs.items()}
        return result

    def compile_workflow(self, wf: WorkflowRevision, inputs: dict[str, ValueRef]) -> ExecutionPlan:
        external = {k: self._value(v, {}) for k, v in inputs.items()}
        outputs = self.workflow(wf, external)
        self._sink(outputs, self._workflow_output_types(wf), self._output_sources(wf, external))
        return self.build()

    def _output_sources(self, wf: WorkflowRevision, external: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, ref in wf.output_sources.items():
            value = self._value(ref, external)
            if isinstance(value, ArtifactBinding):
                result[name] = value.id
        return result

    def compile_task(
        self,
        task: TaskRevision,
        workflows: dict[tuple[str, int], WorkflowRevision],
        inputs: dict[str, ValueRef],
        selection: dict[str, int],
    ) -> ExecutionPlan:
        bindings = {k: self._value(v, {}) for k, v in inputs.items()}
        binding_types: dict[str, PersistableType] = {}
        binding_sources: dict[str, str] = {}
        for call in task.nodes:
            rev = selection.get(call.id, call.candidates[0])
            if rev not in call.candidates:
                raise CompileError(f"{call.id}: selected revision is not a candidate")
            wf = workflows.get((call.workflow_id, rev))
            if not wf:
                raise CompileError(f"{call.id}: workflow {call.workflow_id}@{rev} missing")
            local = {name: self._value(ref, bindings) for name, ref in call.inputs.items()}
            outputs = self.workflow(wf, local)
            output_types = self._workflow_output_types(wf)
            output_sources = self._output_sources(wf, local)
            for name, value in outputs.items():
                bindings[f"{call.id}.{name}"] = value
                binding_types[f"{call.id}.{name}"] = output_types[name]
                if name in output_sources:
                    binding_sources[f"{call.id}.{name}"] = output_sources[name]
        outputs = {name: self._value(ref, bindings) for name, ref in task.outputs.items()}
        output_types = {}
        output_sources = {}
        for name, ref in task.outputs.items():
            key = f"{ref.node}.{ref.output}"
            if key not in binding_types:
                raise CompileError(f"{name}: task output is not a persistable workflow output")
            output_types[name] = binding_types[key]
            if key in binding_sources:
                output_sources[name] = binding_sources[key]
        self._sink(outputs, output_types, output_sources)
        return self.build()

    def _sink(
        self,
        outputs: dict[str, Any],
        output_types: dict[str, PersistableType],
        output_sources: dict[str, str] | None = None,
    ) -> None:
        output_sources = output_sources or {}
        for name, value in outputs.items():
            try:
                self.store_artifact(
                    value,
                    output_types[name],
                    source_artifact=output_sources.get(name, ""),
                )
            except ValueError as exc:
                raise CompileError(str(exc)) from exc


def all_tools(dynamic: list[ToolDescriptor] | None = None) -> list[ToolDescriptor]:
    return builtin_tools() + list(dynamic or [])
