from __future__ import annotations

import types

from fastapi.testclient import TestClient

from cooksprite.api.app import create_app
from cooksprite.comfy import managed


def _remote(label: str, base_url: str) -> dict[str, str]:
    return {
        "label": label,
        "base_url": base_url,
        "location": "remote",
        "callback_url": "https://api.example.test/api/v1",
    }


def test_delete_runtime_removes_connection_and_selects_next(tmp_path) -> None:
    from tests.test_api_contract import FakeComfy

    FakeComfy.online = True
    client = TestClient(create_app(tmp_path, FakeComfy))
    first = client.post(
        "/api/v1/runtimes", json={"id": "rt_first", **_remote("First", "http://fake-1")}
    ).json()
    second = client.post(
        "/api/v1/runtimes", json={"id": "rt_second", **_remote("Second", "http://fake-2")}
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


def test_managed_launch_prefers_cli_and_falls_back_to_python(tmp_path, monkeypatch) -> None:
    comfy = tmp_path / "runtime" / "ComfyUI"
    (comfy / "comfy").mkdir(parents=True)
    (comfy / "main.py").write_text("", encoding="utf-8")
    (comfy / "nodes.py").write_text("", encoding="utf-8")
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
