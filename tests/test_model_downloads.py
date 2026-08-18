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
        def __init__(self, command, **_kwargs):
            command_seen.extend(command)
            staging = root / "models" / ".cooksprite-downloads" / "diffusion_models"
            staging.mkdir(parents=True, exist_ok=True)
            (staging / file["name"]).write_bytes(b"model")

        def communicate(self):
            return "download 25%\\ndownload 100%\\n", ""

        returncode = 0

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
    assert command_seen[:4] == ["fake-comfy", "--workspace=" + str(root), "model", "download"]
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
    assert f'--workspace="{root}"' in command
    assert "test-vae.safetensors" in command
    assert "token" not in command.lower()


def test_cli_auth_failure_is_not_hidden_by_http_fallback(tmp_path, monkeypatch):
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("# test ComfyUI\n", encoding="utf-8")
    (root / "nodes.py").write_text("# test nodes\n", encoding="utf-8")
    (root / "comfy").mkdir()

    class UnauthorizedProcess:
        def __init__(self, *_args, **_kwargs):
            pass

        def communicate(self):
            return "hf_unauthorized: gated model\\n", ""

        returncode = 1

    monkeypatch.setattr(models, "_comfy_cli", lambda _root: "fake-comfy")
    monkeypatch.setattr(models.subprocess, "Popen", UnauthorizedProcess)

    try:
        models.download_bundle_file(
            {"directory": str(root), "base_url": "http://unused"},
            {
                "folder": "diffusion_models",
                "name": "gated.safetensors",
                "url": "https://example.com/gated.safetensors",
            },
        )
    except models.ModelDownloadError as exc:
        assert exc.code == "download_forbidden"
        assert "Hugging Face" in str(exc)
    else:
        raise AssertionError("expected a clear model access error")
