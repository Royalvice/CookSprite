"""Concrete InferenceClient implementations for the workflow runner.

- DirectClient: calls an in-process adapter router directly (synchronous). Used
  by the CLI and tests so no HTTP server is required to run a workflow.
- HttpClient: submits to a running backend `/infer`, polls the async job to
  completion, and returns the result. Used when the backend runs separately
  (e.g. the vLLM-Omni H20 deployment).

Both satisfy `runner.InferenceClient`.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .runner import InferenceClient


class DirectClient(InferenceClient):
    def __init__(self, router: Any) -> None:
        self._router = router

    def infer(self, op: str, model_id: str, inputs: dict, params: dict) -> dict:
        return self._router.run(op, model_id, inputs, params)


class HttpClient(InferenceClient):
    def __init__(self, base_url: str, *, timeout: float = 600.0, poll_interval: float = 1.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval

    def infer(self, op: str, model_id: str, inputs: dict, params: dict) -> dict:
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            resp = client.post(
                "/infer",
                json={"op": op, "model_id": model_id, "inputs": inputs, "params": params},
            )
            resp.raise_for_status()
            job_id = resp.json()["job_id"]

            deadline = time.time() + self.timeout
            while time.time() < deadline:
                status = client.get(f"/jobs/{job_id}").json()
                if status["status"] == "done":
                    return client.get(f"/jobs/{job_id}/result").json()
                if status["status"] == "error":
                    raise RuntimeError(f"inference failed: {status.get('error')}")
                time.sleep(self.poll_interval)
            raise TimeoutError(f"inference job {job_id} timed out")
