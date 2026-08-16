"""Lower typed CookSprite graphs into one private ComfyUI API-format prompt."""

from __future__ import annotations

from typing import Any

from .bridge import ArtifactBridge
from .catalog import builtin_tools
from .domain import PersistableType, TaskRevision, ToolDescriptor, ValueRef, WorkflowRevision
from .execution import ExecutionPlan, PlanBuilder


class CompileError(ValueError):
    pass


LOWERING = {
    "cooksprite.load_artifact": "CS_LoadArtifact",
    "cooksprite.store_artifact": "CS_StoreArtifact",
    "cooksprite.pixelize": "CS_Pixelize",
    "cooksprite.isolate_on_green": "CS_IsolateOnGreen",
    "cooksprite.center_align": "CS_CenterAlign",
    "cooksprite.normal_estimate": "CS_NormalEstimate",
    "cooksprite.make_sprite_pair": "CS_MakeSpritePair",
}


class Compiler(PlanBuilder):
    def __init__(
        self,
        tools: list[ToolDescriptor],
        bridge: ArtifactBridge | None = None,
        run_id: str | None = None,
    ):
        super().__init__(bridge, run_id)
        self.tools = {t.id: t for t in tools}

    def _tool(self, id: str) -> ToolDescriptor:
        if id not in self.tools:
            raise CompileError(f"unknown tool: {id}")
        return self.tools[id]

    def _node(self, class_type: str, inputs: dict[str, Any]) -> str:
        return self.add(class_type, inputs)

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
            try:
                return self.load_artifact(ref.artifact)
            except ValueError as exc:
                raise CompileError(str(exc)) from exc
        return ref.literal

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
                data[name] = self._value(ref, bindings)
            for p in tool.inputs:
                if p.required and p.name not in data:
                    raise CompileError(f"{n.id}.{p.name}: missing required input")
            data.update({name: self._value(ref, bindings) for name, ref in n.params.items()})
            lowered = LOWERING.get(tool.id, tool.id.removeprefix("comfy."))
            if not lowered:
                raise CompileError(f"{tool.id}: no Comfy lowering")
            cid = self._node(lowered, data)
            for index, port in enumerate(tool.outputs):
                bindings[f"{n.id}.{port.name}"] = [cid, index]
        result = {name: self._value(ref, bindings) for name, ref in wf.outputs.items()}
        return result

    def compile_workflow(self, wf: WorkflowRevision, inputs: dict[str, ValueRef]) -> ExecutionPlan:
        external = {k: self._value(v, {}) for k, v in inputs.items()}
        outputs = self.workflow(wf, external)
        self._sink(outputs, self._workflow_output_types(wf))
        return self.build()

    def compile_task(
        self,
        task: TaskRevision,
        workflows: dict[tuple[str, int], WorkflowRevision],
        inputs: dict[str, ValueRef],
        selection: dict[str, int],
    ) -> ExecutionPlan:
        bindings = {k: self._value(v, {}) for k, v in inputs.items()}
        binding_types: dict[str, PersistableType] = {}
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
            for name, value in outputs.items():
                bindings[f"{call.id}.{name}"] = value
                binding_types[f"{call.id}.{name}"] = output_types[name]
        outputs = {name: self._value(ref, bindings) for name, ref in task.outputs.items()}
        output_types = {}
        for name, ref in task.outputs.items():
            key = f"{ref.node}.{ref.output}"
            if key not in binding_types:
                raise CompileError(f"{name}: task output is not a persistable workflow output")
            output_types[name] = binding_types[key]
        self._sink(outputs, output_types)
        return self.build()

    def _sink(
        self,
        outputs: dict[str, Any],
        output_types: dict[str, PersistableType],
    ) -> None:
        for name, value in outputs.items():
            try:
                self.store_artifact(value, output_types[name])
            except ValueError as exc:
                raise CompileError(str(exc)) from exc


def all_tools(dynamic: list[ToolDescriptor] | None = None) -> list[ToolDescriptor]:
    return builtin_tools() + list(dynamic or [])
