from pathlib import Path

import pytest

from cooksprite.comfy import managed


def test_managed_comfy_dependency_lock_is_current():
    assert managed.COMFY_REQUIREMENTS_LOCK.is_file()
    assert managed._lock_is_current()
    assert managed.check_dependencies() == managed.COMFY_REQUIREMENTS_LOCK


def test_node_dependency_changes_make_the_shared_lock_stale(monkeypatch, tmp_path):
    node_requirements = tmp_path / "requirements.txt"
    node_requirements.write_text("new-node==1.0\n", encoding="utf-8")
    monkeypatch.setattr(managed, "COMFY_NODE_REQUIREMENTS", node_requirements)

    assert managed._lock_is_current() is False


def test_sync_locked_uses_the_shared_uv_lock(monkeypatch, tmp_path):
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(managed, "_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(managed, "_run", lambda command, cwd=None: commands.append(tuple(command)))

    python = tmp_path / ".venv" / "bin" / "python"
    managed._sync_locked(python)

    assert commands == [
        (
            "/usr/bin/uv",
            "pip",
            "sync",
            "--python",
            str(python),
            str(managed.COMFY_REQUIREMENTS_LOCK),
        )
    ]


def test_stale_managed_comfy_lock_is_rejected(monkeypatch, tmp_path):
    stale = tmp_path / "requirements.lock"
    stale.write_text("# stale\n", encoding="utf-8")
    monkeypatch.setattr(managed, "COMFY_REQUIREMENTS_LOCK", stale)

    with pytest.raises(RuntimeError, match="stale"):
        managed._sync_locked(Path("/runtime/.venv/bin/python"))


def test_node_pack_copies_new_python_modules(monkeypatch, tmp_path):
    package_root = tmp_path / "repo" / "cooksprite"
    source = package_root / "nodes"
    managed_file = package_root / "comfy" / "managed.py"
    managed_file.parent.mkdir(parents=True)
    source.mkdir()
    (source / "cooksprite_nodes.py").write_text("entry\n", encoding="utf-8")
    (source / "prompting.py").write_text("prompt\n", encoding="utf-8")
    (source / "future_tool.py").write_text("future\n", encoding="utf-8")
    (source / "pixel" / "presets").mkdir(parents=True)
    (source / "pixel" / "__init__.py").write_text("pixel\n", encoding="utf-8")
    (source / "pixel" / "presets" / "high_density_v3.yaml").write_text("preset\n", encoding="utf-8")
    (source / "requirements.txt").write_text("# generated\n", encoding="utf-8")
    monkeypatch.setattr(managed, "__file__", str(managed_file))

    runtime = tmp_path / "runtime"
    target = managed.install_node_pack(runtime, install_dependencies=False)

    assert target == runtime / "custom_nodes" / "cooksprite"
    assert (target / "__init__.py").read_text(encoding="utf-8") == "entry\n"
    assert (target / "prompting.py").read_text(encoding="utf-8") == "prompt\n"
    assert (target / "future_tool.py").read_text(encoding="utf-8") == "future\n"
    assert (target / "pixel" / "__init__.py").read_text(encoding="utf-8") == "pixel\n"
    assert (target / "pixel" / "presets" / "high_density_v3.yaml").read_text(encoding="utf-8") == "preset\n"
