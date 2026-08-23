"""Managed ComfyUI worker lifecycle.

This module deliberately has no dependency on the product API, SQLite store,
or artifact implementation.  A worker owns one local Git worktree and one
managed ComfyUI runtime. Projects, Runs, and Artifacts belong to whichever
CookSprite API/data directory submits work to that runtime. Source updates are
performed exclusively with ``git pull --ff-only``.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .comfy.client import ComfyClient
from .comfy.managed import (
    install as install_managed_comfy,
    install_node_pack,
    launch_with_preference,
    read_node_pack_runtime_info,
    stop_with_preference,
    sync_dependencies,
    wait_until_ready,
)
from .tool_packages import tool_packages
from .version import NODE_PACK_VERSION


WORKER_SCHEMA = "cooksprite.worker/v2"
RUNTIME_SCHEMA = "cooksprite.worker-runtime/v1"
WORKER_CONFIG_NAME = "worker.json"
RUNTIME_IDENTITY_NAME = "cooksprite-runtime.json"
DEFAULT_RUNTIME_DIR_NAME = "worker-runtime"
DEFAULT_WORKER_PORT = 8288


class WorkerError(RuntimeError):
    """A worker lifecycle invariant was not met."""


@dataclass(frozen=True)
class DeviceSpec:
    """Backend-neutral accelerator preference passed to ComfyUI."""

    backend: str
    index: int | None = None

    @classmethod
    def parse(cls, value: str | None) -> DeviceSpec:
        raw = str(value or "auto").strip().lower()
        backend, separator, raw_index = raw.partition(":")
        if backend not in {"auto", "cpu", "cuda", "rocm", "mps"}:
            raise WorkerError("device must be auto, cpu, cuda[:N], rocm[:N], or mps")
        if separator:
            if backend not in {"cuda", "rocm"} or not raw_index.isdigit():
                raise WorkerError("only cuda and rocm devices accept a non-negative index")
            return cls(backend, int(raw_index))
        return cls(backend)

    @property
    def value(self) -> str:
        return f"{self.backend}:{self.index}" if self.index is not None else self.backend

    @property
    def launch_arguments(self) -> tuple[str, ...]:
        return DEVICE_ARGUMENT_ADAPTERS[self.backend](self)


def _automatic_arguments(spec: DeviceSpec) -> tuple[str, ...]:
    return ()


def _cpu_arguments(spec: DeviceSpec) -> tuple[str, ...]:
    return ("--cpu",)


def _indexed_arguments(spec: DeviceSpec) -> tuple[str, ...]:
    # ROCm exposes devices through torch.cuda and uses ComfyUI's public
    # device-selection argument too.
    return ("--cuda-device", str(spec.index)) if spec.index is not None else ()


DEVICE_ARGUMENT_ADAPTERS: dict[str, Callable[[DeviceSpec], tuple[str, ...]]] = {
    "auto": _automatic_arguments,
    "cpu": _cpu_arguments,
    "cuda": _indexed_arguments,
    "rocm": _indexed_arguments,
    "mps": _automatic_arguments,
}


@dataclass(frozen=True)
class WorkerConfig:
    """The small, non-secret worker configuration persisted beside its runtime."""

    schema: str
    source_dir: str
    runtime_dir: str
    branch: str
    source_revision: str
    source_origin: str | None
    host: str
    port: int
    device: str
    exclusive: bool
    node_pack_version: str
    created_at: str

    @classmethod
    def load(cls, runtime_dir: str | Path) -> WorkerConfig:
        path = worker_config_path(runtime_dir)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkerError(
                f"worker is not initialized at {Path(runtime_dir).expanduser().resolve()}; "
                "run `cspr comfy worker init` first"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerError(f"worker configuration is unreadable: {path}") from exc
        if not isinstance(raw, dict) or raw.get("schema") != WORKER_SCHEMA:
            raise WorkerError(f"worker configuration has an unsupported schema: {path}")
        required = {
            "schema",
            "source_dir",
            "runtime_dir",
            "branch",
            "source_revision",
            "source_origin",
            "host",
            "port",
            "device",
            "exclusive",
            "node_pack_version",
            "created_at",
        }
        missing = sorted(required.difference(raw))
        if missing:
            raise WorkerError(f"worker configuration is missing: {', '.join(missing)}")
        try:
            port = int(raw["port"])
        except (TypeError, ValueError) as exc:
            raise WorkerError("worker configuration port is invalid") from exc
        if not 1 <= port <= 65535:
            raise WorkerError("worker configuration port is outside 1..65535")
        source = Path(str(raw["source_dir"])).expanduser().resolve()
        runtime = Path(str(raw["runtime_dir"])).expanduser().resolve()
        if runtime != Path(runtime_dir).expanduser().resolve():
            raise WorkerError("worker configuration runtime path does not match its location")
        if not str(raw["branch"]).strip():
            raise WorkerError("worker configuration branch is empty")
        source_origin = str(raw["source_origin"] or "").strip()
        if not source_origin:
            raise WorkerError("worker configuration source origin is empty")
        host = str(raw["host"]).strip()
        if not host:
            raise WorkerError("worker configuration host is empty")
        return cls(
            schema=WORKER_SCHEMA,
            source_dir=str(source),
            runtime_dir=str(runtime),
            branch=str(raw["branch"]),
            source_revision=str(raw["source_revision"]),
            source_origin=source_origin,
            host=host,
            port=port,
            device=DeviceSpec.parse(str(raw["device"])).value,
            exclusive=bool(raw["exclusive"]),
            node_pack_version=str(raw["node_pack_version"]),
            created_at=str(raw["created_at"]),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_path(runtime_dir: str | Path, name: str) -> Path:
    return Path(runtime_dir).expanduser().resolve() / name


def worker_config_path(runtime_dir: str | Path) -> Path:
    return _json_path(runtime_dir, WORKER_CONFIG_NAME)


def runtime_identity_path(runtime_dir: str | Path) -> Path:
    return _json_path(runtime_dir, RUNTIME_IDENTITY_NAME)


def default_runtime_dir(source_dir: str | Path) -> Path:
    """Return the dedicated worker-runtime sibling for a source clone.

    ``runtime`` is deliberately not the default name.  It is a common legacy
    ComfyUI location, so using it would make an otherwise harmless worker
    command capable of targeting a pre-existing deployment by accident.
    """

    return Path(source_dir).expanduser().resolve().parent / DEFAULT_RUNTIME_DIR_NAME


def _is_empty_directory(path: Path) -> bool:
    """Return whether ``path`` is absent or contains no entries."""

    return not path.exists() or not any(path.iterdir())


def _require_initializable_runtime(
    runtime: Path,
    source: Path,
    *,
    force: bool,
) -> None:
    """Refuse to adopt an arbitrary pre-existing ComfyUI directory.

    A worker can create a new empty directory, or reconfigure its own stopped
    runtime with ``--force``.  It must never plant a worker manifest inside an
    existing legacy or user-owned runtime: doing so would make later lifecycle
    commands appear to own a process they did not create.
    """

    config_path = worker_config_path(runtime)
    if not config_path.exists():
        if not _is_empty_directory(runtime):
            raise WorkerError(
                "refusing to initialize a non-empty runtime directory; "
                "cspr comfy worker never adopts an existing ComfyUI deployment. "
                "Choose a new empty worker-runtime directory instead"
            )
        return
    if not force:
        raise WorkerError(f"worker is already initialized at {runtime}; use --force only to replace its manifest")

    existing = WorkerConfig.load(runtime)
    if Path(existing.source_dir) != source:
        raise WorkerError(
            "refusing to replace a worker manifest owned by a different source worktree"
        )
    _require_port_idle(existing)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _git(source_dir: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", "-C", str(source_dir), *arguments],
            check=check,
            capture_output=True,
            text=True,
            timeout=90,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerError(f"Git command could not run: {exc}") from exc


def _git_text(source_dir: Path, *arguments: str) -> str:
    result = _git(source_dir, *arguments)
    return result.stdout.strip()


def _safe_remote_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return value
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def _source_root(source_dir: str | Path) -> Path:
    candidate = Path(source_dir).expanduser().resolve()
    top_level = Path(_git_text(candidate, "rev-parse", "--show-toplevel")).resolve()
    if top_level != candidate:
        raise WorkerError(
            f"worker source must be the Git worktree root, not {candidate}; expected {top_level}"
        )
    if not (top_level / "pyproject.toml").is_file() or not (top_level / "cooksprite").is_dir():
        raise WorkerError(f"worker source is not a CookSprite worktree: {top_level}")
    return top_level


def source_identity(source_dir: str | Path) -> dict[str, Any]:
    """Return Git identity without exposing credential-bearing remote URLs."""

    root = _source_root(source_dir)
    branch = _git_text(root, "rev-parse", "--abbrev-ref", "HEAD")
    revision = _git_text(root, "rev-parse", "HEAD")
    dirty = bool(_git_text(root, "status", "--porcelain"))
    origin_result = _git(root, "config", "--get", "remote.origin.url", check=False)
    origin = origin_result.stdout.strip() if origin_result.returncode == 0 else None
    return {
        "source_dir": str(root),
        "branch": branch,
        "revision": revision,
        "dirty": dirty,
        "origin": _safe_remote_url(origin),
    }


def _require_clean_source(source_dir: str | Path) -> dict[str, Any]:
    identity = source_identity(source_dir)
    if identity["branch"] == "HEAD":
        raise WorkerError("worker source is detached; check out its tracked branch before syncing")
    if identity["dirty"]:
        raise WorkerError(
            "worker source has tracked local changes; commit/push them on the remote workflow "
            "or discard them before a worker sync"
        )
    return identity


def _require_pinned_origin(config: WorkerConfig, identity: dict[str, Any]) -> None:
    """Reject a source whose configured remote changed after worker initialization.

    A clean Git worktree is not enough: a worker host must only deploy the
    branch from the exact remote that was recorded when the worker was
    initialized.  The identity exposes a credential-redacted URL, so this
    comparison never reads or prints a Git token.
    """

    expected = str(config.source_origin or "").strip()
    observed = str(identity.get("origin") or "").strip()
    if not expected:
        raise WorkerError("worker configuration has no pinned source origin; reinitialize it")
    if not observed:
        raise WorkerError("worker source has no origin remote; refusing to deploy local-only code")
    if observed != expected:
        raise WorkerError(
            "worker source origin differs from the initialized remote; "
            "refusing to deploy code from a changed remote"
        )


def worker_url(config: WorkerConfig) -> str:
    host = config.host
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{display_host}:{config.port}"


def _socket_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((_socket_host(host), port), timeout=0.25):
            return True
    except OSError:
        return False


def _nvidia_smi(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the vendor CLI without importing CUDA into the API/CLI environment."""

    try:
        return subprocess.run(
            ["nvidia-smi", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerError(f"could not inspect NVIDIA GPU ownership: {exc}") from exc


def _pid_command(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _cuda_resource_status(config: WorkerConfig, spec: DeviceSpec) -> dict[str, Any]:
    """Inspect one explicitly selected NVIDIA device for the CUDA adapter."""

    device = spec.index
    if device is None:
        return {
            "backend": spec.backend,
            "device": spec.value,
            "policy": "exclusive",
            "state": "invalid_device",
            "processes": [],
            "error": "exclusive CUDA inspection requires cuda:N",
        }
    devices = _nvidia_smi("--query-gpu=index,uuid", "--format=csv,noheader,nounits")
    if devices.returncode:
        detail = (devices.stderr or devices.stdout).strip() or "nvidia-smi failed"
        return {
            "backend": spec.backend,
            "device": spec.value,
            "policy": "exclusive",
            "state": "unavailable",
            "processes": [],
            "error": detail,
        }
    gpu_uuid = next(
        (
            values[1].strip()
            for line in devices.stdout.splitlines()
            if len(values := line.split(",", 1)) == 2 and values[0].strip() == str(device)
        ),
        None,
    )
    if not gpu_uuid:
        return {
            "backend": spec.backend,
            "device": spec.value,
            "policy": "exclusive",
            "state": "invalid_device",
            "processes": [],
            "error": f"device {spec.value} was not reported by nvidia-smi",
        }
    applications = _nvidia_smi(
        "--query-compute-apps=pid,process_name,used_memory,gpu_uuid",
        "--format=csv,noheader,nounits",
    )
    if applications.returncode:
        detail = (applications.stderr or applications.stdout).strip() or "nvidia-smi failed"
        return {
            "backend": spec.backend,
            "device": spec.value,
            "policy": "exclusive",
            "state": "unavailable",
            "processes": [],
            "error": detail,
        }
    identity = _read_runtime_identity(config) or {}
    owned_pid = int(identity.get("pid") or 0)
    ownership_verified = bool(
        owned_pid and _owns_process(config, identity, source_identity(config.source_dir))
    )
    processes: list[dict[str, Any]] = []
    for line in applications.stdout.splitlines():
        values = [value.strip() for value in line.split(",", 3)]
        if len(values) != 4 or values[3] != gpu_uuid:
            continue
        try:
            pid = int(values[0])
        except ValueError:
            continue
        owned = pid == owned_pid and ownership_verified
        try:
            used_memory_mib = int(values[2])
        except ValueError:
            used_memory_mib = None
        processes.append(
            {
                "pid": pid,
                "process_name": values[1] or "unknown",
                "used_memory_mib": used_memory_mib,
                "owned": owned,
            }
        )
    foreign = [item for item in processes if not item["owned"]]
    return {
        "backend": spec.backend,
        "device": spec.value,
        "policy": "exclusive",
        "state": "idle" if not processes else "owned" if not foreign else "occupied",
        "processes": processes,
    }


RESOURCE_INSPECTORS = {"cuda": _cuda_resource_status}


def resource_status(config: WorkerConfig) -> dict[str, Any]:
    spec = DeviceSpec.parse(config.device)
    if not config.exclusive:
        return {
            "backend": spec.backend,
            "device": spec.value,
            "policy": "shared",
            "state": "unchecked",
            "processes": [],
        }
    inspector = RESOURCE_INSPECTORS.get(spec.backend)
    if inspector is None:
        return {
            "backend": spec.backend,
            "device": spec.value,
            "policy": "exclusive",
            "state": "unsupported",
            "processes": [],
            "error": f"no exclusive resource inspector is registered for {spec.backend}",
        }
    return inspector(config, spec)


def _require_resource_available(config: WorkerConfig) -> None:
    """Enforce only an explicitly requested resource-exclusivity policy."""

    status = resource_status(config)
    state = status["state"]
    if state in {"unavailable", "invalid_device", "unsupported"}:
        raise WorkerError(
            "worker cannot enforce exclusive resource ownership: "
            + str(status.get("error") or state)
        )
    foreign = [item for item in status["processes"] if not item["owned"]]
    if foreign:
        detail = ", ".join(
            f"pid={item['pid']} ({item['process_name']}, {item['used_memory_mib']} MiB)"
            for item in foreign
        )
        raise WorkerError(
            f"configured device {status['device']} is occupied by {detail}; "
            "choose an idle resource or disable --exclusive"
        )


def _read_runtime_identity(config: WorkerConfig) -> dict[str, Any] | None:
    path = runtime_identity_path(config.runtime_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _lock_digest(source_dir: Path) -> str:
    lock = source_dir / "cooksprite" / "comfy" / "requirements.lock"
    if not lock.is_file():
        raise WorkerError(f"CookSprite Comfy lock is absent: {lock}")
    return hashlib.sha256(lock.read_bytes()).hexdigest()


def _node_version(config: WorkerConfig) -> str | None:
    path = Path(config.runtime_dir) / "ComfyUI" / "custom_nodes" / "cooksprite" / "VERSION"
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


_RUNTIME_IDENTITY_FIELDS = (
    "schema",
    "source_branch",
    "source_revision",
    "node_pack_version",
    "dependency_lock_sha256",
    "comfy_url",
)


def _runtime_identity_payload(
    config: WorkerConfig,
    identity: dict[str, Any],
    *,
    launch: dict[str, Any] | None = None,
    stopped: bool = False,
) -> dict[str, Any]:
    """Build the private worker manifest; node deployment projects it safely."""

    previous = _read_runtime_identity(config) or {}
    payload: dict[str, Any] = {
        "schema": RUNTIME_SCHEMA,
        "source_dir": identity["source_dir"],
        "source_origin": identity["origin"],
        "source_branch": identity["branch"],
        "source_revision": identity["revision"],
        "node_pack_version": NODE_PACK_VERSION,
        "dependency_lock_sha256": _lock_digest(Path(identity["source_dir"])),
        "comfy_url": worker_url(config),
        "updated_at": _now(),
    }
    if launch:
        payload.update(launch)
    elif previous.get("pid") is not None and not stopped:
        payload["pid"] = previous.get("pid")
        payload["launch_method"] = previous.get("launch_method")
        payload["started_at"] = previous.get("started_at")
    if stopped:
        payload["pid"] = None
        payload["stopped_at"] = _now()
    return payload


def _write_runtime_identity(
    config: WorkerConfig,
    identity: dict[str, Any],
    *,
    launch: dict[str, Any] | None = None,
    stopped: bool = False,
) -> dict[str, Any]:
    payload = _runtime_identity_payload(config, identity, launch=launch, stopped=stopped)
    _write_json(runtime_identity_path(config.runtime_dir), payload)
    return payload


def _runtime_identity_errors(
    actual: object,
    expected: dict[str, Any],
    *,
    subject: str,
) -> list[str]:
    """Return exact incompatibilities instead of silently accepting stale nodes."""

    if not isinstance(actual, dict):
        return [f"{subject} is missing or invalid"]
    errors: list[str] = []
    for field in _RUNTIME_IDENTITY_FIELDS:
        if actual.get(field) != expected.get(field):
            errors.append(f"{subject} {field} differs from the worker source")
    return errors


def initialize_worker(
    source_dir: str | Path = ".",
    *,
    runtime_dir: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_WORKER_PORT,
    device: str = "auto",
    exclusive: bool = False,
    branch: str | None = None,
    force: bool = False,
) -> WorkerConfig:
    """Create the worker manifest without installing models or starting ComfyUI."""

    identity = _require_clean_source(source_dir)
    if not identity.get("origin"):
        raise WorkerError("worker source has no origin remote; initialize from the authoritative Git clone")
    selected_branch = branch or str(identity["branch"])
    if selected_branch != identity["branch"]:
        raise WorkerError(
            f"worker branch {selected_branch!r} does not match checked-out source branch {identity['branch']!r}"
        )
    if not 1 <= int(port) <= 65535:
        raise WorkerError("worker port must be inside 1..65535")
    selected_device = DeviceSpec.parse(device)
    if exclusive and selected_device.backend not in RESOURCE_INSPECTORS:
        raise WorkerError(
            f"no exclusive resource inspector is registered for {selected_device.backend}"
        )
    source = Path(identity["source_dir"])
    runtime = Path(runtime_dir).expanduser().resolve() if runtime_dir else default_runtime_dir(source)
    if runtime == source or source in runtime.parents:
        raise WorkerError("worker runtime must be outside the Git source worktree")
    _require_initializable_runtime(runtime, source, force=force)
    config_path = worker_config_path(runtime)
    config = WorkerConfig(
        schema=WORKER_SCHEMA,
        source_dir=str(source),
        runtime_dir=str(runtime),
        branch=selected_branch,
        source_revision=str(identity["revision"]),
        source_origin=identity["origin"],
        host=host,
        port=int(port),
        device=selected_device.value,
        exclusive=bool(exclusive),
        node_pack_version=NODE_PACK_VERSION,
        created_at=_now(),
    )
    _write_json(config_path, asdict(config))
    return config


def _refresh_config(config: WorkerConfig, identity: dict[str, Any]) -> WorkerConfig:
    refreshed = WorkerConfig(
        schema=WORKER_SCHEMA,
        source_dir=str(identity["source_dir"]),
        runtime_dir=config.runtime_dir,
        branch=str(identity["branch"]),
        source_revision=str(identity["revision"]),
        source_origin=config.source_origin,
        host=config.host,
        port=config.port,
        device=config.device,
        exclusive=config.exclusive,
        node_pack_version=NODE_PACK_VERSION,
        created_at=config.created_at,
    )
    _write_json(worker_config_path(config.runtime_dir), asdict(refreshed))
    return refreshed


def _require_installed_runtime(config: WorkerConfig) -> Path:
    runtime = Path(config.runtime_dir)
    main = runtime / "ComfyUI" / "main.py"
    if not main.is_file():
        raise WorkerError(
            f"managed ComfyUI is not installed at {runtime}; run `cspr comfy worker install` first"
        )
    return runtime


def _require_port_idle(config: WorkerConfig) -> None:
    if not port_open(config.host, config.port):
        return
    try:
        queue = ComfyClient(worker_url(config)).queue()
    except Exception as exc:  # noqa: BLE001 - the listener may not be this worker.
        raise WorkerError(
            f"{worker_url(config)} is already listening but cannot be identified as an idle worker: {exc}"
        ) from exc
    running = queue.get("queue_running") or []
    pending = queue.get("queue_pending") or []
    if running or pending:
        raise WorkerError("cannot change a worker while its ComfyUI queue has active or pending work")
    raise WorkerError(
        f"{worker_url(config)} is already listening; stop the configured worker before changing it"
    )


def pull_source(config: WorkerConfig) -> dict[str, Any]:
    """Advance exactly one clean worker branch through remote Git fast-forward."""

    identity = _require_clean_source(config.source_dir)
    _require_pinned_origin(config, identity)
    if identity["branch"] != config.branch:
        raise WorkerError(
            f"worker is configured for branch {config.branch!r}, but source is on {identity['branch']!r}"
        )
    source = Path(config.source_dir)
    result = _git(source, "pull", "--ff-only", "origin", config.branch, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise WorkerError(f"git pull --ff-only failed: {detail or 'unknown Git failure'}")
    fetched_revision = _git_text(source, "rev-parse", "FETCH_HEAD")
    refreshed = _require_clean_source(config.source_dir)
    _require_pinned_origin(config, refreshed)
    if refreshed["revision"] != fetched_revision:
        raise WorkerError(
            "worker source HEAD is not exactly the commit fetched from origin; "
            "refusing to deploy local-only commits"
        )
    return refreshed


def sync_worker(config: WorkerConfig) -> dict[str, Any]:
    """Synchronize one stopped worker from its Git source into its local runtime."""

    _require_port_idle(config)
    identity = pull_source(config)
    if identity["branch"] != config.branch:
        raise WorkerError("source branch changed during worker sync")
    config = _refresh_config(config, identity)
    runtime = _require_installed_runtime(config)
    sync_dependencies(runtime)
    expected_runtime_identity = _runtime_identity_payload(config, identity)
    nodes = install_node_pack(
        runtime,
        install_dependencies=False,
        runtime_identity=expected_runtime_identity,
    )
    runtime_identity = _write_runtime_identity(config, identity)
    return {
        "config": asdict(config),
        "runtime_identity": runtime_identity,
        "nodes": str(nodes),
        "dependencies_synced": True,
        "pulled": True,
    }


def install_worker(
    config: WorkerConfig,
    *,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Explicitly install the managed ComfyUI runtime, without models."""

    _require_port_idle(config)
    identity = pull_source(config)
    runtime = Path(config.runtime_dir)
    config = _refresh_config(config, identity)
    expected_runtime_identity = _runtime_identity_payload(config, identity)
    comfy = install_managed_comfy(
        runtime,
        python_executable=python_executable,
        node_runtime_identity=expected_runtime_identity,
    )
    runtime_identity = _write_runtime_identity(config, identity)
    return {
        "config": asdict(config),
        "runtime": str(comfy),
        "runtime_identity": runtime_identity,
    }


def _runtime_is_current(config: WorkerConfig, identity: dict[str, Any]) -> None:
    runtime_identity = _read_runtime_identity(config)
    if not runtime_identity or runtime_identity.get("schema") != RUNTIME_SCHEMA:
        raise WorkerError("worker runtime identity is missing; run `cspr comfy worker sync` first")
    if runtime_identity.get("source_revision") != identity["revision"]:
        raise WorkerError("worker runtime source revision is stale; run `cspr comfy worker sync` first")
    if runtime_identity.get("node_pack_version") != NODE_PACK_VERSION:
        raise WorkerError("worker runtime node pack is stale; run `cspr comfy worker sync` first")
    if runtime_identity.get("dependency_lock_sha256") != _lock_digest(Path(identity["source_dir"])):
        raise WorkerError("worker runtime dependency lock is stale; run `cspr comfy worker sync` first")
    if _node_version(config) != NODE_PACK_VERSION:
        raise WorkerError("installed CookSprite node pack is stale; run `cspr comfy worker sync` first")
    expected = _runtime_identity_payload(config, identity)
    errors = _runtime_identity_errors(
        read_node_pack_runtime_info(config.runtime_dir),
        expected,
        subject="installed node-pack runtime identity",
    )
    if errors:
        raise WorkerError("; ".join(errors) + "; run `cspr comfy worker sync` first")


def _owns_process(
    config: WorkerConfig,
    runtime_identity: dict[str, Any] | None,
    source: dict[str, Any],
) -> bool:
    """Prove PID, checkout path, and live node-pack identity together."""

    pid = int((runtime_identity or {}).get("pid") or 0)
    command = _pid_command(pid)
    if not command or str(Path(config.runtime_dir) / "ComfyUI") not in command:
        return False
    if not port_open(config.host, config.port):
        return False
    try:
        live = ComfyClient(worker_url(config)).runtime_info()
    except Exception:  # noqa: BLE001 - a failed proof must never imply ownership.
        return False
    expected = _runtime_identity_payload(config, source)
    return not _runtime_identity_errors(live, expected, subject="live runtime identity")


def start_worker(config: WorkerConfig, *, timeout: float = 180) -> dict[str, Any]:
    """Start the configured ComfyUI worker and refuse to adopt another listener."""

    runtime = _require_installed_runtime(config)
    identity = _require_clean_source(config.source_dir)
    _runtime_is_current(config, identity)
    state = _read_runtime_identity(config) or {}
    if port_open(config.host, config.port):
        if _owns_process(config, state, identity):
            return {"already_running": True, "status": worker_status(config)}
        raise WorkerError(
            f"{worker_url(config)} is already occupied by a process this worker does not own"
        )
    _require_resource_available(config)
    device = DeviceSpec.parse(config.device)
    launch = launch_with_preference(
        runtime,
        host=config.host,
        port=config.port,
        arguments=device.launch_arguments,
    )
    try:
        report = wait_until_ready(worker_url(config), timeout=timeout)
        expected = _runtime_identity_payload(config, identity)
        errors = _runtime_identity_errors(
            report.get("runtime_info"),
            expected,
            subject="live ComfyUI runtime identity",
        )
        if errors:
            raise WorkerError("; ".join(errors))
    except Exception:
        # This process was just created by this worker.  Do not leave an
        # unverified runtime listening after an import or identity failure.
        stop_with_preference(runtime, port=config.port)
        _write_runtime_identity(config, identity, stopped=True)
        raise
    runtime_identity = _write_runtime_identity(
        config,
        identity,
        launch={
            "pid": launch.pid,
            "launch_method": launch.method,
            "started_at": _now(),
            "command": list(launch.command),
        },
    )
    return {
        "already_running": False,
        "runtime_identity": runtime_identity,
        "comfyui_version": ((report.get("system_stats") or {}).get("system") or {}).get(
            "comfyui_version"
        ),
    }


def stop_worker(config: WorkerConfig) -> dict[str, Any]:
    """Stop only the configured runtime process, never an arbitrary listener."""

    runtime = _require_installed_runtime(config)
    state = _read_runtime_identity(config) or {}
    identity = _require_clean_source(config.source_dir)
    if not _owns_process(config, state, identity):
        raise WorkerError(
            "configured worker has no owned ComfyUI process; refusing to stop an unknown listener"
        )
    method = stop_with_preference(runtime, port=config.port)
    if method == "none" and port_open(config.host, config.port):
        raise WorkerError("configured ComfyUI process could not be stopped")
    runtime_identity = _write_runtime_identity(config, identity, stopped=True)
    return {"stopped": True, "method": method, "runtime_identity": runtime_identity}


def restart_worker(config: WorkerConfig, *, timeout: float = 180) -> dict[str, Any]:
    """Restart only an idle worker after preserving version identity."""

    if port_open(config.host, config.port):
        try:
            queue = ComfyClient(worker_url(config)).queue()
        except Exception as exc:  # noqa: BLE001
            raise WorkerError(f"cannot inspect ComfyUI queue before restart: {exc}") from exc
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise WorkerError("cannot restart worker while ComfyUI queue has work")
        stop_worker(config)
    return start_worker(config, timeout=timeout)


def worker_status(config: WorkerConfig) -> dict[str, Any]:
    """Read-only worker state. This never creates product data or calls the API."""

    identity = source_identity(config.source_dir)
    runtime = Path(config.runtime_dir)
    runtime_identity = _read_runtime_identity(config)
    pid = int((runtime_identity or {}).get("pid") or 0)
    owned = _owns_process(config, runtime_identity, identity)
    return {
        "schema": WORKER_SCHEMA,
        "source": identity,
        "configuration": {
            "runtime_dir": str(runtime),
            "host": config.host,
            "port": config.port,
            "comfy_url": worker_url(config),
            "device": config.device,
            "exclusive": config.exclusive,
            "configured_revision": config.source_revision,
        },
        "resource": resource_status(config),
        "runtime": {
            "installed": (runtime / "ComfyUI" / "main.py").is_file(),
            "listening": port_open(config.host, config.port),
            "node_pack_version": _node_version(config),
            "node_runtime_info": read_node_pack_runtime_info(config.runtime_dir),
            "identity": runtime_identity,
            "owned_pid": pid if owned else None,
        },
    }


def doctor_worker(config: WorkerConfig) -> dict[str, Any]:
    """Validate the worker identity, node pack, and live ComfyUI schema."""

    status = worker_status(config)
    errors: list[str] = []
    warnings: list[str] = []
    source = status["source"]
    runtime = status["runtime"]
    if source["dirty"]:
        errors.append("worker source has tracked local changes")
    if source["branch"] != config.branch:
        errors.append("worker source branch differs from configuration")
    if source["revision"] != config.source_revision:
        errors.append("worker configuration revision is stale; run `cspr comfy worker sync`")
    resource = status["resource"]
    if resource["state"] in {"unavailable", "invalid_device", "unsupported"}:
        errors.append("worker cannot enforce its exclusive resource policy")
    elif resource["state"] == "occupied":
        errors.append("configured resource is occupied by another compute process")
    if not runtime["installed"]:
        errors.append("managed ComfyUI is not installed")
    if runtime["node_pack_version"] != NODE_PACK_VERSION:
        errors.append("installed CookSprite node pack differs from source")
    identity = runtime.get("identity") or {}
    if identity.get("source_revision") != source["revision"]:
        errors.append("runtime identity source revision differs from worker source")
    if identity.get("node_pack_version") != NODE_PACK_VERSION:
        errors.append("runtime identity node-pack version differs from source")
    expected_identity = _runtime_identity_payload(config, source)
    errors.extend(
        _runtime_identity_errors(
            runtime.get("node_runtime_info"),
            expected_identity,
            subject="installed node-pack runtime identity",
        )
    )
    comfy: dict[str, Any] | None = None
    if runtime["listening"]:
        try:
            report = ComfyClient(worker_url(config)).doctor()
            expected_nodes = {
                node for package in tool_packages.manifests for node in package.node_classes
            }
            present_nodes = set((report.get("object_info") or {}).keys())
            missing_nodes = sorted(expected_nodes.difference(present_nodes))
            if missing_nodes:
                errors.append(f"ComfyUI is missing CookSprite nodes: {', '.join(missing_nodes)}")
            live_identity = report.get("runtime_info")
            errors.extend(
                _runtime_identity_errors(
                    live_identity,
                    expected_identity,
                    subject="live ComfyUI runtime identity",
                )
            )
            system = (report.get("system_stats") or {}).get("system") or {}
            comfy = {
                "comfyui_version": system.get("comfyui_version"),
                "pytorch_version": system.get("pytorch_version"),
                "device_count": len((report.get("system_stats") or {}).get("devices") or []),
                "node_count": len(present_nodes),
                "missing_nodes": missing_nodes,
                "runtime_identity": live_identity,
            }
        except Exception as exc:  # noqa: BLE001 - doctor is an external boundary.
            errors.append(f"ComfyUI doctor failed: {exc}")
    else:
        warnings.append("ComfyUI is not listening")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "status": status,
        "comfy": comfy,
    }


__all__ = [
    "RUNTIME_IDENTITY_NAME",
    "RUNTIME_SCHEMA",
    "DEFAULT_RUNTIME_DIR_NAME",
    "DEFAULT_WORKER_PORT",
    "WORKER_CONFIG_NAME",
    "WORKER_SCHEMA",
    "DeviceSpec",
    "WorkerConfig",
    "WorkerError",
    "default_runtime_dir",
    "doctor_worker",
    "resource_status",
    "initialize_worker",
    "install_worker",
    "port_open",
    "pull_source",
    "restart_worker",
    "runtime_identity_path",
    "source_identity",
    "start_worker",
    "stop_worker",
    "sync_worker",
    "worker_config_path",
    "worker_status",
    "worker_url",
]
