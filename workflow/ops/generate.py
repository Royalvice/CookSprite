"""Inference Op components. These do NOT run models themselves — they call the
backend `/infer` API via the RunContext, which routes to whatever adapter the
backend is configured with (stub for dev, vLLM-Omni on the H20 for prod).

The op keeps the model choice open: `model_id` is a parameter, and one op may be
served by many models. Outputs are decoded into typed artifacts.
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np
from PIL import Image as PILImage

from ..component import ParamSpec, Port, RunContext, op
from ..types import Artifact, Image, ensure_rgba


def _decode_image(payload: dict[str, Any]) -> Image:
    """Decode one /infer output entry into an Image.

    Supported forms (adapter-agnostic):
      - {"png_b64": "..."}         base64 PNG bytes
      - {"rgba": [...], "w", "h"}  raw uint8 RGBA flat list
    """
    if "png_b64" in payload:
        raw = base64.b64decode(payload["png_b64"])
        import io

        pil = PILImage.open(io.BytesIO(raw)).convert("RGBA")
        return Image(pixels=ensure_rgba(np.array(pil)))
    if "rgba" in payload:
        w, h = int(payload["w"]), int(payload["h"])
        arr = np.array(payload["rgba"], dtype=np.uint8).reshape(h, w, 4)
        return Image(pixels=arr)
    raise ValueError("unrecognized /infer image payload")


@op(
    id="text2img",
    inputs=[],
    outputs=[Port("image", "image")],
    params=[
        ParamSpec("prompt", "string", "", "Text prompt"),
        ParamSpec("model_id", "string", "stub-image", "Model to use"),
        ParamSpec("width", "int", 512, "Output width"),
        ParamSpec("height", "int", 512, "Output height"),
        ParamSpec("seed", "int", 0, "Random seed"),
        ParamSpec("steps", "int", 20, "Sampling steps"),
    ],
    description="Generate an image from a text prompt via the inference backend.",
)
def text2img(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    model_id = str(params.get("model_id", "stub-image"))
    ctx.progress(0.1, f"submitting text2img to {model_id}")
    result = ctx.infer(
        op="text2img",
        model_id=model_id,
        inputs={"prompt": params.get("prompt", "")},
        params={
            "width": int(params.get("width", 512)),
            "height": int(params.get("height", 512)),
            "seed": int(params.get("seed", 0)),
            "steps": int(params.get("steps", 20)),
        },
    )
    outputs = result.get("outputs", [])
    if not outputs:
        raise RuntimeError("text2img returned no outputs")
    ctx.progress(1.0, "image generated")
    return {"image": _decode_image(outputs[0])}


@op(
    id="img2img",
    inputs=[Port("image", "image")],
    outputs=[Port("image", "image")],
    params=[
        ParamSpec("prompt", "string", "", "Text prompt"),
        ParamSpec("model_id", "string", "stub-image", "Model to use"),
        ParamSpec("strength", "float", 0.6, "Denoise strength"),
        ParamSpec("seed", "int", 0, "Random seed"),
    ],
    description="Transform an input image guided by a prompt via the backend.",
)
def img2img(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    src: Image = inputs["image"]  # type: ignore[assignment]
    model_id = str(params.get("model_id", "stub-image"))
    ctx.progress(0.1, f"submitting img2img to {model_id}")
    from ..codec import encode_image_b64

    result = ctx.infer(
        op="img2img",
        model_id=model_id,
        inputs={"prompt": params.get("prompt", ""), "image_b64": encode_image_b64(src.pixels)},
        params={"strength": float(params.get("strength", 0.6)), "seed": int(params.get("seed", 0))},
    )
    outputs = result.get("outputs", [])
    if not outputs:
        raise RuntimeError("img2img returned no outputs")
    ctx.progress(1.0, "image transformed")
    return {"image": _decode_image(outputs[0])}
