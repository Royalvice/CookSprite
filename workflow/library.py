"""Library: discover the built-in workflow and task YAML files.

`workflows/*.yaml` are task-independent, reusable tool-graphs. `tasks/*.yaml`
are the user-facing goals, each a DAG of workflow-nodes. A workflow file doubles
as a reference example agents can copy.
"""

from __future__ import annotations

from pathlib import Path

from .schema import TaskSpec, WorkflowSpec, load_task_yaml, load_workflow_yaml

WORKFLOW_DIR = Path(__file__).parent / "workflows"
TASK_DIR = Path(__file__).parent / "tasks"


class Library:
    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowSpec] = {}
        self._tasks: dict[str, TaskSpec] = {}

    @classmethod
    def load_builtin(cls) -> "Library":
        lib = cls()
        if WORKFLOW_DIR.exists():
            for path in sorted(WORKFLOW_DIR.glob("*.yaml")):
                lib.add_workflow(load_workflow_yaml(path.read_text()))
        if TASK_DIR.exists():
            for path in sorted(TASK_DIR.glob("*.yaml")):
                lib.add_task(load_task_yaml(path.read_text()))
        return lib

    def add_workflow(self, spec: WorkflowSpec) -> None:
        self._workflows[spec.id] = spec

    def add_task(self, spec: TaskSpec) -> None:
        self._tasks[spec.id] = spec

    def workflows(self) -> list[WorkflowSpec]:
        return list(self._workflows.values())

    def workflow_map(self) -> dict[str, WorkflowSpec]:
        return dict(self._workflows)

    def get_workflow(self, workflow_id: str) -> WorkflowSpec:
        if workflow_id not in self._workflows:
            raise KeyError(f"unknown workflow: {workflow_id}")
        return self._workflows[workflow_id]

    def tasks(self) -> list[TaskSpec]:
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> TaskSpec:
        if task_id not in self._tasks:
            raise KeyError(f"unknown task: {task_id}")
        return self._tasks[task_id]
