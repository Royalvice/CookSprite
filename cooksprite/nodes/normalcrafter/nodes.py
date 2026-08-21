"""Headless ComfyUI nodes for NormalCrafter temporal normal estimation."""

from __future__ import annotations

import numpy as np

from .runtime import infer_sequence


def _progress(frame_count: int):
    """Report standard Comfy progress without depending on its browser UI."""

    try:
        from comfy.utils import ProgressBar

        bar = ProgressBar(max(1, frame_count * 2))
    except (ImportError, AttributeError):  # pragma: no cover - Comfy provides this at runtime.
        return lambda _phase, _current, _total: None

    def update(_phase: str, current: int, total: int) -> None:
        if total:
            bar.update_absolute(frame_count + current, frame_count * 2)
        else:
            bar.update_absolute(min(current, frame_count), frame_count * 2)

    return update


def _rgba_batch(image, mask) -> list[np.ndarray]:
    value = image.detach().cpu().numpy() if hasattr(image, "detach") else np.asarray(image)
    if value.ndim != 4 or value.shape[-1] < 3:
        raise ValueError("NormalCrafter IMAGE must have shape [batch,height,width,channels]")
    if mask is None:
        alpha = np.ones(value.shape[:3], dtype=np.float32)
    else:
        alpha = mask.detach().cpu().numpy() if hasattr(mask, "detach") else np.asarray(mask)
        if alpha.ndim == 4 and alpha.shape[-1] == 1:
            alpha = alpha[..., 0]
        if alpha.ndim == 2:
            alpha = alpha[None, ...]
        if alpha.shape[0] == 1 and value.shape[0] > 1:
            alpha = np.repeat(alpha, value.shape[0], axis=0)
        if alpha.shape != value.shape[:3]:
            raise ValueError("NormalCrafter MASK batch and canvas must match IMAGE")
    return [
        np.concatenate(
            (np.clip(frame[..., :3], 0.0, 1.0), np.clip(frame_alpha, 0.0, 1.0)[..., None]),
            axis=-1,
        )
        for frame, frame_alpha in zip(value, alpha, strict=True)
    ]


class _NormalCrafterInputs:
    @staticmethod
    def fields() -> dict:
        return {
            "max_resolution": ("INT", {"default": 1024, "min": 256, "max": 1024, "step": 64}),
            "window_size": ("INT", {"default": 14, "min": 2, "max": 32}),
            "time_step_size": ("INT", {"default": 10, "min": 1, "max": 32}),
            "decode_chunk_size": ("INT", {"default": 7, "min": 1, "max": 32}),
            "strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 4.0, "step": 0.05}),
            "flip_y": ("BOOLEAN", {"default": False}),
        }

    @staticmethod
    def infer(frames, **kwargs):
        return infer_sequence(frames, progress=_progress(len(frames)), **kwargs)


class CS_NormalCrafterSequence(_NormalCrafterInputs):
    """Infer a bounded, temporally stable normal stream from a FrameSeq."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"source": ("CS_FRAMESEQ",), **cls.fields()}}

    RETURN_TYPES = ("CS_NORMAL_SEQUENCE",)
    RETURN_NAMES = ("normal",)
    FUNCTION = "run"
    CATEGORY = "CookSprite/Normal"

    def run(
        self,
        source,
        max_resolution,
        window_size,
        time_step_size,
        decode_chunk_size,
        strength,
        flip_y,
    ):
        if source is None or not hasattr(source, "iter_rgba"):
            raise ValueError("CS_NormalCrafterSequence requires a CookSprite FrameSeq bridge value")
        result = infer_sequence(
            source.iter_rgba(),
            max_resolution=int(max_resolution),
            window_size=int(window_size),
            time_step_size=int(time_step_size),
            decode_chunk_size=int(decode_chunk_size),
            strength=float(strength),
            flip_y=bool(flip_y),
            progress=_progress(len(source.frames)),
        )
        return (result,)


class CS_NormalCrafterBatch(_NormalCrafterInputs):
    """Batch variant for a <=32-frame SpritePair graph and PixelizePair."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"image": ("IMAGE",), **cls.fields()},
            "optional": {"mask": ("MASK",)},
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("normal", "mask")
    FUNCTION = "run"
    CATEGORY = "CookSprite/Normal"

    def run(
        self,
        image,
        max_resolution,
        window_size,
        time_step_size,
        decode_chunk_size,
        strength,
        flip_y,
        mask=None,
    ):
        frames = _rgba_batch(image, mask)
        if len(frames) > 32:
            raise ValueError("CS_NormalCrafterBatch accepts at most 32 frames")
        result = infer_sequence(
            frames,
            max_resolution=int(max_resolution),
            window_size=int(window_size),
            time_step_size=int(time_step_size),
            decode_chunk_size=int(decode_chunk_size),
            strength=float(strength),
            flip_y=bool(flip_y),
            progress=_progress(len(frames)),
        )
        try:
            normals, alphas = zip(*result.iter_normal_frames(), strict=True)
            import torch

            return (
                torch.from_numpy(np.stack(normals)).to(device=image.device, dtype=image.dtype),
                torch.from_numpy(np.stack(alphas)).to(device=image.device, dtype=image.dtype),
            )
        finally:
            result.close()


__all__ = ["CS_NormalCrafterBatch", "CS_NormalCrafterSequence"]
