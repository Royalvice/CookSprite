from __future__ import annotations

from typing import ClassVar

from fastapi.testclient import TestClient

from cooksprite.api.app import create_app


class FakeComfy:
    identity: ClassVar[dict[str, str] | None] = None

    def __init__(self, _url: str):
        pass

    def ping(self) -> None:
        return None

    def doctor(self) -> dict:
        report = {
            "object_info": {
                name: {"input": {"required": {}}, "output": ["IMAGE"]}
                for name in (
                    "CheckpointLoaderSimple",
                    "CLIPTextEncode",
                    "KSampler",
                    "EmptyLatentImage",
                    "VAEDecode",
                    "CS_LoadArtifact",
                    "CS_StoreArtifact",
                )
            },
            "models": {"checkpoints": ["fixture.safetensors"]},
            "system_stats": {"system": {"comfyui_version": "test"}},
            "runtime_info": self.__class__.identity,
        }
        return report


def _callback() -> str:
    return "https://api.example.test/api/v1"


def _identity(revision: str) -> dict[str, str]:
    return {
        "schema": "cooksprite.worker-runtime/v1",
        "source_branch": "feat/project-scaffold-v1",
        "source_revision": revision,
        "node_pack_version": "2.1.0",
        "dependency_lock_sha256": "a" * 64,
        "comfy_url": "http://127.0.0.1:8188",
    }


def test_remote_control_plane_requires_callback_and_has_no_host_lifecycle_routes(tmp_path) -> None:
    client = TestClient(create_app(tmp_path, FakeComfy))

    assert client.post("/api/v1/comfyui/probe").status_code == 422

    rejected = client.post(
        "/api/v1/runtimes",
        json={"id": "remote", "label": "Remote", "base_url": "http://runtime.example.test:8188"},
    )
    assert rejected.status_code == 422
    assert "callback_url" in str(rejected.json()["detail"])

    created = client.post(
        "/api/v1/runtimes",
        json={
            "id": "remote",
            "label": "Remote",
            "base_url": "http://runtime.example.test:8188",
            "callback_url": _callback(),
        },
    )
    assert created.status_code == 200
    assert created.json()["worker_managed"] is False
    assert "directory" not in created.json()
    runtime_columns = {
        row["name"]
        for row in client.app.state.store.db.execute("PRAGMA table_info(runtimes)").fetchall()
    }
    assert "directory" not in runtime_columns

    paths = client.get("/api/v1/openapi.json").json()["paths"]
    for path in (
        "/api/v1/local/start",
        "/api/v1/setup/local",
        "/api/v1/runtimes/{runtime_id}/nodes/install",
        "/api/v1/runtimes/{runtime_id}/restart",
        "/api/v1/runtimes/{runtime_id}/model-bundles/{bundle_id}/download",
        "/api/v1/runtimes/{runtime_id}/model-downloads/{download_id}",
    ):
        assert path not in paths


def test_worker_managed_runtime_pins_identity_until_explicit_reregistration(tmp_path) -> None:
    FakeComfy.identity = _identity("1" * 40)
    client = TestClient(create_app(tmp_path, FakeComfy))
    payload = {
        "id": "remote",
        "label": "Remote",
        "base_url": "http://runtime.example.test:8188",
        "callback_url": _callback(),
        "worker_managed": True,
    }
    assert client.post("/api/v1/runtimes", json=payload).status_code == 200

    first = client.post("/api/v1/runtimes/remote/doctor")
    assert first.status_code == 200
    assert first.json()["runtime_identity"] == FakeComfy.identity
    assert client.get("/api/v1/runtimes").json()[0]["runtime_identity"] == FakeComfy.identity

    FakeComfy.identity = _identity("2" * 40)
    changed = client.post("/api/v1/runtimes/remote/doctor")
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "worker_runtime_incompatible"
    assert "source_revision" in changed.json()["detail"]["message"]

    reset = client.post("/api/v1/runtimes", json=payload)
    assert reset.status_code == 200
    assert reset.json()["snapshot"] is None
    accepted = client.post("/api/v1/runtimes/remote/doctor")
    assert accepted.status_code == 200
    assert accepted.json()["runtime_identity"] == FakeComfy.identity


def test_worker_managed_runtime_can_be_local_to_its_api(tmp_path) -> None:
    FakeComfy.identity = _identity("3" * 40)
    client = TestClient(create_app(tmp_path, FakeComfy))

    created = client.post(
        "/api/v1/runtimes",
        json={
            "id": "local-managed",
            "label": "Local managed ComfyUI",
            "base_url": "http://127.0.0.1:8288",
            "location": "local",
            "worker_managed": True,
        },
    )

    assert created.status_code == 200
    assert created.json()["worker_managed"] is True
    doctor = client.post("/api/v1/runtimes/local-managed/doctor")
    assert doctor.status_code == 200
    assert doctor.json()["runtime_identity"] == FakeComfy.identity
