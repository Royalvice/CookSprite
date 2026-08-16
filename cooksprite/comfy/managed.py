"""Explicit, isolated ComfyUI installation and lifecycle management."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..version import NODE_PACK_VERSION
from .client import ComfyClient

PINNED_COMFY_REF = "v0.32.0"
DEFAULT_MODEL = {
    "id": "sd15-fp16",
    "filename": "v1-5-pruned-emaonly-fp16.safetensors",
    "relative_path": "models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors",
    "url": "https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors",
    "sha256": "e9476a13728cd75d8279f6ec8bad753a66a1957ca375a1464dc63b37db6e3916",
    "size": 2_132_696_762,
    "license": "CreativeML Open RAIL-M",
}
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
    shutil.copyfile(source / "cooksprite_nodes.py", nodes / "__init__.py")
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


def _download_model(comfy: Path, callback: Progress | None = None) -> Path:
    destination = comfy / DEFAULT_MODEL["relative_path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and _sha256(destination) == DEFAULT_MODEL["sha256"]:
        _progress(callback, "default model already verified", 0.9)
        return destination
    partial = destination.with_suffix(destination.suffix + ".partial")
    request = urllib.request.Request(
        DEFAULT_MODEL["url"], headers={"User-Agent": "CookSprite-Installer/1"}
    )
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
        total = int(response.headers.get("Content-Length") or DEFAULT_MODEL["size"])
        received = 0
        while True:
            chunk = response.read(8 * 1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            received += len(chunk)
            _progress(
                callback,
                f"downloading default model {received / 1024**3:.1f}/{total / 1024**3:.1f} GB",
                0.55 + 0.3 * min(1.0, received / max(total, 1)),
            )
    digest = _sha256(partial)
    if digest != DEFAULT_MODEL["sha256"]:
        raise RuntimeError(f"default model checksum mismatch: {digest}")
    partial.replace(destination)
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(
    root: str | Path,
    repo_url: str = "https://github.com/Comfy-Org/ComfyUI.git",
    *,
    with_models: bool = True,
    python_executable: str | None = None,
    progress: Progress | None = None,
) -> Path:
    """Install an isolated, pinned ComfyUI and the verified starter model.

    This function is never called at CookSprite startup.  It is the explicit
    setup transaction used by the CLI or the local Settings screen.
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
    model = _download_model(target, progress) if with_models else None
    metadata = {
        "schema": "cooksprite.managed-comfy/v1",
        "comfy_ref": PINNED_COMFY_REF,
        "node_pack_version": NODE_PACK_VERSION,
        "python": str(python),
        "accelerator": accelerator,
        "dependency_pins": COMFY_DEPENDENCY_PINS,
        "template_bundle": COMFY_TEMPLATE_BUNDLE,
        "model": DEFAULT_MODEL if model else None,
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
