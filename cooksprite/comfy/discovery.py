"""Small, local-only helpers for finding an existing ComfyUI checkout.

The API never scans a user's whole disk.  It only inspects the process serving
an explicitly supplied loopback URL and a few conventional CookSprite paths.
Remote runtimes deliberately return no directory: a URL is enough to probe,
but it is not authority to write files on the remote machine.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_MAIN_PY = re.compile(r"(?P<path>(?:/|\\.\\.?/)[^\\s'\"]*/main\\.py)(?:\\s|$|['\"])")


def _canonical_root(path: str | Path) -> Path | None:
    candidate = Path(path).expanduser().resolve()
    nested = candidate / "ComfyUI"
    if (nested / "main.py").is_file():
        candidate = nested
    if not all((candidate / marker).exists() for marker in ("main.py", "nodes.py", "comfy")):
        return None
    return candidate


def validate_comfy_directory(path: str | Path | None) -> str | None:
    """Return a canonical ComfyUI root only when the path is clearly valid."""

    if not path:
        return None
    root = _canonical_root(path)
    return str(root) if root else None


def _command(pid: str) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", pid, "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _cwd(pid: str) -> Path | None:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return Path(line[1:])
    return None


def _listening_pids(port: int) -> list[str]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.75,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line[1:] for line in result.stdout.splitlines() if line.startswith("p")]


def discover_comfy_directory(base_url: str, known: str | Path | None = None) -> str | None:
    """Infer the checkout behind a local ComfyUI API, without a disk scan."""

    parsed = urlsplit(base_url)
    if (parsed.hostname or "").lower() not in LOOPBACK_HOSTS:
        return validate_comfy_directory(known)
    if known:
        validated = validate_comfy_directory(known)
        if validated:
            return validated

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    for pid in _listening_pids(port):
        command = _command(pid)
        if "ssh" in command.lower() and "main.py" not in command.lower():
            continue
        cwd = _cwd(pid)
        for candidate in [cwd, *[match.group("path") for match in _MAIN_PY.finditer(command)]]:
            if candidate:
                root = validate_comfy_directory(candidate)
                if root:
                    return root

    # A stopped managed runtime is still unambiguous and can be repaired by
    # the installer.  Do not add broad home-directory guesses for user data.
    if port == 8188:
        managed = Path.home() / ".cooksprite" / "runtime"
        return validate_comfy_directory(managed)
    return None


__all__ = ["discover_comfy_directory", "validate_comfy_directory"]
