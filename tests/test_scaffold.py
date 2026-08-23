from __future__ import annotations

import json
import time
from typing import ClassVar

from fastapi.testclient import TestClient

from cli.__main__ import parser
from cooksprite.api.app import create_app
from cooksprite.comfy.client import ComfyError
from cooksprite.dev import check_generated, check_tool_packages
from cooksprite.execution import ExecutionPlan
from cooksprite.store import Store
from cooksprite.supervisor import RunSupervisor
from cooksprite.tool_packages import tool_packages


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


class EventComfy:
    def __init__(self, _url: str):
        pass

    @staticmethod
    def client_id() -> str:
        return "client-events"

    def submit(self, _graph, client_id=None):
        assert client_id == "client-events"
        return "prompt-events"

    def wait(self, _prompt_id, *, progress, client_id, event):
        assert client_id == "client-events"
        event({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}})
        event({"type": "execution_start", "data": {"prompt_id": "prompt-events"}})
        event({"type": "executing", "data": {"node": "1", "prompt_id": "prompt-events"}})
        event({"type": "executed", "data": {"node": "1", "prompt_id": "prompt-events"}})
        event({"type": "executing", "data": {"node": "2", "prompt_id": "prompt-events"}})
        event(
            {
                "type": "progress",
                "data": {"node": "2", "value": 4, "max": 8, "prompt_id": "prompt-events"},
            }
        )
        event({"type": "execution_success", "data": {"prompt_id": "prompt-events"}})
        return {"status": {"completed": True}}


class ErrorEventComfy(EventComfy):
    def wait(self, _prompt_id, *, progress, client_id, event):
        event({"type": "execution_start", "data": {"prompt_id": "prompt-events"}})
        event({"type": "executing", "data": {"node": "1", "prompt_id": "prompt-events"}})
        detail = {
            "node": "1",
            "exception_type": "torch.OutOfMemoryError",
            "exception_message": "CUDA out of memory while loading checkpoint",
            "traceback": "CUDA out of memory while loading checkpoint",
            "prompt_id": "prompt-events",
        }
        event({"type": "execution_error", "data": detail})
        raise ComfyError.from_event(detail)


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


def test_run_supervisor_keeps_comfy_runtime_events_and_model_state(tmp_path):
    store = Store(tmp_path)
    store.create_run("run_events", "rt_events")
    supervisor = RunSupervisor(store, EventComfy, lambda _runtime_id: None, max_workers=1)
    supervisor.submit_plan(
        "run_events",
        {"id": "rt_events", "base_url": "http://comfy"},
        ExecutionPlan(
            graph={
                "1": {"class_type": "CheckpointLoaderSimple"},
                "2": {"class_type": "KSampler"},
            },
            sinks=[],
        ),
    )
    row = wait_terminal(store, "run_events")
    supervisor.close()
    runtime_state = json.loads(row["runtime_state"])
    assert row["status"] == "succeeded"
    assert runtime_state["model_status"] == "ready"
    assert runtime_state["phase"] == "completed"
    assert runtime_state["current"]["label"] == "Sampler"
    assert runtime_state["current"]["step"] == 4
    assert runtime_state["queue_remaining"] == 1


def test_run_supervisor_surfaces_out_of_memory_as_structured_runtime_error(tmp_path):
    store = Store(tmp_path)
    store.create_run("run_oom", "rt_oom")
    supervisor = RunSupervisor(store, ErrorEventComfy, lambda _runtime_id: None, max_workers=1)
    supervisor.submit_plan(
        "run_oom",
        {"id": "rt_oom", "base_url": "http://comfy"},
        ExecutionPlan(graph={"1": {"class_type": "CheckpointLoaderSimple"}}, sinks=[]),
    )
    row = wait_terminal(store, "run_oom")
    supervisor.close()
    error = json.loads(row["error"])
    runtime_state = json.loads(row["runtime_state"])
    assert row["status"] == "failed"
    assert error["code"] == "out_of_memory"
    assert "out of memory" in error["message"].lower()
    assert runtime_state["model_status"] == "failed"
    assert runtime_state["error"]["node"] == "Model loader"


def test_server_restart_marks_unsubmitted_run_as_explicitly_retryable(tmp_path):
    Store(tmp_path).create_run(
        "run_interrupted",
        None,
        action_id="project.export",
        project_id="prj_missing",
        request={"project": "prj_missing"},
    )
    client = TestClient(create_app(tmp_path))
    run = client.get("/api/v1/runs/run_interrupted").json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "run_interrupted"


def test_registry_projections_and_node_package_manifest_are_in_sync(tmp_path):
    assert check_generated()
    report = check_tool_packages()
    assert report["tools"] == 14
    client = TestClient(create_app(tmp_path))
    assert {item.id for item in tool_packages.manifests} == {
        "bridge",
        "image",
        "pixel",
        "frames",
        "normal",
    }
    assert all(item.lowerings or item.sealed_graphs for item in tool_packages.manifests)
    assert "/api/v1/tool-packages" not in client.get("/api/v1/openapi.json").json()["paths"]


def test_cli_exposes_headless_run_artifact_and_project_export_paths():
    root = parser()
    export = root.parse_args(["project", "export", "prj_test", "--wait"])
    assert export.command == "project"
    assert export.project_action == "export"
    assert export.wait is True
    wait = root.parse_args(["run", "control", "run_test", "wait"])
    assert wait.run_action == "control"
    assert wait.control_action == "wait"
    download = root.parse_args(
        ["artifact", "get", "art_test", "download", "--out", "sprite.png"]
    )
    assert download.artifact_action == "get"
    assert download.get_action == "download"


def test_cli_start_serves_the_packaged_frontend_without_a_second_process():
    root = parser()
    start = root.parse_args(["service", "start"])
    assert start.no_frontend is False
    assert start.port == 8000
    assert start.data_dir is None
    assert start.public_api_url is None
    assert start.restart is False
    override = root.parse_args(
        ["service", "start", "--public-api-url", "https://api.example.test/api/v1"]
    )
    assert override.public_api_url == "https://api.example.test/api/v1"


def test_api_frontend_routes_can_be_disabled_explicitly(monkeypatch, tmp_path):
    client = TestClient(create_app(tmp_path / "with-web"))
    assert client.get("/").status_code == 200

    client = TestClient(create_app(tmp_path / "without-web", serve_frontend=False))
    assert client.get("/").status_code == 404

    monkeypatch.setenv("COOKSPRITE_SERVE_FRONTEND", "0")
    client = TestClient(create_app(tmp_path / "reload-without-web"))
    assert client.get("/").status_code == 404


def test_cli_exposes_worker_lifecycle_and_remote_runtime_registration():
    root = parser()
    worker_sync = root.parse_args(["comfy", "worker", "sync"])
    assert worker_sync.command == "comfy"
    assert worker_sync.worker_action == "sync"
    imported = root.parse_args(
        [
            "comfy",
            "connect",
            "import",
            "--url",
            "http://runtime.example.test:8188",
            "--callback-url",
            "https://api.example.test/api/v1",
            "--worker-managed",
        ]
    )
    assert imported.worker_managed is True
    assert imported.location == "remote"
