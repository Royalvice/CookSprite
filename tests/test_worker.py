from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import cooksprite.worker as worker


@pytest.mark.parametrize(
    ("value", "normalized", "arguments"),
    [
        ("auto", "auto", ()),
        ("cpu", "cpu", ("--cpu",)),
        ("cuda", "cuda", ()),
        ("cuda:2", "cuda:2", ("--cuda-device", "2")),
        ("rocm:3", "rocm:3", ("--cuda-device", "3")),
        ("mps", "mps", ()),
    ],
)
def test_device_spec_is_backend_neutral(
    value: str, normalized: str, arguments: tuple[str, ...]
) -> None:
    spec = worker.DeviceSpec.parse(value)
    assert spec.value == normalized
    assert spec.launch_arguments == arguments


@pytest.mark.parametrize("device", ["auto", "cpu", "rocm:0", "mps", "cuda:0"])
def test_shared_devices_do_not_require_vendor_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, device: str
) -> None:
    source = _source(tmp_path)
    config = worker.initialize_worker(
        source, runtime_dir=tmp_path / "worker-runtime", device=device
    )
    monkeypatch.setattr(
        worker,
        "_nvidia_smi",
        lambda *_args: pytest.fail("shared workers must not call nvidia-smi"),
    )
    assert worker.resource_status(config)["state"] == "unchecked"


