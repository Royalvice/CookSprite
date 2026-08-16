from __future__ import annotations

import time
from typing import ClassVar

from fastapi.testclient import TestClient

from cooksprite.api.app import create_app
from cooksprite.bridge import ArtifactBridge
from cooksprite.catalog import builtin_tools
from cooksprite.compiler import CompileError, Compiler
from cooksprite.domain import ValueRef, WorkflowRevision
from cooksprite.execution import ExecutionPlan


class FakeComfy:
    submitted: ClassVar[list[dict]] = []
    online: ClassVar[bool] = True

    def __init__(self, url):
        self.url = url

    def doctor(self):
        return {
            "object_info": {
                name: {"input": {"required": {}}, "output": ["IMAGE"]}
                for name in (
                    "CheckpointLoaderSimple",
                    "CLIPTextEncode",
                    "KSampler",
                    "EmptyLatentImage",
                    "VAEEncode",
                    "VAEDecode",
                    "RepeatLatentBatch",
                    "ImageScale",
                    "CS_LoadArtifact",
                    "CS_StoreArtifact",
                    "CS_IsolateOnGreen",
                    "CS_NormalEstimate",
                )
            },
            "models": {"checkpoints": ["test-model.safetensors"]},
            "system_stats": {"system": {"comfyui_version": "test"}},
        }

    def submit(self, graph):
        self.__class__.submitted.append(graph)
        return "private-prompt"

    def ping(self):
        if not self.__class__.online:
            raise ConnectionError("runtime stopped")

    def wait(self, prompt):
        return {"status": {"completed": True}}


def ready(c):
    assert (
        c.post(
            "/api/v1/runtimes", json={"id": "rt_test", "label": "test", "base_url": "http://fake"}
        ).status_code
        == 200
    )
    assert c.post("/api/v1/runtimes/rt_test/doctor").status_code == 200


def workflow():
    return {
        "id": "generate",
        "title": "Generate test sprite",
        "runtime_id": "rt_test",
        "inputs": {"image": "Image"},
        "nodes": [
            {
                "id": "gen",
                "tool": "cooksprite.normal_estimate",
                "inputs": {"image": {"input": "image"}},
            }
        ],
        "outputs": {"normal": {"node": "gen", "output": "normal"}},
    }


def test_structured_refs_reject_ambiguous_sources():
    try:
        ValueRef(input="x", literal="x")
    except ValueError:
        return
    assert False, "ambiguous structured reference accepted"


def test_runtime_reregister_keeps_last_validated_snapshot(tmp_path):
    c = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    ready(c)
    before = c.get("/api/v1/health").json()
    assert before["runtime"] == "ready"

    response = c.post(
        "/api/v1/runtimes",
        json={"id": "rt_test", "label": "renamed", "base_url": "http://fake"},
    )
    assert response.status_code == 200
    assert response.json()["snapshot"]
    assert c.get("/api/v1/health").json()["runtime"] == "ready"


def test_stored_snapshot_does_not_hide_an_offline_runtime(tmp_path):
    FakeComfy.online = True
    c = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    ready(c)
    assert c.get("/api/v1/health").json()["runtime"] == "ready"
    FakeComfy.online = False
    time.sleep(2.05)
    state = c.get("/api/v1/health").json()
    assert state["runtime"] == "offline"
    assert state["runtime_id"] == "rt_test"
    assert "stopped" in state["error"]
    assert all(
        not action["available"]
        for action in c.get("/api/v1/actions").json()
        if action["id"] != "sprite.export"
    )
    FakeComfy.online = True


def test_fake_runtime_requires_explicit_test_mode(tmp_path):
    FakeComfy.online = True
    c = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=False))
    assert (
        c.post(
            "/api/v1/runtimes",
            json={"id": "rt_test", "label": "test", "base_url": "http://fake"},
        ).status_code
        == 200
    )
    response = c.post("/api/v1/runtimes/rt_test/doctor")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "test_runtime_not_allowed"


