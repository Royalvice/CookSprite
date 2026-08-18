"""ComfyUI-side rembg adapter for the CookSprite alpha Tool."""

from __future__ import annotations

import io
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image

REMBG_MODELS = ("u2net", "u2netp", "isnet-anime", "birefnet-general")


@lru_cache(maxsize=3)
def _session(model: str) -> Any:
    if model not in REMBG_MODELS:
        raise ValueError(f"unsupported rembg model: {model}")
    try:
        from rembg import new_session
    except ImportError as exc:  # pragma: no cover - exercised in runtime setup
        raise RuntimeError(
            "CookSprite background removal requires rembg and onnxruntime-gpu in ComfyUI"
        ) from exc
    try:
        return new_session(model)
    except Exception as exc:
        raise RuntimeError(f"failed to load rembg model '{model}': {exc}") from exc


def remove_background_batch(
    image: np.ndarray,
    model: str = "u2net",
    alpha_matting: bool = False,
    foreground_threshold: int = 240,
    background_threshold: int = 10,
    erode_size: int = 10,
    batch_size: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    value = np.asarray(image, dtype=np.float32)
    if value.ndim != 4 or value.shape[-1] < 3:
        raise ValueError("IMAGE must have shape [batch,height,width,channels]")
    if model not in REMBG_MODELS:
        raise ValueError(f"unsupported rembg model: {model}")
    if int(batch_size) < 1:
        raise ValueError("batch_size must be at least 1")
    session = _session(model)
    try:
        from rembg import remove
    except ImportError as exc:  # pragma: no cover - _session normally catches this first
        raise RuntimeError(
            "CookSprite background removal requires rembg and onnxruntime-gpu in ComfyUI"
        ) from exc
    rgb = np.clip(value[..., :3], 0.0, 1.0)
    source_alpha = value[..., 3] if value.shape[-1] > 3 else None
    foreground: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    for start in range(0, len(rgb), int(batch_size)):
        for index in range(start, min(len(rgb), start + int(batch_size))):
            source = io.BytesIO()
            Image.fromarray(np.rint(rgb[index] * 255.0).astype(np.uint8), "RGB").save(source, format="PNG")
            try:
                output = remove(
                    source.getvalue(),
                    session=session,
                    alpha_matting=bool(alpha_matting),
                    alpha_matting_foreground_threshold=int(foreground_threshold),
                    alpha_matting_background_threshold=int(background_threshold),
                    alpha_matting_erode_size=int(erode_size),
                )
            except Exception as exc:
                raise RuntimeError(f"rembg inference failed for model '{model}': {exc}") from exc
            rgba = np.asarray(Image.open(io.BytesIO(output)).convert("RGBA"), dtype=np.float32) / 255.0
            alpha = np.clip(rgba[..., 3], 0.0, 1.0)
            if source_alpha is not None:
                alpha *= np.clip(source_alpha[index], 0.0, 1.0)
            rgb_output = np.clip(rgba[..., :3], 0.0, 1.0)
            rgb_output[alpha <= 0.0] = 0.0
            foreground.append(rgb_output)
            masks.append(alpha)
    return np.stack(foreground, axis=0).astype(np.float32), np.stack(masks, axis=0).astype(np.float32)
