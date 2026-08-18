from __future__ import annotations

from cooksprite.comfy import models


def test_model_download_uses_cli_staging_and_atomic_move(tmp_path, monkeypatch):
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("# test ComfyUI\n", encoding="utf-8")
    (root / "nodes.py").write_text("# test nodes\n", encoding="utf-8")
    (root / "comfy").mkdir()
    file = {
        "folder": "diffusion_models",
        "name": "test-model.safetensors",
        "url": "https://example.com/test-model.safetensors",
    }
    command_seen: list[str] = []

    class FakeProcess:
        stdout = iter(("download 25%\n", "download 100%\n"))

        def __init__(self, command, **_kwargs):
            command_seen.extend(command)
            staging = root / "models" / ".cooksprite-downloads" / "diffusion_models"
            staging.mkdir(parents=True, exist_ok=True)
            (staging / file["name"]).write_bytes(b"model")

        def wait(self):
            return 0

    monkeypatch.setattr(models, "_comfy_cli", lambda _root: "fake-comfy")
    monkeypatch.setattr(models.subprocess, "Popen", FakeProcess)
    events: list[dict] = []

    result = models.download_bundle_file(
        {"directory": str(root), "base_url": "http://unused"},
        file,
        progress=events.append,
    )

    assert result == root / "models" / "diffusion_models" / file["name"]
    assert result.read_bytes() == b"model"
    assert not (
        root / "models" / ".cooksprite-downloads" / "diffusion_models" / file["name"]
    ).exists()
    assert command_seen[:4] == ["fake-comfy", "--workspace=" + str(tmp_path), "model", "download"]
    assert events[-1]["progress"] == 1.0


def test_official_download_command_contains_no_api_credentials(tmp_path):
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("# test ComfyUI\n", encoding="utf-8")
    (root / "nodes.py").write_text("# test nodes\n", encoding="utf-8")
    (root / "comfy").mkdir()
    command = models.official_download_command(
        root,
        {
            "folder": "vae",
            "name": "test-vae.safetensors",
            "url": "https://example.com/test-vae.safetensors",
        },
    )
    assert "model download" in command
    assert "test-vae.safetensors" in command
    assert "token" not in command.lower()
