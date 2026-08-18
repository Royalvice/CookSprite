"""Explicit ComfyUI model-bundle downloads.

This module is part of the CookSprite control plane.  It never imports
ComfyUI, Torch, or an inference library.  The preferred downloader is the
official ``comfy model download`` command in the selected ComfyUI workspace;
the small HTTP adapter is only for runtimes that expose ComfyUI's download
endpoint.  A missing downloader is reported as an actionable command instead
of being mistaken for a successful install.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .discovery import validate_comfy_directory
from .managed import _comfy_cli, _comfy_root

Progress = Callable[[dict[str, Any]], None]


class ModelDownloadError(RuntimeError):
    """A model download failed without leaving a usable model file."""

    def __init__(self, message: str, *, code: str = "model_download_failed") -> None:
        super().__init__(message)
        self.code = code


def _safe_file_name(value: Any) -> str:
    name = str(value or "")
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ModelDownloadError("invalid model filename", code="model_path_invalid")
    return name


def _safe_folder(value: Any) -> str:
    folder = str(value or "")
    if not folder or Path(folder).name != folder or folder in {".", ".."}:
        raise ModelDownloadError("invalid model folder", code="model_path_invalid")
    return folder


def comfy_model_path(directory: str | Path, file: dict[str, Any]) -> Path:
    """Resolve one declared bundle file below the validated ComfyUI root."""

    root = _comfy_root(directory)
    return root / "models" / _safe_folder(file.get("folder")) / _safe_file_name(file.get("name"))


def official_download_command(directory: str | Path, file: dict[str, Any]) -> str:
    """Return the copyable official command without credentials."""

    root = _comfy_root(directory)
    url = str(file.get("url") or "")
    if urlsplit(url).scheme != "https":
        raise ModelDownloadError("model source must use HTTPS", code="model_source_invalid")
    folder = _safe_folder(file.get("folder"))
    cli = _comfy_cli(root) or "comfy"
    # ``launch`` uses the managed runtime parent as its CLI workspace, but
    # model download resolves paths relative to the actual ComfyUI checkout.
    # Keep those lifecycle concerns separate so downloads land under
    # ``ComfyUI/models`` rather than the runtime parent.
    workspace = root
    return (
        f'{cli} --workspace="{workspace}" model download '
        f'--url "{url}" --relative-path "models/{folder}"'
    )


def _emit(
    progress: Progress | None,
    *,
    current_file: str,
    progress_value: float,
    message: str,
    bytes_done: int = 0,
    bytes_total: int = 0,
) -> None:
    if progress:
        progress(
            {
                "current_file": current_file,
                "progress": max(0.0, min(1.0, progress_value)),
                "bytes_done": max(0, int(bytes_done)),
                "bytes_total": max(0, int(bytes_total)),
                "message": message,
            }
        )


def _percent(text: str) -> float | None:
    match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    return max(0.0, min(100.0, float(match.group(1)))) / 100.0


def _run_cli(
    root: Path,
    file: dict[str, Any],
    progress: Progress | None,
) -> None:
    cli = _comfy_cli(root)
    if not cli:
        raise ModelDownloadError("official Comfy CLI is not installed", code="comfy_cli_missing")
    url = str(file.get("url") or "")
    if urlsplit(url).scheme != "https":
        raise ModelDownloadError("model source must use HTTPS", code="model_source_invalid")
    name = _safe_file_name(file.get("name"))
    folder = _safe_folder(file.get("folder"))
    # The model command must run with the ComfyUI checkout as its workspace;
    # the parent runtime workspace is only valid for lifecycle commands such
    # as ``comfy launch``.
    workspace = root
    staging_relative = Path("models") / ".cooksprite-downloads" / folder
    staging = root / staging_relative / name
    final = root / "models" / folder / name
    staging.parent.mkdir(parents=True, exist_ok=True)
    command = [
        cli,
        f"--workspace={workspace}",
        "model",
        "download",
        "--background",
        "--url",
        url,
        "--relative-path",
        staging_relative.as_posix(),
    ]
    _emit(progress, current_file=name, progress_value=0.0, message="downloading with Comfy CLI")
    try:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
    except OSError as exc:
        raise ModelDownloadError(f"unable to start Comfy CLI: {exc}", code="comfy_cli_failed") from exc

    stdout, _ = process.communicate()
    combined_output = stdout or ""
    return_code = process.returncode
    if return_code:
        staging.unlink(missing_ok=True)
        if re.search(
            r"(?:hf_unauthorized|unauthorized|access denied|\b(?:401|403)\b|gated)",
            combined_output,
            re.IGNORECASE,
        ):
            raise ModelDownloadError(
                "Comfy CLI model download was rejected by the source; authenticate "
                "with Hugging Face and accept the model license before retrying",
                code="download_forbidden",
            )
        raise ModelDownloadError(
            f"Comfy CLI model download failed with exit code {return_code}",
            code="comfy_cli_failed",
        )
    download_match = re.search(
        r"(?:download_id[\"']?\s*[:=]\s*[\"']|background:\s*)([A-Za-z0-9_-]+)",
        combined_output,
        re.IGNORECASE,
    )
    if download_match:
        download_id = download_match.group(1)
        state_path = root / ".comfy-downloads" / f"{download_id}.json"
        while True:
            state: dict[str, Any] = {}
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                pass
            completed = int(state.get("completed_bytes") or 0)
            total = int(state.get("total_bytes") or 0)
            fraction = completed / total if total else 0.0
            status = str(state.get("status") or "starting")
            _emit(
                progress,
                current_file=name,
                progress_value=fraction,
                message="downloading with Comfy CLI",
                bytes_done=completed,
                bytes_total=total,
            )
            if status in {"completed", "succeeded"}:
                break
            if status in {"failed", "cancelled", "canceled"}:
                error = str(state.get("error") or "unknown downloader error")
                if re.search(
                    r"(?:unauthorized|access denied|\b(?:401|403)\b|gated)",
                    error,
                    re.IGNORECASE,
                ):
                    raise ModelDownloadError(
                        "Comfy CLI model download was rejected by the source; authenticate "
                        "with Hugging Face and accept the model license before retrying",
                        code="download_forbidden",
                    )
                raise ModelDownloadError(
                    f"Comfy CLI model download failed: {error}", code="comfy_cli_failed"
                )
            time.sleep(0.5)
    else:
        # Keep simple subprocess fakes and older Comfy CLI builds useful. A
        # background-capable official CLI always returns a download id; a
        # successful legacy response is accepted only when the staged file is
        # already present.
        value = _percent(combined_output)
        if value is not None:
            _emit(
                progress,
                current_file=name,
                progress_value=value,
                message="downloading with Comfy CLI",
            )
    if not staging.is_file() or staging.stat().st_size <= 0:
        raise ModelDownloadError(
            f"Comfy CLI finished but did not produce {name}", code="model_file_missing"
        )
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)


def _download_with_http_endpoint(
    base_url: str,
    root: Path | None,
    file: dict[str, Any],
    progress: Progress | None,
) -> None:
    url = str(file.get("url") or "")
    if urlsplit(url).scheme != "https":
        raise ModelDownloadError("model source must use HTTPS", code="model_source_invalid")
    name = _safe_file_name(file.get("name"))
    folder = _safe_folder(file.get("folder"))
    _emit(progress, current_file=name, progress_value=0.0, message="requesting ComfyUI download")
    try:
        response = httpx.post(
            base_url.rstrip("/") + "/download_model",
            json={"url": url, "relative_path": f"models/{folder}"},
            timeout=None,
        )
    except httpx.HTTPError as exc:
        raise ModelDownloadError(f"ComfyUI download endpoint unavailable: {exc}", code="download_endpoint_unavailable") from exc
    if response.status_code in {401, 403}:
        raise ModelDownloadError(
            f"ComfyUI download endpoint rejected the request ({response.status_code})",
            code="download_forbidden",
        )
    if response.status_code == 404:
        raise ModelDownloadError(
            "ComfyUI does not expose its model download endpoint", code="download_endpoint_missing"
        )
    if not response.is_success:
        raise ModelDownloadError(
            f"ComfyUI download endpoint returned HTTP {response.status_code}",
            code="download_endpoint_failed",
        )
    target = root / "models" / folder / name if root else None
    if target is not None and (not target.is_file() or target.stat().st_size <= 0):
        raise ModelDownloadError(
            f"ComfyUI download endpoint returned success but did not produce {name}",
            code="model_file_missing",
        )
    _emit(progress, current_file=name, progress_value=1.0, message="model verified")


def download_bundle_file(
    runtime: dict[str, Any],
    file: dict[str, Any],
    *,
    progress: Progress | None = None,
) -> Path:
    """Download and verify one bundle member.

    Existing files are treated as already verified by size.  No API startup
    path calls this function; it is only used by the explicit download route.
    """

    name = _safe_file_name(file.get("name"))
    directory = validate_comfy_directory(runtime.get("directory"))
    if directory:
        root = _comfy_root(directory)
        final = comfy_model_path(root, file)
        if final.is_file() and final.stat().st_size > 0:
            _emit(progress, current_file=name, progress_value=1.0, message="model already present")
            return final
        try:
            _run_cli(root, file, progress)
        except ModelDownloadError as cli_error:
            if cli_error.code == "download_forbidden":
                raise
            # A local runtime can expose the same operation over HTTP.  Keep
            # the CLI as the first choice, but let the runtime endpoint be a
            # useful fallback before returning the copyable command.
            try:
                _download_with_http_endpoint(runtime["base_url"], root, file, progress)
            except ModelDownloadError as endpoint_error:
                command = official_download_command(root, file)
                raise ModelDownloadError(
                    f"{endpoint_error}; run manually: {command}", code=endpoint_error.code
                ) from cli_error
        if not final.is_file() or final.stat().st_size <= 0:
            raise ModelDownloadError(f"downloaded model {name} is empty", code="model_file_invalid")
        _emit(progress, current_file=name, progress_value=1.0, message="model verified")
        return final

    # A remote runtime can only be mutated through its own ComfyUI endpoint.
    # If it is unavailable, report the exact command for execution on that
    # host; the API must never pretend that a remote file was installed.
    try:
        validated = validate_comfy_directory(runtime.get("directory"))
        root = Path(validated) if validated else None
        _download_with_http_endpoint(runtime["base_url"], root, file, progress)
    except (KeyError, ModelDownloadError) as endpoint_error:
        command = (
            f'comfy model download --url "{file.get("url")}" '
            f'--relative-path "models/{_safe_folder(file.get("folder"))}"'
        )
        if isinstance(endpoint_error, ModelDownloadError):
            raise ModelDownloadError(
                f"{endpoint_error}; run on the ComfyUI host: {command}",
                code=endpoint_error.code,
            ) from endpoint_error
        raise ModelDownloadError(
            f"remote runtime directory is unavailable; run on the ComfyUI host: {command}",
            code="remote_download_manual",
        ) from endpoint_error
    return (
        root / "models" / _safe_folder(file.get("folder")) / name
        if root
        else Path(f"models/{_safe_folder(file.get('folder'))}/{name}")
    )


__all__ = [
    "ModelDownloadError",
    "comfy_model_path",
    "download_bundle_file",
    "official_download_command",
]
