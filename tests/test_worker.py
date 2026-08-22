from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import cooksprite.worker as worker


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
    runtime = tmp_path / "runtime"

    config = worker.initialize_worker(source, runtime_dir=runtime, cuda_device=0)
    status = worker.worker_status(config)

    assert worker.worker_config_path(runtime).is_file()
    assert status["source"]["dirty"] is False
    assert status["source"]["branch"] == "feat/project-scaffold-v1"
    assert status["configuration"]["cuda_device"] == 0
    assert status["runtime"]["installed"] is False
    assert not (runtime / "data").exists()
    assert not (runtime / "cooksprite.sqlite3").exists()


def test_worker_init_rejects_untracked_source_code(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / "untracked.py").write_text("print('not synced')\n", encoding="utf-8")

    with pytest.raises(worker.WorkerError, match="tracked local changes"):
        worker.initialize_worker(source, runtime_dir=tmp_path / "runtime")


def test_worker_sync_pulls_only_via_fast_forward_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime"
    (runtime / "ComfyUI").mkdir(parents=True)
    (runtime / "ComfyUI" / "main.py").write_text("# fixture\n", encoding="utf-8")
    config = worker.initialize_worker(source, runtime_dir=runtime, port=39199)
    actual_git = worker._git
    calls: list[tuple[str, ...]] = []

    def fake_git(source_dir: Path, *arguments: str, check: bool = True):
        if arguments[:1] == ("pull",):
            calls.append(arguments)
            return subprocess.CompletedProcess(["git", *arguments], 0, "Already up to date.\n", "")
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

    result = worker.sync_worker(config, pull=True, dependencies=True)

    assert calls == [("pull", "--ff-only", "origin", "feat/project-scaffold-v1")]
    assert result["runtime_identity"]["schema"] == worker.RUNTIME_SCHEMA
    assert result["runtime_identity"]["source_revision"] == worker.source_identity(source)["revision"]
    persisted = json.loads(worker.runtime_identity_path(runtime).read_text(encoding="utf-8"))
    assert persisted["node_pack_version"] == worker.NODE_PACK_VERSION
    deployed = json.loads((nodes / "RUNTIME.json").read_text(encoding="utf-8"))
    assert deployed["source_revision"] == persisted["source_revision"]


def test_worker_stop_refuses_unknown_process(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime"
    (runtime / "ComfyUI").mkdir(parents=True)
    (runtime / "ComfyUI" / "main.py").write_text("# fixture\n", encoding="utf-8")
    config = worker.initialize_worker(source, runtime_dir=runtime, port=39198)
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
