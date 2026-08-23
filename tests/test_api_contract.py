from __future__ import annotations

import re
import time
from typing import ClassVar

from fastapi.testclient import TestClient

from cooksprite.api.app import _snapshot, create_app
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
    assert "directory" not in first


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
            "/api/v1/runtimes",
            json={
                "id": "rt_test",
                "label": "test",
                "base_url": "http://fake",
                "location": "local",
            },
        ).status_code
        == 200
    )
    assert c.post("/api/v1/runtimes/rt_test/doctor").status_code == 200


def test_runtime_id_is_generated_from_endpoint_when_user_omits_it(tmp_path):
    client = TestClient(create_app(tmp_path, FakeComfy))

    response = client.post(
        "/api/v1/runtimes",
        json={"label": "Desk ComfyUI", "base_url": "http://fake", "location": "local"},
    )

    assert response.status_code == 200
    assert re.fullmatch(r"rt_fake_[0-9a-f]{8}", response.json()["id"])


def test_runtime_registration_without_id_reuses_same_local_endpoint(tmp_path):
    client = TestClient(create_app(tmp_path, FakeComfy))

    first = client.post(
        "/api/v1/runtimes",
        json={
            "label": "Local ComfyUI",
            "base_url": "http://127.0.0.1:8188",
            "location": "local",
        },
    ).json()
    second = client.post(
        "/api/v1/runtimes",
        json={
            "label": "ComfyUI",
            "base_url": "http://127.0.0.1:8188",
            "location": "local",
        },
    ).json()

    assert second["id"] == first["id"]
    runtimes = client.get("/api/v1/runtimes").json()
    assert len(runtimes) == 1
    assert runtimes[0]["label"] == "ComfyUI"


def test_comfy_probe_accepts_a_single_explicit_local_or_remote_url(tmp_path):
    client = TestClient(create_app(tmp_path, FakeComfy))

    response = client.post("/api/v1/comfyui/probe", json={"base_url": "http://gpu.example:8188"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "found"
    assert body["candidates"][0]["base_url"] == "http://gpu.example:8188"
    assert body["candidates"][0]["models"] == 1
    assert "directory" not in body["candidates"][0]


def test_comfy_probe_keeps_unreachable_remote_endpoints_location_neutral(tmp_path):
    client = TestClient(create_app(tmp_path, FakeComfy))
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
        }
    ]


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
    c = TestClient(create_app(tmp_path, FakeComfy))
    ready(c)
    before = c.get("/api/v1/health").json()
    assert before["runtime"] == "ready"

    response = c.post(
        "/api/v1/runtimes",
        json={
            "id": "rt_test",
            "label": "renamed",
            "base_url": "http://fake",
            "location": "local",
        },
    )
    assert response.status_code == 200
    assert response.json()["snapshot"]
    assert c.get("/api/v1/health").json()["runtime"] == "ready"


def test_stored_snapshot_does_not_hide_an_offline_runtime(tmp_path):
    FakeComfy.online = True
    c = TestClient(create_app(tmp_path, FakeComfy))
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


def test_runtime_identity_capabilities_and_project_metadata(tmp_path):
    FakeComfy.online = True
    c = TestClient(create_app(tmp_path, FakeComfy))
    response = c.post(
        "/api/v1/runtimes",
        json={
            "id": "remote-gpu0",
            "label": "Remote GPU · GPU0",
            "base_url": "http://127.0.0.1:8188",
            "location": "remote",
            "transport": "ssh-tunnel",
            "callback_url": "https://api.example.test/api/v1",
        },
    )
    assert response.status_code == 200
    assert c.post("/api/v1/runtimes/remote-gpu0/doctor").status_code == 200
    listed = c.get("/api/v1/runtimes").json()
    assert len(listed) == 1
    assert listed[0]["location"] == "remote"
    assert listed[0]["transport"] == "ssh-tunnel"
    assert listed[0]["active"] is True

    # Reconnecting the same stable ID updates one row instead of appending one.
    assert c.post(
        "/api/v1/runtimes",
        json={
            "id": "remote-gpu0",
            "label": "Remote GPU · GPU0 (updated)",
            "base_url": "http://127.0.0.1:8188",
            "location": "remote",
            "transport": "ssh-tunnel",
            "callback_url": "https://api.example.test/api/v1",
        },
    ).status_code == 200
    assert len(c.get("/api/v1/runtimes").json()) == 1
    capabilities = c.get("/api/v1/runtimes/remote-gpu0/capabilities").json()
    assert set(capabilities["categories"]) == {"image", "text", "video", "tools"}
    assert capabilities["categories"]["image"]["models"][0]["source"] == "User existing"

    project = c.post("/api/v1/projects", json={"name": "Workspace", "type": "static"}).json()
    assert "directory" not in project
    artifact = c.post(
        "/api/v1/artifacts",
        params={"project_id": project["id"], "kind": "Image", "title": "hero.png"},
        content=b"not-a-real-image-but-a-typed-test-artifact",
    ).json()
    assert (tmp_path / "artifacts" / artifact["sha256"]).is_file()
    assert not (tmp_path / "artifacts" / "projects").exists()
    paths = c.get("/api/v1/openapi.json").json()["paths"]
    assert "/api/v1/projects/{project_id}/directory" not in paths
    assert "/api/v1/projects/{project_id}/directory/open" not in paths


