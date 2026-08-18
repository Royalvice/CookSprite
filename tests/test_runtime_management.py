from __future__ import annotations

import time
import types
from pathlib import Path

from fastapi.testclient import TestClient

from cooksprite.api import app as app_module
from cooksprite.api.app import create_app
from cooksprite.comfy import managed


def _comfy_checkout(root: Path) -> Path:
    comfy = root / "ComfyUI"
    (comfy / "comfy").mkdir(parents=True)
    (comfy / "main.py").write_text("", encoding="utf-8")
    (comfy / "nodes.py").write_text("", encoding="utf-8")
    return comfy


def test_delete_runtime_removes_connection_and_selects_next(tmp_path):
    from tests.test_api_contract import FakeComfy

    FakeComfy.online = True
    client = TestClient(create_app(tmp_path, FakeComfy, allow_test_runtime=True))
    first = client.post(
        "/api/v1/runtimes", json={"id": "rt_first", "label": "First", "base_url": "http://fake-1"}
    ).json()
    second = client.post(
        "/api/v1/runtimes", json={"id": "rt_second", "label": "Second", "base_url": "http://fake-2"}
    ).json()
    assert client.post(f"/api/v1/runtimes/{first['id']}/doctor").status_code == 200
    assert client.post(f"/api/v1/runtimes/{second['id']}/doctor").status_code == 200
    assert client.post(f"/api/v1/runtimes/{second['id']}/select").status_code == 200

    response = client.delete(f"/api/v1/runtimes/{second['id']}")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["active_runtime_id"] == first["id"]
    remaining = client.get("/api/v1/runtimes").json()
    assert [item["id"] for item in remaining] == [first["id"]]
    assert remaining[0]["active"] is True


def test_local_start_uses_cli_and_registers_runtime(tmp_path, monkeypatch):
    from tests.test_api_contract import FakeComfy

    comfy = _comfy_checkout(tmp_path / "existing")
    FakeComfy.online = False
    app = create_app(tmp_path / "data", FakeComfy, allow_test_runtime=True)
    client = TestClient(app)

    launched: list[tuple[str, str, int]] = []

    def fake_launch(directory, *, host, port, cuda_device=None):
        launched.append((str(directory), host, port))
        return managed.LaunchResult(1234, "comfy-cli", ("comfy", "launch"))

    monkeypatch.setattr(app_module, "launch_with_preference", fake_launch)
    monkeypatch.setattr(app_module, "wait_until_ready", lambda _url, timeout: FakeComfy("http://127.0.0.1:8188").doctor())

    response = client.post(
        "/api/v1/local/start",
        json={"base_url": "http://127.0.0.1:8188", "directory": str(comfy)},
    )

    assert response.status_code == 202
    for _ in range(50):
        state = client.get("/api/v1/setup/local").json()
        if state["status"] not in {"starting", "validating"}:
            break
        time.sleep(0.01)
    assert state["status"] == "ready"
    assert state["method"] == "comfy-cli"
    assert launched == [(str(comfy), "127.0.0.1", 8188)]
    assert client.get("/api/v1/runtimes").json()[0]["location"] == "local"
    FakeComfy.online = True


def test_local_start_does_not_spawn_when_comfy_is_already_online(tmp_path, monkeypatch):
    from tests.test_api_contract import FakeComfy

    comfy = _comfy_checkout(tmp_path / "existing")
    FakeComfy.online = True
    app = create_app(tmp_path / "data", FakeComfy, allow_test_runtime=True)
    client = TestClient(app)
    monkeypatch.setattr(
        app_module,
        "launch_with_preference",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )

    response = client.post(
        "/api/v1/local/start",
        json={"base_url": "http://127.0.0.1:8188", "directory": str(comfy)},
    )

    assert response.status_code == 202
    for _ in range(50):
        state = client.get("/api/v1/setup/local").json()
        if state["status"] not in {"starting", "validating"}:
            break
        time.sleep(0.01)
    assert state["status"] == "ready"
    assert state["method"] == "already_running"


def test_local_runtime_restart_waits_for_comfy_and_refreshes_capabilities(tmp_path, monkeypatch):
    from tests.test_api_contract import FakeComfy

    comfy = _comfy_checkout(tmp_path / "existing")
    FakeComfy.online = True
    app = create_app(tmp_path / "data", FakeComfy, allow_test_runtime=True)
    client = TestClient(app)
    runtime = client.post(
        "/api/v1/runtimes",
        json={
            "id": "local-test",
            "label": "Local ComfyUI",
            "base_url": "http://127.0.0.1:8188",
            "location": "local",
            "directory": str(comfy),
        },
    ).json()
    assert client.post(f"/api/v1/runtimes/{runtime['id']}/doctor").status_code == 200
    launched: list[str] = []

    def fake_restart(directory, *, host, port, cuda_device=None):
        launched.append(f"{directory}:{host}:{port}")
        return managed.LaunchResult(5678, "comfy-cli", ("comfy", "restart"))

    monkeypatch.setattr(app_module, "restart_with_preference", fake_restart)
    monkeypatch.setattr(app_module, "wait_until_ready", lambda _url, timeout: FakeComfy("http://127.0.0.1:8188").doctor())

    response = client.post(f"/api/v1/runtimes/{runtime['id']}/restart")

    assert response.status_code == 202
    for _ in range(50):
        state = client.get("/api/v1/setup/local").json()
        if state["status"] not in {"starting", "validating"}:
            break
        time.sleep(0.01)
    assert state["status"] == "ready"
    assert state["method"] == "comfy-cli"
    assert launched == [f"{comfy}:127.0.0.1:8188"]


def test_managed_launch_prefers_cli_and_falls_back_to_python(tmp_path, monkeypatch):
    comfy = _comfy_checkout(tmp_path / "runtime")
    spawned: list[list[str]] = []

    def fake_spawn(command, cwd, log_path):
        spawned.append(command)
        return types.SimpleNamespace(pid=4321)

    monkeypatch.setattr(managed, "_spawn_local", fake_spawn)
    monkeypatch.setattr(managed, "_comfy_cli", lambda _comfy: "/usr/local/bin/comfy")
    cli_result = managed.launch_with_preference(comfy)
    assert cli_result.method == "comfy-cli"
    assert cli_result.command[0] == "/usr/local/bin/comfy"
    assert "launch" in cli_result.command

    spawned.clear()
    monkeypatch.setattr(managed, "_comfy_cli", lambda _comfy: None)
    python = comfy.parent / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python_result = managed.launch_with_preference(comfy)
    assert python_result.method == "python"
    assert python_result.command[0] == str(python)