def test_persisted_fake_runtime_stays_offline_in_normal_process(tmp_path):
    FakeComfy.online = True
    test_client = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    ready(test_client)
    assert test_client.get("/api/v1/health").json()["runtime"] == "ready"
    test_client.close()

    product_client = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=False))
    health = product_client.get("/api/v1/health").json()
    assert health["runtime"] == "offline"
    assert "disabled" in health["error"]
    assert product_client.get("/api/v1/runtimes").json()[0]["status"] == "offline"
    assert all(
        not action["available"]
        for action in product_client.get("/api/v1/actions").json()
        if action["id"] != "sprite.export"
    )


def test_versioned_definitions_candidates_and_comfy_compilation(tmp_path):
    c = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    ready(c)
    w = c.post("/api/v1/workflows", json=workflow())
    assert w.status_code == 201
    assert w.json()["revision"] == 1
    t = {
        "id": "single_sprite",
        "title": "Single sprite",
        "runtime_id": "rt_test",
        "inputs": {"image": "Image"},
        "nodes": [
            {
                "id": "generate",
                "workflow_id": "generate",
                "candidates": [1],
                "inputs": {"image": {"input": "image"}},
            }
        ],
        "outputs": {"normal": {"node": "generate", "output": "normal"}},
    }
    assert c.post("/api/v1/tasks", json=t).status_code == 201
    run = c.post(
        "/api/v1/runs",
        json={
            "target": {"kind": "task", "id": "single_sprite", "revision": 1},
            "runtime_id": "rt_test",
            "inputs": {"image": {"literal": "protocol-placeholder"}},
        },
    )
    assert run.status_code == 202
    for _ in range(20):
        state = c.get("/api/v1/runs/" + run.json()["id"]).json()
        if state["status"] != "queued" and state["status"] != "running":
            break
        time.sleep(0.02)
    assert state["status"] == "succeeded"
    graph = FakeComfy.submitted[-1]
    assert any(n["class_type"] == "CS_NormalEstimate" for n in graph.values())
    assert any(n["class_type"] == "CS_StoreArtifact" for n in graph.values())
    assert "private-prompt" not in str(state)


def test_rejects_nonpersistable_workflow_output(tmp_path):
    c = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    ready(c)
    body = workflow()
    body["outputs"] = {"bad": {"node": "gen", "output": "missing"}}
    assert c.post("/api/v1/workflows", json=body).status_code == 422


def test_artifacts_deduplicate_and_gc(tmp_path):
    c = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    a = c.post("/api/v1/artifacts", content=b"png", params={"media_type": "image/png"}).json()
    b = c.post("/api/v1/artifacts", content=b"png", params={"media_type": "image/png"}).json()
    assert a["id"] == b["id"]
    assert c.get(a["url"]).content == b"png"
    assert c.post("/api/v1/artifacts/gc").json()["removed_blobs"] == 0


def test_compiler_rejects_unbound_inputs():
    wf = WorkflowRevision(**workflow(), revision=1, runtime_snapshot="x")
    try:
        Compiler(builtin_tools()).compile_workflow(wf, {})
    except CompileError:
        return
    assert False, "compiler accepted missing prompt binding"


def test_workflow_output_kind_comes_from_the_tool_port_not_its_public_name():
    body = workflow()
    body["outputs"] = {"surface": {"node": "gen", "output": "normal"}}
    wf = WorkflowRevision(**body, revision=1, runtime_snapshot="x")
    plan = Compiler(
        builtin_tools(),
        ArtifactBridge(b"k" * 32, "http://api.test/api/v1"),
        "run_typed_output",
    ).compile_workflow(wf, {"image": ValueRef(literal="protocol-placeholder")})
    assert isinstance(plan, ExecutionPlan)
    sink = next(node for node in plan.graph.values() if node["class_type"] == "CS_StoreArtifact")
    assert "kind=NormalMap" in sink["inputs"]["upload_url"]


def test_contributor_runs_only_accept_versioned_workflows_or_tasks(tmp_path):
    c = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    ready(c)
    tool = c.post(
        "/api/v1/runs",
        json={
            "target": {"kind": "tool", "id": "cooksprite.normal_estimate", "revision": 1},
            "runtime_id": "rt_test",
        },
    )
    assert tool.status_code == 422
    missing_revision = c.post(
        "/api/v1/runs",
        json={
            "target": {"kind": "workflow", "id": "generate"},
            "runtime_id": "rt_test",
        },
    )
    assert missing_revision.status_code == 422