def test_runtime_doctor_materializes_private_versioned_definitions(tmp_path):
    c = TestClient(create_app(tmp_path, FakeComfy))
    ready(c)
    runtime = c.get("/api/v1/runtimes").json()[0]
    rows = c.app.state.store.db.execute(
        "SELECT kind,id,revision,runtime_id,snapshot FROM definitions ORDER BY kind,id,revision"
    ).fetchall()
    assert rows
    assert {row["kind"] for row in rows} == {"workflow"}
    assert all(row["runtime_id"] == "rt_test" for row in rows)
    assert all(row["snapshot"] == runtime["snapshot"] for row in rows)
    count = len(rows)
    assert c.post("/api/v1/runtimes/rt_test/doctor").status_code == 200
    repeated = c.app.state.store.db.execute("SELECT COUNT(*) FROM definitions").fetchone()[0]
    assert repeated == count


def test_runtime_snapshot_ignores_dynamic_usage_but_tracks_capabilities():
    report = {
        "object_info": {"Node": {"input": {"required": {"value": ["STRING", {}]}}}},
        "models": {"checkpoints": ["model.safetensors"]},
        "workflow_templates": {"image": {"version": 1}},
        "features": {"preview": True},
        "system_stats": {
            "system": {
                "comfyui_version": "1.0",
                "python_version": "3.11",
                "pytorch_version": "2.7",
            },
            "devices": [{"name": "device", "vram_free": 1}],
        },
        "queue": {"running": 3},
        "runtime_info": {
            "schema": "cooksprite.worker-runtime/v1",
            "source_revision": "a" * 40,
            "node_pack_version": "1",
            "dependency_lock_sha256": "b" * 64,
        },
    }
    stable = _snapshot(report)
    report["system_stats"]["devices"][0]["vram_free"] = 999
    report["queue"]["running"] = 0
    assert _snapshot(report) == stable

    for key, value in (
        ("object_info", {"OtherNode": {}}),
        ("models", {"checkpoints": ["other.safetensors"]}),
        ("workflow_templates", {"image": {"version": 2}}),
        ("features", {"preview": False}),
        ("runtime_info", {**report["runtime_info"], "source_revision": "c" * 40}),
    ):
        changed = {**report, key: value}
        assert _snapshot(changed) != stable
    changed_system = {
        **report,
        "system_stats": {
            **report["system_stats"],
            "system": {**report["system_stats"]["system"], "pytorch_version": "2.8"},
        },
    }
    assert _snapshot(changed_system) != stable


def test_artifacts_deduplicate_and_gc(tmp_path):
    c = TestClient(create_app(tmp_path, FakeComfy))
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


def test_public_api_exposes_stable_actions_not_generic_graph_authoring(tmp_path):
    c = TestClient(create_app(tmp_path, FakeComfy))
    paths = c.get("/api/v1/openapi.json").json()["paths"]
    for path in (
        "/api/v1/tools",
        "/api/v1/tool-packages",
        "/api/v1/runtimes/{runtime_id}/tools",
        "/api/v1/workflows",
        "/api/v1/workflows/{workflow_id}/{revision}",
        "/api/v1/tasks",
        "/api/v1/tasks/{task_id}/{revision}",
    ):
        assert path not in paths
    assert "/api/v1/runs" not in paths
    # The production SPA fallback owns GET only, so a removed POST endpoint
    # is correctly rejected as method-not-allowed rather than being routed.
    assert c.post("/api/v1/runs", json={"runtime_id": "rt_test"}).status_code == 405
