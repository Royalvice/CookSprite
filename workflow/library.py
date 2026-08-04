"""Workflow library: discover the built-in workflow YAML files and index them
by capability. Each workflow file doubles as a reference example agents can copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .schema import WorkflowSpec, load_workflow_yaml

WORKFLOW_DIR = Path(__file__).parent / "workflows"


@dataclass
class Capability:
    id: str
    workflows: list[WorkflowSpec] = field(default_factory=list)

    @property
    def default_workflow(self) -> WorkflowSpec:
        for wf in self.workflows:
            if wf.default:
                return wf
        return self.workflows[0]


class Library:
    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowSpec] = {}
        self._capabilities: dict[str, Capability] = {}

    @classmethod
    def load_builtin(cls) -> "Library":
        lib = cls()
        if WORKFLOW_DIR.exists():
            for path in sorted(WORKFLOW_DIR.glob("*.yaml")):
                lib.add(load_workflow_yaml(path.read_text()))
        return lib

    def add(self, spec: WorkflowSpec) -> None:
        self._workflows[spec.id] = spec
        cap = self._capabilities.setdefault(spec.capability, Capability(id=spec.capability))
        cap.workflows.append(spec)

    def capabilities(self) -> list[Capability]:
        return list(self._capabilities.values())

    def get_capability(self, cap_id: str) -> Capability:
        if cap_id not in self._capabilities:
            raise KeyError(f"unknown capability: {cap_id}")
        return self._capabilities[cap_id]

    def resolve(self, capability: str, workflow: str | None = None) -> WorkflowSpec:
        """Pick a workflow: named if given, else the capability's default."""
        cap = self.get_capability(capability)
        if workflow is None:
            return cap.default_workflow
        for wf in cap.workflows:
            if wf.id == workflow:
                return wf
        raise KeyError(f"capability {capability} has no workflow {workflow}")

    def get_workflow(self, workflow_id: str) -> WorkflowSpec:
        if workflow_id not in self._workflows:
            raise KeyError(f"unknown workflow: {workflow_id}")
        return self._workflows[workflow_id]
