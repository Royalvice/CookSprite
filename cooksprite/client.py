"""Compact public CookSprite HTTP client used by the CLI and integrations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from typing_extensions import Self

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility.
    import tomli as tomllib

DEFAULT_API_URL = "http://127.0.0.1:8000"


def config_path() -> Path:
    return Path(os.environ.get("COOKSPRITE_CONFIG", "~/.cooksprite/config.toml")).expanduser()


def api_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    if value := os.environ.get("COOKSPRITE_API_URL"):
        return value.rstrip("/")
    path = config_path()
    if path.is_file():
        try:
            configured = tomllib.loads(path.read_text(encoding="utf-8")).get("api_url")
            if configured:
                return str(configured).rstrip("/")
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return DEFAULT_API_URL


class CookSpriteClient:
    """A deliberately thin client over the stable ``/api/v1`` boundary."""

    def __init__(self, base_url: str | None = None, timeout: float = 30):
        self.base_url = api_url(base_url)
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.http.close()

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.http.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.http.post(path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.http.put(path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.http.patch(path, **kwargs)


__all__ = ["CookSpriteClient", "api_url", "config_path"]
