"""PNG <-> base64 and PNG <-> numpy helpers, shared by ops, workspace, server.

Keeping this separate avoids a circular import between ops and the workspace.
"""

from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image as PILImage

from .types import ensure_rgba


def encode_png_bytes(pixels: np.ndarray) -> bytes:
    buf = io.BytesIO()
    PILImage.fromarray(ensure_rgba(pixels), mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def encode_image_b64(pixels: np.ndarray) -> str:
    return base64.b64encode(encode_png_bytes(pixels)).decode("ascii")


def decode_image_b64(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    pil = PILImage.open(io.BytesIO(raw)).convert("RGBA")
    return ensure_rgba(np.array(pil))
