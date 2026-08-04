"""Deterministic stub adapter — the development inference backend.

Produces a structured, prompt-seeded image (NOT random noise) so the downstream
pipeline (pixelize, crop, normal-estimate, preview) has real content to work on,
and so tests are reproducible. This is what lets the whole four-layer system run
end-to-end on a machine with no GPU. On the H20 the vLLM-Omni adapter replaces
it behind the identical contract.
"""

from __future__ import annotations

import base64
import hashlib
import io
from typing import Any

import numpy as np
from PIL import Image as PILImage


class StubAdapter:
    name = "stub"

    _MODELS = {"stub-image": ["text2img", "img2img"]}

    def supports(self, op: str, model_id: str) -> bool:
        return model_id in self._MODELS and op in self._MODELS[model_id]

    def models(self) -> list[dict[str, Any]]:
        return [{"model_id": m, "ops": ops} for m, ops in self._MODELS.items()]

    def run(self, op: str, model_id: str, inputs: dict, params: dict) -> dict:
        if op == "text2img":
            w = int(params.get("width", 512))
            h = int(params.get("height", 512))
            seed = int(params.get("seed", 0))
            prompt = str(inputs.get("prompt", ""))
            png = _render_prompt_glyph(prompt, w, h, seed)
        elif op == "img2img":
            # Re-tint the input deterministically by the prompt.
            src_b64 = inputs.get("image_b64", "")
            prompt = str(inputs.get("prompt", ""))
            png = _retint(src_b64, prompt)
        else:
            raise ValueError(f"stub adapter does not support op {op}")
        return {"outputs": [{"png_b64": base64.b64encode(png).decode("ascii")}], "meta": {"adapter": "stub"}}


def _seeded_rgb(prompt: str, seed: int) -> tuple[int, int, int]:
    digest = hashlib.sha256(f"{prompt}:{seed}".encode()).digest()
    return digest[0], digest[1], digest[2]


def _render_prompt_glyph(prompt: str, w: int, h: int, seed: int) -> bytes:
    """Draw a centered blob with a prompt-derived color on a soft background,
    plus a few shaded circles so there is real luminance variation for the
    normal estimator. Deterministic in (prompt, w, h, seed)."""
    r, g, b = _seeded_rgb(prompt, seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2.0, h / 2.0
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    radius = min(w, h) * 0.36

    # Background: dark, subtly varying.
    bg = np.zeros((h, w, 3), dtype=np.float32)
    bg[..., 0] = 18 + 6 * np.sin(xx / max(1.0, w) * 6.28)
    bg[..., 1] = 20
    bg[..., 2] = 26 + 6 * np.cos(yy / max(1.0, h) * 6.28)

    # Body: a shaded sphere-like blob (Lambert-ish falloff → good height field).
    inside = dist <= radius
    shade = np.clip(1.0 - (dist / max(1.0, radius)) ** 2, 0.0, 1.0)
    body = np.zeros((h, w, 3), dtype=np.float32)
    body[..., 0] = r * (0.4 + 0.6 * shade)
    body[..., 1] = g * (0.4 + 0.6 * shade)
    body[..., 2] = b * (0.4 + 0.6 * shade)

    rgb = np.where(inside[..., None], body, bg)

    alpha = np.where(inside, 255, 0).astype(np.uint8)
    rgba = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), alpha])

    buf = io.BytesIO()
    PILImage.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _retint(src_b64: str, prompt: str) -> bytes:
    raw = base64.b64decode(src_b64)
    pil = PILImage.open(io.BytesIO(raw)).convert("RGBA")
    arr = np.array(pil).astype(np.float32)
    r, g, b = _seeded_rgb(prompt, 0)
    tint = np.array([r, g, b, 255], dtype=np.float32) / 255.0
    arr[..., :3] = np.clip(arr[..., :3] * (0.5 + 0.5 * tint[:3]), 0, 255)
    out = arr.astype(np.uint8)
    buf = io.BytesIO()
    PILImage.fromarray(out, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()
