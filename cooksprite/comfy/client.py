"""Private ComfyUI HTTP client. Its identifiers never appear in the public API."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect


class ComfyError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "comfy_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    @classmethod
    def from_event(cls, data: dict[str, Any]) -> ComfyError:
        return cls(
            str(data.get("exception_message") or data.get("message") or "ComfyUI execution failed"),
            code="execution_error",
            details=data,
        )


EventCallback = Callable[[dict[str, Any]], None]


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
            workflow_templates = c.get(self.base_url + "/workflow_templates")
            runtime_info_response = c.get(self.base_url + "/cooksprite/runtime-info")
            models: dict[str, list[str]] = {}
            if folders.is_success and isinstance(folders.json(), list):
                for folder in folders.json():
                    response = c.get(self.base_url + f"/models/{folder}")
                    if response.is_success and isinstance(response.json(), list):
                        models[str(folder)] = response.json()
            runtime_info: dict[str, Any] | None = None
            if runtime_info_response.is_success:
                try:
                    candidate = runtime_info_response.json()
                except ValueError:
                    candidate = None
                if isinstance(candidate, dict):
                    runtime_info = candidate
        return {
            "object_info": nodes.json(),
            "system_stats": system.json(),
            "features": features.json() if features.is_success else {},
            "models": models,
            "workflow_templates": workflow_templates.json()
            if workflow_templates.is_success
            else {},
            # This endpoint is optional for external/user-owned ComfyUI.  A
            # worker-managed runtime validates it explicitly at a higher
            # layer rather than treating an absent endpoint as an HTTP failure.
            "runtime_info": runtime_info,
        }

    def runtime_info(self) -> dict[str, Any] | None:
        response = httpx.get(self.base_url + "/cooksprite/runtime-info", timeout=5)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else None

    def submit(self, graph: dict[str, Any], client_id: str | None = None) -> str:
        body: dict[str, Any] = {"prompt": graph}
        if client_id:
            body["client_id"] = client_id
        r = httpx.post(self.base_url + "/prompt", json=body, timeout=20)
        if not r.is_success:
            try:
                raw_detail = r.json()
            except ValueError:
                raw_detail = {"message": r.text}
            detail = raw_detail if isinstance(raw_detail, dict) else {"message": str(raw_detail)}
            nested_error = detail.get("error") if isinstance(detail.get("error"), dict) else {}
            error_detail = {**detail, **nested_error}
            message = error_detail.get("message") or error_detail.get("error") or r.reason_phrase
            raise ComfyError(
                str(message),
                code=str(error_detail.get("type") or "prompt_rejected"),
                details=error_detail,
            )
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
            detail = self._last_error(messages)
            message = detail.get("exception_message") or "ComfyUI execution failed"
            raise ComfyError(
                str(message),
                code="execution_error",
                details=detail or {"messages": messages},
            )
        return item

    def wait(
        self,
        prompt_id: str,
        timeout: float = 3600,
        *,
        progress: Callable[[float, str], None] | None = None,
        client_id: str | None = None,
        event: EventCallback | None = None,
    ) -> dict[str, Any]:
        if client_id:
            try:
                return self._wait_websocket(prompt_id, client_id, timeout, progress, event)
            except (OSError, TimeoutError, WebSocketException):
                # A proxy may block WebSockets. History polling remains authoritative.
                pass
        return self._wait_polling(prompt_id, timeout, progress, event)

    def _wait_polling(
        self,
        prompt_id: str,
        timeout: float,
        progress: Callable[[float, str], None] | None = None,
        event: EventCallback | None = None,
    ) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            item = self._history(prompt_id)
            if item and item.get("status", {}).get("completed"):
                self._replay_history(item, event)
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
        progress: Callable[[float, str], None] | None = None,
        on_event: EventCallback | None = None,
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
                        self._replay_history(item, on_event)
                        return item
                    continue
                if not isinstance(raw, str):
                    continue
                message = json.loads(raw)
                data = message.get("data") or {}
                if data.get("prompt_id") not in {None, prompt_id}:
                    continue
                if on_event:
                    on_event(message)
                event_type = message.get("type")
                if event_type == "progress":
                    value = float(data.get("value") or 0)
                    maximum = max(1.0, float(data.get("max") or 1))
                    if progress:
                        progress(min(1.0, value / maximum), str(data.get("node") or "sampling"))
                elif event_type == "progress_state":
                    active = next(
                        (
                            item
                            for item in (data.get("nodes") or {}).values()
                            if isinstance(item, dict)
                            and item.get("state") in {"executing", "running"}
                        ),
                        None,
                    )
                    if progress and active:
                        value = float(active.get("value") or 0)
                        maximum = max(1.0, float(active.get("max") or 1))
                        progress(
                            min(1.0, value / maximum), str(active.get("node_id") or "sampling")
                        )
                elif event_type == "executing" and data.get("node") is None:
                    item = self._history(prompt_id)
                    if item and item.get("status", {}).get("completed"):
                        self._replay_history(item, on_event)
                        return item
                elif event_type == "execution_error":
                    raise ComfyError.from_event(data)
                elif event_type == "execution_interrupted":
                    raise ComfyError(
                        "ComfyUI execution interrupted",
                        code="execution_interrupted",
                        details=data,
                    )
        raise ComfyError("ComfyUI job timed out")

    @staticmethod
    def _last_error(messages: Any) -> dict[str, Any]:
        if not isinstance(messages, list):
            return {}
        for entry in reversed(messages):
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                name, data = entry
                if name in {"execution_error", "execution_interrupted"} and isinstance(data, dict):
                    return data
        return {}

    @classmethod
    def _replay_history(cls, item: dict[str, Any], event: EventCallback | None) -> None:
        if not event:
            return
        messages = (item.get("status") or {}).get("messages") or []
        for entry in messages:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                name, data = entry
                if isinstance(data, dict):
                    event({"type": str(name), "data": data})

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
