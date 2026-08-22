"""Compact public CookSprite HTTP client used by the CLI and integrations."""

from __future__ import annotations

import os
from typing import Any

import httpx
from typing_extensions import Self

from .config import config_path, read_config

DEFAULT_API_URL = "http://127.0.0.1:8000"


def api_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    if value := os.environ.get("COOKSPRITE_API_URL"):
        return value.rstrip("/")
    configured = read_config().get("api_url")
    if configured:
        return str(configured).rstrip("/")
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
