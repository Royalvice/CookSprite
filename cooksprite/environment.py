"""Small project-environment helpers.

CookSprite owns the API/CLI environment; ComfyUI owns the compute environment.
This module only synchronizes the former.  ComfyUI synchronization lives in
``cooksprite.comfy.managed`` so the two virtual environments cannot be mixed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class EnvironmentError(RuntimeError):
    pass


def _uv() -> str:
    executable = shutil.which("uv")
    if not executable:
        raise EnvironmentError("uv is required for locked environment management")
    return executable


def _project_root(path: str | Path | None = None) -> Path:
    root = Path(path or Path.cwd()).expanduser().resolve()
    if not (root / "pyproject.toml").is_file():
        raise EnvironmentError(f"CookSprite pyproject.toml was not found in {root}")
    return root


def lock_project(path: str | Path | None = None) -> Path:
    root = _project_root(path)
    subprocess.run([_uv(), "lock"], cwd=root, check=True)
    return root / "uv.lock"


def check_project(path: str | Path | None = None) -> bool:
    root = _project_root(path)
    subprocess.run([_uv(), "lock", "--check"], cwd=root, check=True)
    return True


__all__ = ["EnvironmentError", "check_project", "lock_project"]
