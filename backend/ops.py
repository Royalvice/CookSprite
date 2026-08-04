"""Adapter registry / router for the inference backend.

Holds the configured adapters and routes an (op, model_id) to the first adapter
that supports it. No silent fallback: if nothing supports the pair, raise.
"""

from __future__ import annotations

import os
from typing import Any

from .adapters.stub import StubAdapter


class AdapterRouter:
    def __init__(self, adapters: list[Any]) -> None:
        self._adapters = adapters

    def route(self, op: str, model_id: str) -> Any:
        for adapter in self._adapters:
            if adapter.supports(op, model_id):
                return adapter
        raise LookupError(f"no adapter serves op={op!r} model_id={model_id!r}")

    def run(self, op: str, model_id: str, inputs: dict, params: dict) -> dict:
        return self.route(op, model_id).run(op, model_id, inputs, params)

    def models(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for adapter in self._adapters:
            for m in adapter.models():
                out.append({**m, "adapter": adapter.name})
        return out


def build_default_router() -> AdapterRouter:
    """Configure adapters from the environment.

    - Always includes the deterministic stub (dev).
    - If COOKSPRITE_VLLM_URL is set, add the vLLM-Omni adapter (H20 / prod),
      preferred over the stub for the models it serves.
    """
    adapters: list[Any] = []
    vllm_url = os.environ.get("COOKSPRITE_VLLM_URL")
    if vllm_url:
        from .adapters.vllm_omni import VLLMOmniAdapter

        adapters.append(VLLMOmniAdapter(vllm_url))
    adapters.append(StubAdapter())
    return AdapterRouter(adapters)
