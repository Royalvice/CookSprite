"""Private ComfyUI HTTP client. Its identifiers never appear in the public API."""

from __future__ import annotations

import time
from typing import Any

import httpx


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

    def submit(self, graph: dict[str, Any]) -> str:
        r = httpx.post(self.base_url + "/prompt", json={"prompt": graph}, timeout=20)
        r.raise_for_status()
        body = r.json()
        if "prompt_id" not in body:
            raise ComfyError(f"ComfyUI did not return prompt_id: {body}")
        return body["prompt_id"]

    def wait(self, prompt_id: str, timeout: float = 3600) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r = httpx.get(self.base_url + f"/history/{prompt_id}", timeout=10)
            r.raise_for_status()
            history = r.json()
            item = history.get(prompt_id)
            if item and item.get("status", {}).get("status_str") == "error":
                messages = item.get("status", {}).get("messages", [])
                raise ComfyError(f"ComfyUI execution failed: {messages}")
            if item and item.get("status", {}).get("completed"):
                return item
            time.sleep(0.35)
        raise ComfyError("ComfyUI job timed out")

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
