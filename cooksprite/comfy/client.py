"""Private ComfyUI HTTP client. Its identifiers never appear in the public API."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def doctor(self) -> dict[str, Any]:
        with httpx.Client(timeout=10) as c:
            nodes = c.get(self.base_url + "/object_info")
            nodes.raise_for_status()
            system = c.get(self.base_url + "/system_stats")
            system.raise_for_status()
            features = c.get(self.base_url + "/features")
            folders = c.get(self.base_url + "/models")
            models: dict[str, list[str]] = {}
            if folders.is_success and isinstance(folders.json(), list):
                for folder in folders.json():
                    response = c.get(self.base_url + f"/models/{folder}")
                    if response.is_success and isinstance(response.json(), list):
                        models[str(folder)] = response.json()
        return {
            "object_info": nodes.json(),
            "system_stats": system.json(),
            "features": features.json() if features.is_success else {},
            "models": models,
        }

    def submit(self, graph: dict[str, Any], client_id: str | None = None) -> str:
        body: dict[str, Any] = {"prompt": graph}
        if client_id:
            body["client_id"] = client_id
        r = httpx.post(self.base_url + "/prompt", json=body, timeout=20)
        r.raise_for_status()
        body = r.json()
        if "prompt_id" not in body:
            raise ComfyError(f"ComfyUI did not return prompt_id: {body}")
        return body["prompt_id"]

    def _history(self, prompt_id: str) -> dict[str, Any] | None:
        r = httpx.get(self.base_url + f"/history/{prompt_id}", timeout=10)
        r.raise_for_status()
        item = r.json().get(prompt_id)
        if item and item.get("status", {}).get("status_str") == "error":
            messages = item.get("status", {}).get("messages", [])
            raise ComfyError(f"ComfyUI execution failed: {messages}")
        return item

    def wait(
        self,
        prompt_id: str,
        timeout: float = 3600,
        *,
        progress: Callable[[float, str], None] | None = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        if progress and client_id:
            try:
                return self._wait_websocket(prompt_id, client_id, timeout, progress)
            except (OSError, TimeoutError, ConnectionClosed):
                # A proxy may block WebSockets. History polling remains authoritative.
                pass
        return self._wait_polling(prompt_id, timeout, progress)

    def _wait_polling(
        self,
        prompt_id: str,
        timeout: float,
        progress: Callable[[float, str], None] | None = None,
    ) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            item = self._history(prompt_id)
            if item and item.get("status", {}).get("completed"):
                return item
            if progress:
                progress(0.0, "waiting for ComfyUI")
            time.sleep(0.35)
        raise ComfyError("ComfyUI job timed out")

    def _wait_websocket(
        self,
        prompt_id: str,
        client_id: str,
        timeout: float,
        progress: Callable[[float, str], None],
    ) -> dict[str, Any]:
        parsed = urlsplit(self.base_url)
        websocket_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                "/ws",
                f"clientId={client_id}",
                "",
            )
        )
        end = time.monotonic() + timeout
        with connect(websocket_url, open_timeout=10, close_timeout=2) as socket:
            while time.monotonic() < end:
                try:
                    raw = socket.recv(timeout=min(0.5, max(0.01, end - time.monotonic())))
                except TimeoutError:
                    item = self._history(prompt_id)
                    if item and item.get("status", {}).get("completed"):
                        return item
                    continue
                if not isinstance(raw, str):
                    continue
                event = json.loads(raw)
                data = event.get("data") or {}
                if data.get("prompt_id") not in {None, prompt_id}:
                    continue
                if event.get("type") == "progress":
                    value = float(data.get("value") or 0)
                    maximum = max(1.0, float(data.get("max") or 1))
                    progress(min(1.0, value / maximum), str(data.get("node") or "sampling"))
                elif event.get("type") == "executing" and data.get("node") is None:
                    item = self._history(prompt_id)
                    if item and item.get("status", {}).get("completed"):
                        return item
                elif event.get("type") == "execution_error":
                    raise ComfyError(
                        str(data.get("exception_message") or "ComfyUI execution failed")
                    )
        raise ComfyError("ComfyUI job timed out")

    @staticmethod
    def client_id() -> str:
        return f"cooksprite-{uuid.uuid4().hex}"

    def queue(self) -> dict[str, Any]:
        r = httpx.get(self.base_url + "/queue", timeout=10)
        r.raise_for_status()
        return r.json()

    def ping(self) -> None:
        """Cheap liveness check; a stored capability snapshot is not a heartbeat."""
        r = httpx.get(self.base_url + "/queue", timeout=0.75)
        r.raise_for_status()

    def cancel(self, prompt_id: str | None = None) -> None:
        if prompt_id:
            response = httpx.post(
                self.base_url + "/queue", json={"delete": [prompt_id]}, timeout=10
            )
            response.raise_for_status()
        response = httpx.post(self.base_url + "/interrupt", json={}, timeout=10)
        response.raise_for_status()
