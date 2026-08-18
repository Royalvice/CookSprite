"""Explicit, isolated ComfyUI installation and lifecycle management."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..version import NODE_PACK_VERSION
from .client import ComfyClient

PINNED_COMFY_REF = "v0.32.0"
NVIDIA_TORCH = {
    "index": "configured package index with official PyPI fallback",
    "torch": "2.7.0",
    "torchvision": "0.22.0",
    "torchaudio": "2.7.0",
    "cuda": "12.6",
}
COMFY_DEPENDENCY_PINS = ["SQLAlchemy==2.0.52"]
COMFY_TEMPLATE_PACKAGES = [
    "comfyui-workflow-templates-core==0.3.307",
    "comfyui-workflow-templates-json==0.1.42",
    "comfyui-workflow-templates-media-api==0.3.84",
    "comfyui-workflow-templates-media-video==0.3.101",
    "comfyui-workflow-templates-media-image==0.3.160",
    "comfyui-workflow-templates-media-other==0.3.229",
    "comfyui-workflow-templates-media-assets-01==0.1.26",
]
COMFY_TEMPLATE_BUNDLE = "comfyui-workflow-templates==0.11.39"
COMFY_REQUIREMENT_PREFLIGHT = ["comfy-kitchen==0.2.30"]

Progress = Callable[[str, float], None]
REQUIRED_COMFY_PATHS = (
    "main.py",
    "nodes.py",
    "comfy/sd.py",
    "comfy/ldm/models/autoencoder.py",
)


def _progress(callback: Progress | None, message: str, value: float) -> None:
    if callback:
        callback(message, value)


def _python_in(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _pick_python(requested: str | None = None) -> str:
    candidates = [requested] if requested else []
    candidates += ["python3.11", "python3.12", sys.executable]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate) if not Path(candidate).exists() else candidate
        if resolved:
            return str(resolved)
    raise RuntimeError("Python 3.11 or 3.12 is required to install ComfyUI")


def _run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _pip_install(python: Path, *arguments: str) -> None:
    """Install with the configured index, then retry missing packages on PyPI.

    Several GPU images preconfigure a partial regional mirror.  Keeping that
    mirror as the fast path is useful, but it must not make an otherwise valid
    pinned ComfyUI release impossible to install.
    """

    command = [str(python), "-m", "pip", "install", *arguments]
    try:
        _run(command)
    except subprocess.CalledProcessError:
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--index-url",
                "https://pypi.org/simple",
                *arguments,
            ]
        )


def _install_accelerator(python: Path, callback: Progress | None = None) -> dict | None:
    """Install a driver-compatible PyTorch before generic Comfy requirements.

    Unversioned PyPI currently resolves to CUDA 13 on Linux.  CUDA 12.6 wheels
    remain compatible with common 535+ datacenter drivers and prevent pip from
    silently replacing them while it resolves ComfyUI's unpinned torch entry.
    """

    if platform.system() != "Linux" or not shutil.which("nvidia-smi"):
        return None
    _progress(callback, "installing NVIDIA CUDA 12.6 PyTorch", 0.14)
    _pip_install(
        python,
        f"torch=={NVIDIA_TORCH['torch']}",
        f"torchvision=={NVIDIA_TORCH['torchvision']}",
        f"torchaudio=={NVIDIA_TORCH['torchaudio']}",
    )
    return dict(NVIDIA_TORCH)


def install_node_pack(root: str | Path, *, install_dependencies: bool = True) -> Path:
    root = Path(root).expanduser().resolve()
    comfy = root / "ComfyUI" if (root / "ComfyUI").exists() else root
    nodes = comfy / "custom_nodes" / "cooksprite"
    nodes.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parents[1] / "nodes"
    # Keep the installed package extensible: copy the complete node tree,
    # including algorithm subpackages and non-Python provenance/preset files.
    # The historical single-file ComfyUI entrypoint remains ``__init__.py``.
    for source_file in source.rglob("*"):
        if not source_file.is_file() or source_file.name == "requirements.txt":
            continue
        relative = source_file.relative_to(source)
        if relative == Path("__init__.py"):
            continue
        target_relative = Path("__init__.py") if relative == Path("cooksprite_nodes.py") else relative
        target = nodes / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
    shutil.copyfile(source / "requirements.txt", nodes / "requirements.txt")
    (nodes / "VERSION").write_text(NODE_PACK_VERSION + "\n", encoding="utf-8")
    if install_dependencies:
        environment_root = comfy.parent
        python = _python_in(environment_root)
        if not python.exists():
            raise RuntimeError(
                f"managed Python environment not found at {python}; pass --no-deps only when attaching to an externally managed ComfyUI"
            )
        _pip_install(python, "-r", str(nodes / "requirements.txt"))
    return nodes


def install(
    root: str | Path,
    repo_url: str = "https://github.com/Comfy-Org/ComfyUI.git",
    *,
    python_executable: str | None = None,
    progress: Progress | None = None,
) -> Path:
    """Install an isolated, pinned ComfyUI and CookSprite node pack.

    Model selection and download are deliberately outside this installer. This
    function is never called at CookSprite startup; it is the explicit setup
    transaction used by the CLI or the local Settings screen.
    """

    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "ComfyUI"
    _progress(progress, "preparing isolated ComfyUI", 0.02)
    if not target.exists():
        _run(
            [
                "git",
                "clone",
                "--branch",
                PINNED_COMFY_REF,
                "--depth",
                "1",
                repo_url,
                str(target),
            ]
        )
    missing = [relative for relative in REQUIRED_COMFY_PATHS if not (target / relative).is_file()]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"{target} is an incomplete ComfyUI checkout; missing: {joined}. "
            "Move the incomplete managed directory aside and run the explicit install again."
        )

    python = _python_in(root)
    if not python.exists():
        _progress(progress, "creating isolated Python environment", 0.1)
        _run([_pick_python(python_executable), "-m", "venv", str(root / ".venv")])
    _progress(progress, "installing ComfyUI dependencies", 0.18)
    _pip_install(python, "--upgrade", "pip", "wheel")
    accelerator = _install_accelerator(python, progress)
    _pip_install(python, *COMFY_DEPENDENCY_PINS)
    # Install each pinned template package independently.  Regional mirrors
    # often contain most, but not all, of this bundle; resolving the meta-package
    # in one transaction would send hundreds of megabytes through the slower
    # fallback index when only one wheel is missing.
    for package in COMFY_TEMPLATE_PACKAGES:
        _pip_install(python, "--no-deps", package)
    _pip_install(python, "--no-deps", COMFY_TEMPLATE_BUNDLE)
    for package in COMFY_REQUIREMENT_PREFLIGHT:
        _pip_install(python, "--no-deps", package)
    _pip_install(python, "-r", str(target / "requirements.txt"))
    _progress(progress, "installing CookSprite nodes", 0.48)
    install_node_pack(root)
    metadata = {
        "schema": "cooksprite.managed-comfy/v1",
        "comfy_ref": PINNED_COMFY_REF,
        "node_pack_version": NODE_PACK_VERSION,
        "python": str(python),
        "accelerator": accelerator,
        "dependency_pins": COMFY_DEPENDENCY_PINS,
        "template_bundle": COMFY_TEMPLATE_BUNDLE,
        "model": None,
    }
    (root / "install.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _progress(progress, "installation complete", 1.0)
    return target


def launch(
    root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8188,
    cuda_device: int | None = None,
) -> int:
    root = Path(root).expanduser().resolve()
    comfy = root / "ComfyUI"
    python = _python_in(root)
    if not comfy.exists() or not python.exists():
        raise RuntimeError("managed ComfyUI is not installed")
    command = [
        str(python),
        str(comfy / "main.py"),
        "--listen",
        host,
        "--port",
        str(port),
        "--disable-auto-launch",
    ]
    if cuda_device is not None:
        command += ["--cuda-device", str(cuda_device)]
    log = (root / "comfy.log").open("ab")
    process = subprocess.Popen(
        command,
        cwd=comfy,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )
    (root / "comfy.pid").write_text(str(process.pid) + "\n", encoding="utf-8")
    return process.pid


def wait_until_ready(url: str, timeout: float = 180) -> dict:
    end = time.monotonic() + timeout
    error: Exception | None = None
    while time.monotonic() < end:
        try:
            return ComfyClient(url).doctor()
        except Exception as exc:  # noqa: BLE001 - service may still be importing nodes.
            error = exc
            time.sleep(1)
    raise RuntimeError(f"ComfyUI did not become ready: {error}")


def doctor(url: str):
    return ComfyClient(url).doctor()
