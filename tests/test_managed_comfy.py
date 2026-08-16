from pathlib import Path

from cooksprite.comfy import managed


def test_nvidia_linux_installs_pinned_cuda_compatible_torch(monkeypatch):
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(managed.platform, "system", lambda: "Linux")
    monkeypatch.setattr(managed.shutil, "which", lambda command: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        managed, "_pip_install", lambda _python, *arguments: commands.append(arguments)
    )

    result = managed._install_accelerator(Path("/runtime/python"))

    assert result and result["cuda"] == "12.6"
    assert commands == [
        (
            "torch==2.7.0",
            "torchvision==0.22.0",
            "torchaudio==2.7.0",
        )
    ]


def test_non_nvidia_host_does_not_force_a_torch_build(monkeypatch):
    monkeypatch.setattr(managed.platform, "system", lambda: "Darwin")
    assert managed._install_accelerator(Path("/runtime/python")) is None