def _run(*command: str, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "cooksprite" / "comfy").mkdir(parents=True)
    (source / "cooksprite" / "__init__.py").write_text("", encoding="utf-8")
    (source / "cooksprite" / "comfy" / "requirements.lock").write_text(
        "# lock\n", encoding="utf-8"
    )
    (source / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (source / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _run("git", "init", "--initial-branch=feat/project-scaffold-v1", cwd=source)
    _run("git", "config", "user.name", "CookSprite Test", cwd=source)
    _run("git", "config", "user.email", "test@example.invalid", cwd=source)
    _run("git", "remote", "add", "origin", "https://github.com/Royalvice/CookSprite.git", cwd=source)
    _run("git", "add", ".", cwd=source)
    _run("git", "commit", "-m", "fixture", cwd=source)
    return source


def test_worker_init_and_status_are_compute_only(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "worker-runtime"

    config = worker.initialize_worker(source, runtime_dir=runtime, device="mps")
    status = worker.worker_status(config)

    assert worker.worker_config_path(runtime).is_file()
    assert status["source"]["dirty"] is False
    assert status["source"]["branch"] == "feat/project-scaffold-v1"
    assert status["configuration"]["device"] == "mps"
    assert status["resource"]["state"] == "unchecked"
    assert status["runtime"]["installed"] is False
    assert not (runtime / "data").exists()
    assert not (runtime / "cooksprite.sqlite3").exists()


def test_worker_default_runtime_is_dedicated_sibling(tmp_path: Path) -> None:
    source = _source(tmp_path)

    assert worker.default_runtime_dir(source) == tmp_path / "worker-runtime"
    config = worker.initialize_worker(source)

    assert Path(config.runtime_dir) == tmp_path / "worker-runtime"
    assert config.port == worker.DEFAULT_WORKER_PORT


def test_worker_init_refuses_to_adopt_nonempty_runtime(tmp_path: Path) -> None:
    source = _source(tmp_path)
    legacy = tmp_path / "runtime"
    (legacy / "ComfyUI").mkdir(parents=True)
    (legacy / "ComfyUI" / "main.py").write_text("# legacy fixture\n", encoding="utf-8")

    with pytest.raises(worker.WorkerError, match="never adopts an existing ComfyUI"):
        worker.initialize_worker(source, runtime_dir=legacy)

    assert not worker.worker_config_path(legacy).exists()


def test_worker_resource_guard_refuses_a_foreign_compute_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    config = worker.initialize_worker(
        source, runtime_dir=tmp_path / "worker-runtime", device="cuda:2", exclusive=True
    )

    def fake_nvidia_smi(*arguments: str) -> subprocess.CompletedProcess[str]:
        if arguments[0].startswith("--query-gpu="):
            return subprocess.CompletedProcess(
                ["nvidia-smi", *arguments], 0, "0, GPU-zero\n2, GPU-two\n", ""
            )
        return subprocess.CompletedProcess(
            ["nvidia-smi", *arguments],
            0,
            "4242, unrelated-compute, 8192, GPU-two\n",
            "",
        )

    monkeypatch.setattr(worker, "_nvidia_smi", fake_nvidia_smi)
    status = worker.resource_status(config)

    assert status["state"] == "occupied"
    assert status["processes"] == [
        {
            "pid": 4242,
            "process_name": "unrelated-compute",
            "used_memory_mib": 8192,
            "owned": False,
        }
    ]
    with pytest.raises(worker.WorkerError, match="occupied"):
        worker._require_resource_available(config)


def test_cuda_exclusive_reports_idle_and_verified_owned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "worker-runtime"
    config = worker.initialize_worker(source, runtime_dir=runtime, device="cuda:0", exclusive=True)
    applications = ""

    def fake_nvidia_smi(*arguments: str) -> subprocess.CompletedProcess[str]:
        if arguments[0].startswith("--query-gpu="):
            return subprocess.CompletedProcess(["nvidia-smi", *arguments], 0, "0, GPU-zero\n", "")
        return subprocess.CompletedProcess(["nvidia-smi", *arguments], 0, applications, "")

    monkeypatch.setattr(worker, "_nvidia_smi", fake_nvidia_smi)
    assert worker.resource_status(config)["state"] == "idle"

    worker._write_json(worker.runtime_identity_path(runtime), {"pid": 4242})
    applications = "4242, python, 2048, GPU-zero\n"
    monkeypatch.setattr(worker, "_owns_process", lambda *_args: True)
    owned = worker.resource_status(config)
    assert owned["state"] == "owned"
    assert owned["processes"][0]["owned"] is True


def test_process_ownership_requires_live_runtime_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    config = worker.initialize_worker(source, runtime_dir=tmp_path / "worker-runtime")
    identity = worker.source_identity(source)
    state = {"pid": 4242}
    monkeypatch.setattr(
        worker,
        "_pid_command",
        lambda _pid: f"python {Path(config.runtime_dir) / 'ComfyUI' / 'main.py'}",
    )
    monkeypatch.setattr(worker, "port_open", lambda *_args: True)

    class WrongRuntime:
        def __init__(self, _url: str):
            pass

        def runtime_info(self):
            return {"schema": "wrong"}

    monkeypatch.setattr(worker, "ComfyClient", WrongRuntime)
    assert worker._owns_process(config, state, identity) is False


def test_worker_start_checks_resource_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "worker-runtime"
    config = worker.initialize_worker(
        source, runtime_dir=runtime, device="cuda:2", exclusive=True
    )
    identity = worker.source_identity(source)
    calls: list[worker.WorkerConfig] = []

    monkeypatch.setattr(worker, "_require_installed_runtime", lambda _config: runtime)
    monkeypatch.setattr(worker, "_require_clean_source", lambda _source: identity)
    monkeypatch.setattr(worker, "_runtime_is_current", lambda _config, _identity: None)
    monkeypatch.setattr(worker, "port_open", lambda _host, _port: False)

    def blocked_resource(selected: worker.WorkerConfig) -> None:
        calls.append(selected)
        raise worker.WorkerError("configured resource is occupied")

    monkeypatch.setattr(worker, "_require_resource_available", blocked_resource)
    monkeypatch.setattr(
        worker,
        "launch_with_preference",
        lambda *_args, **_kwargs: pytest.fail("launch must not run before resource validation"),
    )

    with pytest.raises(worker.WorkerError, match="resource is occupied"):
        worker.start_worker(config)
    assert calls == [config]


def test_worker_init_rejects_untracked_source_code(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / "untracked.py").write_text("print('not synced')\n", encoding="utf-8")

    with pytest.raises(worker.WorkerError, match="tracked local changes"):
        worker.initialize_worker(source, runtime_dir=tmp_path / "runtime")


def test_worker_sync_pulls_only_via_fast_forward_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime"
    config = worker.initialize_worker(source, runtime_dir=runtime, port=39199)
    (runtime / "ComfyUI").mkdir(parents=True)
    (runtime / "ComfyUI" / "main.py").write_text("# fixture\n", encoding="utf-8")
    actual_git = worker._git
    calls: list[tuple[str, ...]] = []

    def fake_git(source_dir: Path, *arguments: str, check: bool = True):
        if arguments[:1] == ("pull",):
            calls.append(arguments)
            return subprocess.CompletedProcess(["git", *arguments], 0, "Already up to date.\n", "")
        if arguments == ("rev-parse", "FETCH_HEAD"):
            return subprocess.CompletedProcess(
                ["git", *arguments], 0, worker.source_identity(source)["revision"] + "\n", ""
            )
        return actual_git(source_dir, *arguments, check=check)

    nodes = runtime / "ComfyUI" / "custom_nodes" / "cooksprite"

    def fake_nodes(
        root: Path,
        *,
        install_dependencies: bool = True,
        runtime_identity: dict | None = None,
    ) -> Path:
        nodes.mkdir(parents=True)
        (nodes / "VERSION").write_text(worker.NODE_PACK_VERSION + "\n", encoding="utf-8")
        (nodes / "RUNTIME.json").write_text(
            json.dumps(
                {
                    "schema": worker.RUNTIME_SCHEMA,
                    "source_branch": runtime_identity["source_branch"],
                    "source_revision": runtime_identity["source_revision"],
                    "node_pack_version": worker.NODE_PACK_VERSION,
                    "dependency_lock_sha256": runtime_identity["dependency_lock_sha256"],
                    "comfy_url": runtime_identity["comfy_url"],
                }
            ),
            encoding="utf-8",
        )
        return nodes

    monkeypatch.setattr(worker, "_git", fake_git)
    monkeypatch.setattr(worker, "sync_dependencies", lambda root: runtime / ".venv" / "bin" / "python")
    monkeypatch.setattr(worker, "install_node_pack", fake_nodes)

    result = worker.sync_worker(config)

    assert calls == [("pull", "--ff-only", "origin", "feat/project-scaffold-v1")]
    assert result["runtime_identity"]["schema"] == worker.RUNTIME_SCHEMA
    assert result["runtime_identity"]["source_revision"] == worker.source_identity(source)["revision"]
    persisted = json.loads(worker.runtime_identity_path(runtime).read_text(encoding="utf-8"))
    assert persisted["node_pack_version"] == worker.NODE_PACK_VERSION
    deployed = json.loads((nodes / "RUNTIME.json").read_text(encoding="utf-8"))
    assert deployed["source_revision"] == persisted["source_revision"]


def test_worker_sync_rejects_a_changed_origin_or_local_only_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    config = worker.initialize_worker(source, runtime_dir=tmp_path / "worker-runtime", port=39197)
    identity = worker.source_identity(source)

    with pytest.raises(worker.WorkerError, match="origin differs"):
        worker._require_pinned_origin(config, {**identity, "origin": "https://example.invalid/other.git"})

    actual_git = worker._git

    def fake_git(source_dir: Path, *arguments: str, check: bool = True):
        if arguments[:1] == ("pull",):
            return subprocess.CompletedProcess(["git", *arguments], 0, "Already up to date.\n", "")
        if arguments == ("rev-parse", "FETCH_HEAD"):
            return subprocess.CompletedProcess(["git", *arguments], 0, "0" * 40 + "\n", "")
        return actual_git(source_dir, *arguments, check=check)

    monkeypatch.setattr(worker, "_git", fake_git)
    with pytest.raises(worker.WorkerError, match="not exactly the commit fetched"):
        worker.pull_source(config)


def test_worker_stop_refuses_unknown_process(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime"
    config = worker.initialize_worker(source, runtime_dir=runtime, port=39198)
    (runtime / "ComfyUI").mkdir(parents=True)
    (runtime / "ComfyUI" / "main.py").write_text("# fixture\n", encoding="utf-8")
    worker._write_json(
        worker.runtime_identity_path(runtime),
        {
            "schema": worker.RUNTIME_SCHEMA,
            "pid": os.getpid(),
            "source_revision": worker.source_identity(source)["revision"],
            "node_pack_version": worker.NODE_PACK_VERSION,
        },
    )

    with pytest.raises(worker.WorkerError, match="refusing to stop an unknown listener"):
        worker.stop_worker(config)
