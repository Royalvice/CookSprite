from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from cooksprite.comfy import client as comfy_client
from cooksprite.comfy import managed
from cooksprite.nodes import cooksprite_nodes


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    (runtime / "ComfyUI").mkdir(parents=True)
    (runtime / "ComfyUI" / "main.py").write_text("# fixture\n", encoding="utf-8")
    return runtime


def _identity() -> dict[str, object]:
    return {
        "schema": "cooksprite.worker-runtime/v1",
        "source_branch": "feat/project-scaffold-v1",
        "source_revision": "a" * 40,
        "node_pack_version": "untrusted-caller-value",
        "dependency_lock_sha256": "b" * 64,
        "comfy_url": "http://127.0.0.1:39197",
        "updated_at": "2026-08-22T00:00:00+00:00",
        "source_origin": "https://secret@example.invalid/CookSprite.git",
        "source_dir": "/private/worker/source",
        "pid": 99999,
        "command": ["secret"],
    }


def test_node_pack_is_atomically_replaced_with_public_identity(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    active = runtime / "ComfyUI" / "custom_nodes" / "cooksprite"
    active.mkdir(parents=True)
    (active / "obsolete.txt").write_text("old pack\n", encoding="utf-8")

    installed = managed.install_node_pack(
        runtime,
        install_dependencies=False,
        runtime_identity=_identity(),
    )

    assert installed == active
    assert (active / "__init__.py").is_file()
    assert (active / "VERSION").read_text(encoding="utf-8").strip() == managed.NODE_PACK_VERSION
    assert not (active / "obsolete.txt").exists()
    identity = json.loads((active / "RUNTIME.json").read_text(encoding="utf-8"))
    assert identity == {
        "schema": "cooksprite.worker-runtime/v1",
        "source_branch": "feat/project-scaffold-v1",
        "source_revision": "a" * 40,
        "node_pack_version": managed.NODE_PACK_VERSION,
        "dependency_lock_sha256": "b" * 64,
        "comfy_url": "http://127.0.0.1:39197",
        "updated_at": "2026-08-22T00:00:00+00:00",
    }
    assert "source_origin" not in identity
    assert "source_dir" not in identity
    assert "pid" not in identity
    assert managed.read_node_pack_runtime_info(runtime) == identity
    assert cooksprite_nodes.runtime_info_payload(active / "RUNTIME.json") == identity
    assert not list(active.parent.glob(".cooksprite-staging-*"))
    assert not list(runtime.glob(".cooksprite-node-pack-backup-*"))


def test_failed_staging_copy_preserves_the_active_node_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    active = managed.install_node_pack(
        runtime,
        install_dependencies=False,
        runtime_identity=_identity(),
    )
    before = (active / "RUNTIME.json").read_bytes()

    def fail_copy(_staging: Path, _identity: dict[str, object] | None) -> None:
        raise RuntimeError("simulated node-pack copy failure")

    monkeypatch.setattr(managed, "_populate_node_pack", fail_copy)
    with pytest.raises(RuntimeError, match="simulated node-pack copy failure"):
        managed.install_node_pack(
            runtime,
            install_dependencies=False,
            runtime_identity={**_identity(), "source_revision": "c" * 40},
        )

    assert (active / "RUNTIME.json").read_bytes() == before
    assert not list(active.parent.glob(".cooksprite-staging-*"))
    assert not list(runtime.glob(".cooksprite-node-pack-backup-*"))


class _Response:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise RuntimeError(f"unexpected HTTP status {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url: str) -> _Response:
        path = urlsplit(url).path
        responses = {
            "/object_info": _Response(200, {"CS_LoadArtifact": {}}),
            "/system_stats": _Response(200, {"system": {"comfyui_version": "fixture"}}),
            "/features": _Response(200, {"api": True}),
            "/models": _Response(200, ["checkpoints"]),
            "/models/checkpoints": _Response(200, ["fixture.safetensors"]),
            "/workflow_templates": _Response(404, {}),
            "/cooksprite/runtime-info": _Response(200, _identity()),
        }
        return responses[path]


def test_comfy_doctor_reads_optional_runtime_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(comfy_client.httpx, "Client", _Client)

    report = comfy_client.ComfyClient("http://fixture.invalid").doctor()

    assert report["runtime_info"] == _identity()
    assert report["models"] == {"checkpoints": ["fixture.safetensors"]}
    assert report["workflow_templates"] == {}


def test_comfy_doctor_accepts_external_runtime_without_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClientWithoutRuntimeInfo(_Client):
        def get(self, url: str) -> _Response:
            if urlsplit(url).path == "/cooksprite/runtime-info":
                return _Response(404, {})
            return super().get(url)

    monkeypatch.setattr(comfy_client.httpx, "Client", ClientWithoutRuntimeInfo)

    report = comfy_client.ComfyClient("http://fixture.invalid").doctor()

    assert report["runtime_info"] is None
