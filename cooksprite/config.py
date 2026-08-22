"""One local configuration and one canonical CookSprite data directory."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility.
    import tomli as tomllib


def config_path() -> Path:
    return Path(os.environ.get("COOKSPRITE_CONFIG", "~/.cooksprite/config.toml")).expanduser()


def read_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def resolve_data_dir(explicit: str | Path | None = None) -> Path:
    """Resolve every server entry point to the same local artifact store."""

    selected = explicit or os.environ.get("COOKSPRITE_DATA_DIR")
    if selected is None:
        selected = read_config().get("data_dir") or "~/.cooksprite/data"
    return Path(selected).expanduser().resolve()


def save_data_dir(value: str | Path) -> Path:
    """Persist an explicit store choice so later starts cannot fork state."""

    resolved = resolve_data_dir(value)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    assignment = f"data_dir = {json.dumps(str(resolved), ensure_ascii=False)}"
    updated, replacements = re.subn(
        r"(?m)^data_dir\s*=.*$",
        assignment,
        current,
        count=1,
    )
    if replacements == 0:
        updated = current.rstrip() + ("\n" if current.strip() else "") + assignment + "\n"
    elif not updated.endswith("\n"):
        updated += "\n"
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(path)
    return resolved


__all__ = ["config_path", "read_config", "resolve_data_dir", "save_data_dir"]
