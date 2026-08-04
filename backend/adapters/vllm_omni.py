"""vLLM-Omni production adapter (the H20 path).

vLLM-Omni serves the target image/video models (FLUX.2, WAN2.2, LTX-2,
Qwen-Image, ...) behind an OpenAI-style HTTP server with an async job/poll
model. This adapter translates a CookSprite `(op, model_id, inputs, params)`
call into a vLLM-Omni request and decodes the result back into the CookSprite
image payload contract.

It is intentionally a thin skeleton: the exact request/response schema is filled
in against the running vLLM-Omni endpoint on the H20. On a machine without that
endpoint, constructing the adapter is fine; calling `run` raises a clear error
so nothing silently falls back to a stub.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

# op -> which model families vLLM-Omni exposes it through. Extend as models land.
DEFAULT_MODEL_OPS: dict[str, list[str]] = {
    "flux.2-dev": ["text2img", "img2img"],
    "flux.2-klein": ["text2img", "img2img"],
    "qwen-image": ["text2img", "img2img"],
    "wan2.2-t2v": ["text2vid"],
    "wan2.2-i2v": ["img2vid"],
    "ltx-2": ["text2vid", "img2vid"],
}


class VLLMOmniAdapter:
    name = "vllm-omni"

    def __init__(self, base_url: str, *, timeout: float = 600.0, poll_interval: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval

    def supports(self, op: str, model_id: str) -> bool:
        return op in DEFAULT_MODEL_OPS.get(model_id, [])

    def models(self) -> list[dict[str, Any]]:
        return [{"model_id": m, "ops": ops} for m, ops in DEFAULT_MODEL_OPS.items()]

    def run(self, op: str, model_id: str, inputs: dict, params: dict) -> dict:
        """Submit to vLLM-Omni, poll to completion, decode to png_b64 outputs.

        NOTE: request/response field names are aligned to the vLLM-Omni online
        serving schema at deploy time on the H20. Kept explicit so there is no
        hidden fallback if the endpoint is unreachable."""
        payload = {
            "model": model_id,
            "op": op,
            "inputs": inputs,
            "params": params,
        }
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            submit = client.post("/v1/generations", json=payload)
            submit.raise_for_status()
            job_id = submit.json()["id"]

            deadline = time.time() + self.timeout
            while time.time() < deadline:
                status = client.get(f"/v1/generations/{job_id}")
                status.raise_for_status()
                body = status.json()
                if body.get("status") == "completed":
                    return {"outputs": self._decode(body), "meta": {"adapter": "vllm-omni", "model": model_id}}
                if body.get("status") == "failed":
                    raise RuntimeError(f"vLLM-Omni job {job_id} failed: {body.get('error')}")
                time.sleep(self.poll_interval)
            raise TimeoutError(f"vLLM-Omni job {job_id} timed out")

    @staticmethod
    def _decode(body: dict) -> list[dict]:
        outputs = []
        for item in body.get("data", []):
            if "b64_json" in item:
                outputs.append({"png_b64": item["b64_json"]})
            elif "url" in item:
                # Fetch the artifact bytes and re-encode as png_b64.
                raw = httpx.get(item["url"], timeout=120.0).content
                outputs.append({"png_b64": base64.b64encode(raw).decode("ascii")})
        return outputs
