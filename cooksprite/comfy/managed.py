"""Explicit, isolated ComfyUI installation and lifecycle management."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..version import NODE_PACK_VERSION
from .client import ComfyClient

PINNED_COMFY_REF = "v0.32.0"
COMFY_PYTHON_VERSION = "3.11"
COMFY_REQUIREMENTS_INPUT = Path(__file__).with_name("requirements.in")
COMFY_NODE_REQUIREMENTS = Path(__file__).parents[1] / "nodes" / "requirements.txt"
COMFY_REQUIREMENTS_LOCK = Path(__file__).with_name("requirements.lock")

Progress = Callable[[str, float], None]
REQUIRED_COMFY_PATHS = (
    "main.py",
    "nodes.py",
    "comfy/sd.py",
    "comfy/ldm/models/autoencoder.py",
)


@dataclass(frozen=True)
class LaunchResult:
    """The local process launch method and child pid for UI/API reporting."""

    pid: int
    method: str
    command: tuple[str, ...]


def _progress(callback: Progress | None, message: str, value: float) -> None:
    if callback:
        callback(message, value)


def _python_in(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _comfy_root(root: str | Path) -> Path:
    """Resolve either a managed workspace or a direct ComfyUI checkout."""

    candidate = Path(root).expanduser().resolve()
    nested = candidate / "ComfyUI"
    if (nested / "main.py").is_file():
        return nested
    if (candidate / "main.py").is_file():
        return candidate
    raise RuntimeError(f"ComfyUI main.py was not found under {candidate}")


def _workspace_for_comfy_cli(comfy: Path) -> Path:
    return comfy.parent if comfy.name.lower() == "comfyui" else comfy


def _comfy_cli(comfy: Path) -> str | None:
    """Find the official ``comfy`` executable without importing it in the API."""

    candidates: list[str] = []
    resolved = shutil.which("comfy")
    if resolved:
        candidates.append(resolved)
    executable = "comfy.exe" if os.name == "nt" else "comfy"
    for workspace in (_workspace_for_comfy_cli(comfy), comfy):
        for environment in (workspace / ".venv", workspace / "venv"):
            candidates.append(
                str(environment / ("Scripts" if os.name == "nt" else "bin") / executable)
            )
    for candidate in candidates:
        if Path(candidate).is_file() or shutil.which(candidate):
            return candidate
    return None


def _comfy_python(comfy: Path) -> Path:
    executable = "python.exe" if os.name == "nt" else "python"
    candidates: list[Path] = []
    for workspace in (_workspace_for_comfy_cli(comfy), comfy):
        for environment in (workspace / ".venv", workspace / "venv"):
            candidates.append(environment / ("Scripts" if os.name == "nt" else "bin") / executable)
    candidates += [Path(value) for value in (shutil.which("python3"), shutil.which("python")) if value]
    if sys.executable:
        candidates.append(Path(sys.executable))
    for candidate in candidates:
        if candidate.is_file() or shutil.which(str(candidate)):
            return candidate
    raise RuntimeError(f"no Python executable is available for ComfyUI at {comfy}")


def _spawn_local(command: list[str], cwd: Path, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log = log_path.open("ab")
    except OSError:
        log = subprocess.DEVNULL
    try:
        return subprocess.Popen(
            command,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
    finally:
        if hasattr(log, "close"):
            log.close()


def launch_with_preference(
    root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8188,
    cuda_device: int | None = None,
) -> LaunchResult:
    """Start local ComfyUI with comfy-cli first, then direct Python fallback."""

    comfy = _comfy_root(root)
    workspace = _workspace_for_comfy_cli(comfy)
    cli = _comfy_cli(comfy)
    cli_error: Exception | None = None
    if cli:
        command = [
            cli,
            f"--workspace={workspace}",
            "launch",
            "--background",
            "--",
            "--listen",
            host,
            "--port",
            str(port),
            "--disable-auto-launch",
        ]
        if cuda_device is not None:
            command += ["--cuda-device", str(cuda_device)]
        try:
            process = _spawn_local(command, workspace, workspace / "comfy-cli.log")
            (workspace / "comfy.pid").write_text(str(process.pid) + "\n", encoding="utf-8")
            return LaunchResult(process.pid, "comfy-cli", tuple(command))
        except OSError as exc:
            cli_error = exc

    python = _comfy_python(comfy)
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
    try:
        process = _spawn_local(command, comfy, workspace / "comfy.log")
    except OSError as exc:
        if cli_error:
            raise RuntimeError(f"comfy-cli and direct Python launch both failed: {cli_error}; {exc}") from exc
        raise RuntimeError(f"failed to start ComfyUI with {python}: {exc}") from exc
    (workspace / "comfy.pid").write_text(str(process.pid) + "\n", encoding="utf-8")
    return LaunchResult(process.pid, "python", tuple(command))


def _process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _listening_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line[1:]) for line in result.stdout.splitlines() if line.startswith("p") and line[1:].isdigit()]


def _terminate_pid(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def _stop_direct(comfy: Path, port: int) -> bool:
    workspace = _workspace_for_comfy_cli(comfy)
    candidates: list[int] = []
    pid_file = workspace / "comfy.pid"
    try:
        candidates.append(int(pid_file.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass
    candidates.extend(_listening_pids(port))
    for pid in dict.fromkeys(candidates):
        command = _process_command(pid)
        if "main.py" not in command or str(comfy) not in command:
            continue
        if _terminate_pid(pid):
            pid_file.unlink(missing_ok=True)
            return True
    return False


def _stop_cli(comfy: Path) -> bool:
    cli = _comfy_cli(comfy)
    if not cli:
        return False
    workspace = _workspace_for_comfy_cli(comfy)
    try:
        result = subprocess.run(
            [cli, f"--workspace={workspace}", "stop"],
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def stop_with_preference(root: str | Path, *, port: int = 8188) -> str:
    """Stop only a ComfyUI process belonging to the supplied local checkout."""

    comfy = _comfy_root(root)
    if _stop_cli(comfy):
        return "comfy-cli"
    if _stop_direct(comfy, port):
        return "python"
    return "none"


def restart_with_preference(
    root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8188,
    cuda_device: int | None = None,
) -> LaunchResult:
    """Restart a local ComfyUI checkout and use the same launch preference."""

    stop_with_preference(root, port=port)
    return launch_with_preference(root, host=host, port=port, cuda_device=cuda_device)


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


def _uv() -> str | None:
    return shutil.which("uv")


def _requirements_fingerprint() -> str:
    paths = (COMFY_REQUIREMENTS_INPUT, COMFY_NODE_REQUIREMENTS)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"managed ComfyUI dependency input is missing: {', '.join(missing)}")
    payload = PINNED_COMFY_REF.encode()
    for path in paths:
        payload += b"\n-- " + str(path.name).encode() + b" --\n" + path.read_bytes()
    return hashlib.sha256(payload).hexdigest()


def _lock_header() -> str:
    return f"# CookSprite lock-input-sha256: {_requirements_fingerprint()}\n"


def _lock_is_current() -> bool:
    if not COMFY_REQUIREMENTS_LOCK.is_file():
        return False
    first_line = COMFY_REQUIREMENTS_LOCK.read_text(encoding="utf-8").splitlines()[:1]
    return first_line == [_lock_header().rstrip("\n")]


def check_dependencies() -> Path:
    """Verify that the packaged ComfyUI lock matches every dependency input."""

    if not _lock_is_current():
        raise RuntimeError(
            "managed ComfyUI dependency lock is missing or stale; "
            "run `cspr dev sync` then `cspr comfy lock`"
        )
    return COMFY_REQUIREMENTS_LOCK


def lock_dependencies(*, progress: Progress | None = None) -> Path:
    """Resolve the pinned ComfyUI + current Tool Package inputs once.

    The lock is shared by every managed ComfyUI runtime.  Adding a Tool
    Package requirement changes the generated node requirements input and
    requires this explicit lock refresh; installation never silently upgrades.
    """

    uv = _uv()
    if not uv:
        raise RuntimeError("uv is required to refresh ComfyUI dependencies; install uv first")
    _progress(progress, "locking ComfyUI and CookSprite node dependencies", 0.05)
    _run(
        [
            uv,
            "pip",
            "compile",
            "--quiet",
            "--universal",
            "--python-version",
            COMFY_PYTHON_VERSION,
            "--output-file",
            str(COMFY_REQUIREMENTS_LOCK),
            str(COMFY_REQUIREMENTS_INPUT),
        ]
    )
    body = COMFY_REQUIREMENTS_LOCK.read_text(encoding="utf-8")
    COMFY_REQUIREMENTS_LOCK.write_text(_lock_header() + body, encoding="utf-8")
    _progress(progress, "ComfyUI dependency lock ready", 0.2)
    return COMFY_REQUIREMENTS_LOCK


def _sync_locked(python: Path) -> None:
    check_dependencies()
    uv = _uv()
    if uv:
        _run([uv, "pip", "sync", "--python", str(python), str(COMFY_REQUIREMENTS_LOCK)])
        return
    # A released wheel can still bootstrap on a machine without uv.  This
    # keeps the exact versions from the lock, while contributors should use
    # uv so stale packages are removed rather than merely upgraded.
    _pip_install(python, "-r", str(COMFY_REQUIREMENTS_LOCK))


def sync_dependencies(
    root: str | Path,
    *,
    update_lock: bool = False,
    python_executable: str | None = None,
    progress: Progress | None = None,
) -> Path:
    """Create/synchronize exactly one managed ComfyUI `.venv`."""

    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if update_lock:
        lock_dependencies(progress=progress)
    python = _python_in(root)
    if not python.exists():
        _progress(progress, "creating managed ComfyUI Python environment", 0.08)
        _run([_pick_python(python_executable), "-m", "venv", str(root / ".venv")])
    _progress(progress, "syncing locked ComfyUI and node dependencies", 0.3)
    _sync_locked(python)
    _progress(progress, "managed ComfyUI environment ready", 1.0)
    return python


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
        sync_dependencies(comfy.parent)
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

    python = sync_dependencies(
        root,
        python_executable=python_executable,
        progress=progress,
    )
    _progress(progress, "installing CookSprite nodes", 0.85)
    install_node_pack(root, install_dependencies=False)
    metadata = {
        "schema": "cooksprite.managed-comfy/v2",
        "comfy_ref": PINNED_COMFY_REF,
        "node_pack_version": NODE_PACK_VERSION,
        "python": str(python),
        "dependency_lock": COMFY_REQUIREMENTS_LOCK.name,
        "dependency_lock_sha256": hashlib.sha256(
            COMFY_REQUIREMENTS_LOCK.read_bytes()
        ).hexdigest(),
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
    if not (root / "ComfyUI" / "main.py").is_file():
        raise RuntimeError("managed ComfyUI is not installed")
    return launch_with_preference(root, host=host, port=port, cuda_device=cuda_device).pid


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
