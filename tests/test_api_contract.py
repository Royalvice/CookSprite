from __future__ import annotations

import importlib
import json
import re
import time
from pathlib import Path
from typing import ClassVar

from fastapi.testclient import TestClient

from cooksprite.api.app import create_app
from cooksprite.bridge import ArtifactBridge
from cooksprite.catalog import builtin_tools
from cooksprite.compiler import CompileError, Compiler
from cooksprite.domain import ValueRef, WorkflowRevision
from cooksprite.execution import ExecutionPlan


def test_project_id_uses_name_prefix_and_short_uuid(tmp_path):
    client = TestClient(create_app(tmp_path))

    first = client.post(
        "/api/v1/projects", json={"name": "Test 1 / Hero!", "type": "character"}
    ).json()
    second = client.post(
        "/api/v1/projects", json={"name": "Test 1 / Hero!", "type": "character"}
    ).json()

    assert re.fullmatch(r"prj_test_1_hero_[0-9a-f]{8}", first["id"])
    assert re.fullmatch(r"prj_test_1_hero_[0-9a-f]{8}", second["id"])
    assert first["id"] != second["id"]
    assert Path(first["directory"]).name == first["id"]


def test_image_action_uses_the_fixed_front_eye_level_camera_contract(tmp_path):
    client = TestClient(create_app(tmp_path))

    action = client.get("/api/v1/actions/image.generate").json()

    assert "camera" not in {control["id"] for control in action["controls"]}


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


def test_runtime_id_is_generated_from_endpoint_when_user_omits_it(tmp_path):
    client = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))

    response = client.post(
        "/api/v1/runtimes",
        json={"label": "Desk ComfyUI", "base_url": "http://fake"},
    )

    assert response.status_code == 200
    assert re.fullmatch(r"rt_fake_[0-9a-f]{8}", response.json()["id"])


def test_comfy_probe_accepts_a_single_explicit_local_or_remote_url(tmp_path):
    client = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))

    response = client.post("/api/v1/comfyui/probe", json={"base_url": "http://gpu.example:8188"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "found"
    assert body["candidates"][0]["base_url"] == "http://gpu.example:8188"
    assert body["candidates"][0]["models"] == 1
    assert body["candidates"][0]["managed"] is False

    # Keep the pre-existing endpoint available to older CLI clients.
    legacy = client.post("/api/v1/local/probe", json={"base_url": "http://gpu.example:8188"})
    assert legacy.status_code == 200
    assert legacy.json()["candidates"][0]["base_url"] == "http://gpu.example:8188"


def test_comfy_probe_keeps_unreachable_remote_endpoints_location_neutral(tmp_path):
    client = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    FakeComfy.online = False
    try:
        response = client.post(
            "/api/v1/comfyui/probe", json={"base_url": "http://gpu.example:8188"}
        )
    finally:
        FakeComfy.online = True

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unreachable"
    assert body["candidates"] == [
        {
            "base_url": "http://gpu.example:8188",
            "status": "unreachable",
            "error": "runtime stopped",
            "directory_found": False,
            "directory": None,
            "managed": False,
        }
    ]


def test_remote_node_install_does_not_claim_to_write_remote_files(tmp_path):
    client = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    runtime = client.post(
        "/api/v1/runtimes",
        json={"label": "Remote ComfyUI", "base_url": "http://fake", "location": "remote"},
    ).json()

    response = client.post(f"/api/v1/runtimes/{runtime['id']}/nodes/install")

    assert response.status_code == 200
    assert response.json()["status"] == "manual_required"
    assert response.json()["restart_required"] is False


def test_local_node_install_uses_the_explicitly_discovered_checkout(tmp_path):
    comfy_root = tmp_path / "ComfyUI"
    (comfy_root / "comfy").mkdir(parents=True)
    (comfy_root / "main.py").write_text("", encoding="utf-8")
    (comfy_root / "nodes.py").write_text("", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "data", FakeComfy, allow_test_runtime=True))
    runtime = client.post(
        "/api/v1/runtimes",
        json={
            "label": "Local ComfyUI",
            "base_url": "http://fake",
            "location": "local",
            "directory": str(comfy_root),
        },
    ).json()

    response = client.post(f"/api/v1/runtimes/{runtime['id']}/nodes/install")

    assert response.status_code == 200
    assert response.json()["status"] == "installed"
    assert (comfy_root / "custom_nodes" / "cooksprite" / "__init__.py").is_file()


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
    assert all(not action["available"] for action in c.get("/api/v1/actions").json())
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
    assert all(not action["available"] for action in product_client.get("/api/v1/actions").json())


def test_runtime_identity_capabilities_and_project_workspace(tmp_path, monkeypatch):
    FakeComfy.online = True
    c = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    response = c.post(
        "/api/v1/runtimes",
        json={
            "id": "h20-gpu0",
            "label": "H20-baidu · GPU0",
            "base_url": "http://127.0.0.1:18188",
            "location": "remote",
            "transport": "ssh-tunnel",
        },
    )
    assert response.status_code == 200
    assert c.post("/api/v1/runtimes/h20-gpu0/doctor").status_code == 200
    listed = c.get("/api/v1/runtimes").json()
    assert len(listed) == 1
    assert listed[0]["location"] == "remote"
    assert listed[0]["transport"] == "ssh-tunnel"
    assert listed[0]["active"] is True

    # Reconnecting the same stable ID updates one row instead of appending one.
    assert c.post(
        "/api/v1/runtimes",
        json={
            "id": "h20-gpu0",
            "label": "H20-baidu · GPU0 (updated)",
            "base_url": "http://127.0.0.1:18188",
            "location": "remote",
            "transport": "ssh-tunnel",
        },
    ).status_code == 200
    assert len(c.get("/api/v1/runtimes").json()) == 1
    capabilities = c.get("/api/v1/runtimes/h20-gpu0/capabilities").json()
    assert set(capabilities["categories"]) == {"image", "text", "video", "tools"}
    assert capabilities["categories"]["image"]["models"][0]["source"] == "User existing"

    project = c.post("/api/v1/projects", json={"name": "Workspace", "type": "static"}).json()
    directory = Path(project["directory"])
    assert directory.is_dir()
    artifact = c.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "Image", "title": "hero.png"},
        content=b"not-a-real-image-but-a-typed-test-artifact",
    ).json()
    assert (directory / f"{artifact['id']}.png").is_file()
    manifest = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    assert manifest["project"]["id"] == project["id"]
    assert manifest["artifacts"][0]["id"] == artifact["id"]
    assert c.get(f"/api/v1/projects/{project['id']}/directory").json()["path"] == str(directory)

    module = importlib.import_module("cooksprite.api.app")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: None)
    opened = c.post(f"/api/v1/projects/{project['id']}/directory/open").json()
    assert opened["opened"] is True


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
    assert state["runtime_state"]["phase"] == "completed"
    graph = FakeComfy.submitted[-1]
    assert any(n["class_type"] == "CS_NormalEstimate" for n in graph.values())
    assert any(n["class_type"] == "CS_StoreArtifact" for n in graph.values())
    assert "private-prompt" not in str(state)
    events = c.get("/api/v1/runs/" + run.json()["id"] + "/events")
    assert events.status_code == 200
    assert '"runtime_state"' in events.text


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
