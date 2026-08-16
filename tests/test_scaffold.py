from __future__ import annotations

import time
from typing import ClassVar

from fastapi.testclient import TestClient

from cli.__main__ import parser
from cooksprite.api.app import create_app
from cooksprite.dev import check_generated, check_tool_packages
from cooksprite.execution import ExecutionPlan
from cooksprite.store import Store
from cooksprite.supervisor import RunSupervisor


class ProgressComfy:
    submitted_client_id: ClassVar[str | None] = None

    def __init__(self, _url: str):
        pass

    @staticmethod
    def client_id() -> str:
        return "client-progress"

    def submit(self, _graph, client_id=None):
        self.__class__.submitted_client_id = client_id
        return "prompt-progress"

    def wait(self, _prompt_id, *, progress, client_id):
        assert client_id == "client-progress"
        progress(0.5, "KSampler 10/20")
        time.sleep(0.02)
        progress(1, "KSampler 20/20")
        return {"status": {"completed": True}}


def wait_terminal(store: Store, run_id: str) -> dict:
    for _ in range(100):
        row = store.run(run_id)
        if row and row["status"] in {"succeeded", "failed", "cancelled"}:
            return row
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def test_run_supervisor_uses_comfy_client_identity_and_progress(tmp_path):
    store = Store(tmp_path)
    store.create_run("run_progress", "rt_progress")
    supervisor = RunSupervisor(store, ProgressComfy, lambda _runtime_id: None, max_workers=1)
    supervisor.submit_plan(
        "run_progress",
        {"id": "rt_progress", "base_url": "http://comfy"},
        ExecutionPlan(graph={}, sinks=[]),
    )
    row = wait_terminal(store, "run_progress")
    supervisor.close()
    assert row["status"] == "succeeded"
    assert row["progress"] == 1
    assert ProgressComfy.submitted_client_id == "client-progress"


def test_server_restart_marks_unsubmitted_run_as_explicitly_retryable(tmp_path):
    Store(tmp_path).create_run(
        "run_interrupted",
        None,
        action_id="project.export",
        project_id="prj_missing",
        request={"project": "prj_missing"},
    )
    client = TestClient(create_app(tmp_path, allow_test_runtime=True))
    run = client.get("/api/v1/runs/run_interrupted").json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "run_interrupted"


def test_registry_projections_and_node_package_manifest_are_in_sync(tmp_path):
    assert check_generated()
    report = check_tool_packages()
    assert report["tools"] == 9
    client = TestClient(create_app(tmp_path, allow_test_runtime=True))
    packages = client.get("/api/v1/tool-packages").json()
    assert {item["id"] for item in packages} == {"bridge", "prompt", "image", "frames", "normal"}
    assert all(item["lowerings"] for item in packages)


def test_cli_exposes_headless_run_artifact_and_project_export_paths():
    root = parser()
    export = root.parse_args(["project", "export", "prj_test", "--wait"])
    assert export.action == "export"
    assert export.wait is True
    wait = root.parse_args(["run", "wait", "run_test"])
    assert wait.action == "wait"
    download = root.parse_args(["artifact", "download", "art_test", "--out", "sprite.png"])
    assert download.action == "download"
