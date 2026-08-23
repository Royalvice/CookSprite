"""Background daemon lifecycle for the CookSprite API and packaged Web UI."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import config_path, resolve_data_dir


class ServiceError(RuntimeError):
    """A CookSprite service lifecycle invariant was not met."""


def _state_dir(path: str | Path | None = None) -> Path:
    return Path(path).expanduser().resolve() if path else config_path().parent


def _paths(path: str | Path | None = None) -> tuple[Path, Path, Path]:
    root = _state_dir(path)
    return root / "service.pid", root / "service.json", root / "service.log"


def _read_state(path: str | Path | None = None) -> dict[str, Any] | None:
    _, state_path, _ = _paths(path)
    if not state_path.is_file():
        return None
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_command(pid: int) -> str | None:
    ps = shutil.which("ps")
    if not ps:
        return None
    try:
        result = subprocess.run(
            [ps, "-p", str(pid), "-o", "args="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    command = result.stdout.strip()
    return command or None


def _owns_process(state: dict[str, Any]) -> bool:
    try:
        pid = int(state["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if not _pid_alive(pid):
        return False
    command = _pid_command(pid)
    if command is None:
        return False
    return "cooksprite.service" in command and " -m cooksprite.service" in command


def _port_open(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.15)
        return sock.connect_ex((probe_host, port)) == 0


def _write_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(state_path)


def _cleanup_state(path: str | Path | None = None) -> None:
    pid_path, state_path, _ = _paths(path)
    pid_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)


def service_status(path: str | Path | None = None) -> dict[str, Any]:
    state = _read_state(path)
    if not state:
        return {"status": "stopped", "pid": None}
    if _owns_process(state):
        return {**state, "status": "running"}
    try:
        pid = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if _pid_alive(pid):
        return {**state, "status": "unknown"}
    return {**state, "status": "stale"}


def start_service(
    data_dir: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    serve_frontend: bool = True,
    public_api_url: str | None = None,
    restart: bool = False,
    state_dir: str | Path | None = None,
    ready_timeout: float = 15,
) -> dict[str, Any]:
    """Start one detached CookSprite API/Web daemon and record its identity."""

    current = service_status(state_dir)
    if current["status"] == "running":
        if not restart:
            return {**current, "already_running": True}
        stop_service(state_dir=state_dir)
    elif current["status"] == "unknown":
        raise ServiceError(f"service state points to an unknown process (pid {current.get('pid')})")
    elif current["status"] == "stale":
        _cleanup_state(state_dir)

    if _port_open(host, port):
        raise ServiceError(f"{host}:{port} is already in use")
    resolved_data = resolve_data_dir(data_dir)
    pid_path, state_path, log_path = _paths(state_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "cooksprite.service",
        "run",
        "--data-dir",
        str(resolved_data),
        "--host",
        host,
        "--port",
        str(port),
    ]
    if not serve_frontend:
        command.append("--no-frontend")
    if public_api_url:
        command.extend(["--public-api-url", public_api_url])
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
    state = {
        "pid": process.pid,
        "command": command,
        "data_dir": str(resolved_data),
        "host": host,
        "port": port,
        "frontend": serve_frontend,
        "log": str(log_path),
        "started_at": time.time(),
    }
    _write_state(state_path, state)
    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _cleanup_state(state_dir)
            raise ServiceError(f"CookSprite service exited during startup; see {log_path}")
        if _port_open(host, port):
            return {**state, "status": "running"}
        time.sleep(0.1)
    try:
        os.kill(process.pid, signal.SIGTERM)
    except OSError:
        pass
    _cleanup_state(state_dir)
    raise ServiceError(f"CookSprite service did not become ready; see {log_path}")


def stop_service(
    *,
    state_dir: str | Path | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    """Stop only the daemon whose command identity matches the state file."""

    current = service_status(state_dir)
    if current["status"] == "stopped":
        return current
    if current["status"] != "running":
        raise ServiceError(
            f"refusing to stop service state with status {current['status']} "
            f"(pid {current.get('pid')})"
        )
    pid = int(current["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _cleanup_state(state_dir)
        return {"status": "stopped", "pid": pid}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.1)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _cleanup_state(state_dir)
    return {"status": "stopped", "pid": pid}


def run_service(
    data_dir: str | Path,
    *,
    host: str,
    port: int,
    serve_frontend: bool,
    public_api_url: str | None,
) -> None:
    """Run the daemon child in the foreground until it receives a signal."""

    import uvicorn

    from .api.app import create_app

    previous_public_api_url = os.environ.get("COOKSPRITE_PUBLIC_API_URL")
    os.environ["COOKSPRITE_PUBLIC_API_URL"] = (
        public_api_url or f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}/api/v1"
    ).rstrip("/")
    try:
        uvicorn.run(create_app(data_dir, serve_frontend=serve_frontend), host=host, port=port)
    finally:
        if previous_public_api_url is None:
            os.environ.pop("COOKSPRITE_PUBLIC_API_URL", None)
        else:
            os.environ["COOKSPRITE_PUBLIC_API_URL"] = previous_public_api_url


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m cooksprite.service")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--data-dir", required=True)
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, required=True)
    run.add_argument("--no-frontend", action="store_true")
    run.add_argument("--public-api-url")
    args = parser.parse_args()
    if args.command == "run":
        run_service(
            args.data_dir,
            host=args.host,
            port=args.port,
            serve_frontend=not args.no_frontend,
            public_api_url=args.public_api_url,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by daemon smoke tests.
    raise SystemExit(main())


__all__ = ["ServiceError", "run_service", "service_status", "start_service", "stop_service"]
