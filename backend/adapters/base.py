"""Inference adapter contract.

An adapter implements one or more `op`s for one or more `model_id`s. The backend
routes an `/infer` request to an adapter that declares support for (op,
model_id). The result is a dict: {"outputs": [ {image payload}, ... ], "meta": {}}.

Image payloads use {"png_b64": "..."} so they are transport-agnostic.
"""

from __future__ import annotations

from typing import Any, Protocol


class InferenceAdapter(Protocol):
    """Every backend adapter (stub, vLLM-Omni, ...) implements this."""

    name: str

    def supports(self, op: str, model_id: str) -> bool:
        """Whether this adapter can serve the (op, model_id) pair."""
        ...

    def models(self) -> list[dict[str, Any]]:
        """List of {"model_id", "ops": [...]} this adapter serves."""
        ...

    def run(self, op: str, model_id: str, inputs: dict, params: dict) -> dict:
        """Execute synchronously and return {"outputs": [...], "meta": {...}}.
        The async job wrapper lives in the backend, not here."""
        ...
