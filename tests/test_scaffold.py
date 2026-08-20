from __future__ import annotations

import json
import time
from typing import ClassVar

from fastapi.testclient import TestClient

from cli.__main__ import _runtime_registration_payload, parser
from cooksprite.api.app import create_app
from cooksprite.comfy.client import ComfyError
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
    client = TestClient(create_app(tmp_path, allow_test_runtime=True))
    run = client.get("/api/v1/runs/run_interrupted").json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "run_interrupted"


def test_registry_projections_and_node_package_manifest_are_in_sync(tmp_path):
    assert check_generated()
    report = check_tool_packages()
    assert report["tools"] == 10
    client = TestClient(create_app(tmp_path, allow_test_runtime=True))
    packages = client.get("/api/v1/tool-packages").json()
    assert {item["id"] for item in packages} == {
        "bridge",
        "image",
        "pixel",
        "alpha",
        "frames",
        "normal",
    }
    assert all(item["lowerings"] or item.get("sealed_graphs") for item in packages)


def test_cli_exposes_headless_run_artifact_and_project_export_paths():
    root = parser()
    export = root.parse_args(["project", "export", "prj_test", "--wait"])
    assert export.action == "export"
    assert export.wait is True
    wait = root.parse_args(["run", "wait", "run_test"])
    assert wait.action == "wait"
    download = root.parse_args(["artifact", "download", "art_test", "--out", "sprite.png"])
    assert download.action == "download"


def test_cli_start_defaults_to_api_frontend_and_managed_comfy():
    root = parser()
    start = root.parse_args(["start"])
    assert start.no_comfy is False
    assert start.no_frontend is False
    assert start.port == 8000
    assert start.frontend_port == 5173
    assert start.runtime is None
    payload = _runtime_registration_payload(
        start,
        comfy_url="http://127.0.0.1:8188",
        runtime_location="local",
        runtime_transport="http",
        api_base="http://127.0.0.1:8000",
    )
    assert "id" not in payload
    explicit = root.parse_args(["start", "--runtime", "rt_named"])
    explicit_payload = _runtime_registration_payload(
        explicit,
        comfy_url="http://127.0.0.1:8188",
        runtime_location="local",
        runtime_transport="http",
        api_base="http://127.0.0.1:8000",
    )
    assert explicit_payload["id"] == "rt_named"
    no_comfy = root.parse_args(["start", "--no-comfy", "--frontend-port", "5174"])
    assert no_comfy.no_comfy is True
    assert no_comfy.frontend_port == 5174


def test_cli_exposes_two_environment_lock_and_sync_commands():
    root = parser()
    comfy_lock = root.parse_args(["comfy", "lock"])
    assert comfy_lock.action == "lock"
    comfy_sync = root.parse_args(["comfy", "sync", "/tmp/comfy-runtime", "--update-lock"])
    assert comfy_sync.action == "sync"
    assert comfy_sync.update_lock is True
    env_sync = root.parse_args(
        ["env", "sync", "--project-dir", ".", "--comfy-dir", "/tmp/comfy-runtime"]
    )
    assert env_sync.action == "sync"
    assert env_sync.comfy_dir == "/tmp/comfy-runtime"
