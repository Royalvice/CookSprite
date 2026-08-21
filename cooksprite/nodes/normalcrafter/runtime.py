"""Bounded-memory NormalCrafter inference owned by the ComfyUI process.

The API never imports this module.  A cached Diffusers pipeline remains on CPU
between jobs; a job moves it to the Comfy-selected GPU once, performs bounded
overlapping-window inference, then returns it to CPU and softly clears only
the cache.  It never unloads unrelated ComfyUI models.
"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .geometry import SpatialTransform, prepare_rgba, restore_normal

Progress = Callable[[str, int, int], None]

_PIPE_LOCK = threading.RLock()
_PIPE_CACHE: dict[str, Any] = {}


def register_model_folder() -> None:
    """Expose the Diffusers bundle to ComfyUI's normal model discovery API."""

    try:
        import folder_paths
    except ImportError:  # pragma: no cover - only absent in API/unit-test processes.
        return
    folder = "normalcrafter"
    known = getattr(folder_paths, "folder_names_and_paths", {})
    if folder not in known:
        folder_paths.add_model_folder_path(
            folder,
            str(Path(folder_paths.models_dir) / folder),
            is_default=True,
        )


def _enable_xformers_if_available(pipe) -> bool:
    """Use an already-compatible xFormers install without requiring one."""

    try:
        from diffusers.utils.import_utils import is_xformers_available

        if not is_xformers_available():
            return False
        pipe.enable_xformers_memory_efficient_attention()
    except (ImportError, AttributeError, RuntimeError, ValueError):
        return False
    return True


@dataclass
class NormalSequenceResult:
    """Disk-backed normal stream consumed immediately by ``CS_StoreArtifact``."""

    temporary: tempfile.TemporaryDirectory[str]
    frames: tuple[tuple[Path, Path], ...]

    def iter_normal_frames(self) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        for normal_path, alpha_path in self.frames:
            yield (
                np.load(normal_path, allow_pickle=False),
                np.load(alpha_path, allow_pickle=False),
            )

    def close(self) -> None:
        self.temporary.cleanup()


def _normalcrafter_root() -> Path:
    """Resolve the explicit local bundle and register its Comfy model folder."""

    import folder_paths

    folder = "normalcrafter"
    register_model_folder()
    try:
        path = folder_paths.get_full_path_or_raise(folder, "normalcrafter-v1/model_index.json")
    except Exception as exc:
        raise RuntimeError(
            "NormalCrafter bundle is incomplete; install the pinned normalcrafter-v1 model bundle first"
        ) from exc
    return Path(path).parent


def _load_pipe(root: Path):
    """Load the pinned full local snapshot, never contacting Hugging Face."""

    key = str(root.resolve())
    cached = _PIPE_CACHE.get(key)
    if cached is not None:
        return cached
    import torch
    from diffusers import AutoencoderKLTemporalDecoder, StableVideoDiffusionPipeline

    from .unet import NormalCrafterUNet

    unet = NormalCrafterUNet.from_pretrained(
        root,
        subfolder="unet",
        torch_dtype=torch.float16,
        local_files_only=True,
        low_cpu_mem_usage=True,
    )
    vae = AutoencoderKLTemporalDecoder.from_pretrained(
        root,
        subfolder="vae",
        torch_dtype=torch.float16,
        local_files_only=True,
        low_cpu_mem_usage=True,
    )
    pipe = StableVideoDiffusionPipeline.from_pretrained(
        root,
        unet=unet,
        vae=vae,
        torch_dtype=torch.float16,
        variant="fp16",
        local_files_only=True,
        low_cpu_mem_usage=True,
    )
    # Optional only: use xFormers when the existing Comfy runtime has a
    # compatible build.  It is never installed or required by this package.
    _enable_xformers_if_available(pipe)
    pipe.to("cpu")
    _PIPE_CACHE[key] = pipe
    return pipe


def _prepared_tensor(frames: list[np.ndarray], device):
    import torch

    return torch.from_numpy(np.stack(frames, axis=0)).to(device=device, dtype=torch.float32)


def _image_embeddings(pipe, frames, device):
    """Original NormalCrafter CLIP preprocessing for a T-frame RGB tensor."""

    from torch.nn import functional

    pixels = frames.permute(0, 3, 1, 2)
    resized = functional.interpolate(
        pixels * 2.0 - 1.0,
        size=(224, 224),
        mode="bicubic",
        align_corners=True,
        antialias=True,
    )
    features = pipe.feature_extractor(
        images=(resized + 1.0) * 0.5,
        do_normalize=True,
        do_center_crop=False,
        do_resize=False,
        do_rescale=False,
        return_tensors="pt",
    ).pixel_values.to(device=device, dtype=next(pipe.image_encoder.parameters()).dtype)
    embeddings = pipe.image_encoder(features).image_embeds
    return embeddings.unsqueeze(1) if embeddings.ndim < 3 else embeddings


def _vae_latents(pipe, frames, decode_chunk_size: int, device):
    """Encode a temporal window in bounded VAE chunks, matching upstream semantics."""

    import torch

    needs_upcast = pipe.vae.dtype == torch.float16 and bool(pipe.vae.config.force_upcast)
    if needs_upcast:
        pipe.vae.to(dtype=torch.float32)
    try:
        pixels = frames.permute(0, 3, 1, 2).to(device=device, dtype=pipe.vae.dtype) * 2.0 - 1.0
        latents = [
            pipe.vae.encode(pixels[start : start + decode_chunk_size]).latent_dist.mode()
            for start in range(0, pixels.shape[0], decode_chunk_size)
        ]
        return torch.cat(latents, dim=0)
    finally:
        if needs_upcast:
            pipe.vae.to(dtype=torch.float16)


def _infer_window(
    pipe,
    frames: list[np.ndarray],
    *,
    previous: Any | None,
    decode_chunk_size: int,
    device,
):
    """Run the authors' one-step zero-latent denoiser for one 14-frame window."""

    import torch

    tensor = _prepared_tensor(frames, device)
    embeddings = _image_embeddings(pipe, tensor, device)
    image_latents = _vae_latents(pipe, tensor, decode_chunk_size, device).to(embeddings.dtype)
    image_latents = image_latents.unsqueeze(0)
    batch_size, num_frames = 1, tensor.shape[0]
    added_time_ids = pipe._get_add_time_ids(
        7,
        127,
        0.0,
        embeddings.dtype,
        batch_size,
        1,
        False,
    ).to(device)
    pipe.scheduler.set_timesteps(1, device=device)
    timestep = pipe.scheduler.timesteps[0]
    latents = pipe.prepare_latents(
        batch_size,
        num_frames,
        pipe.unet.config.in_channels,
        tensor.shape[1],
        tensor.shape[2],
        embeddings.dtype,
        device,
        None,
    )
    latents.zero_()
    if previous is not None:
        latents[:, : previous.shape[1]] = previous
    model_input = pipe.scheduler.scale_model_input(latents, timestep)
    model_input = torch.cat([model_input, image_latents], dim=2)
    noise = pipe.unet(
        model_input,
        timestep,
        encoder_hidden_states=embeddings,
        added_time_ids=added_time_ids,
        return_dict=False,
    )[0]
    return pipe.scheduler.step(noise, timestep, latents).prev_sample


def _decode(pipe, latents, decode_chunk_size: int) -> np.ndarray:
    """Decode latents into the original NormalCrafter [-1, 1] normal range."""

    decoded = pipe.decode_latents(latents, latents.shape[1], decode_chunk_size)
    frames = pipe.video_processor.postprocess_video(video=decoded, output_type="np")
    value = np.asarray(frames, dtype=np.float32)
    if value.ndim != 5 or value.shape[0] != 1:
        raise RuntimeError("NormalCrafter VAE returned an unexpected video shape")
    return value[0] * 2.0 - 1.0


def _window_starts(frame_count: int, window_size: int, step: int) -> list[int]:
    if not 1 <= frame_count <= 240:
        raise ValueError("NormalCrafter accepts 1 to 240 frames")
    if not 2 <= window_size <= 32:
        raise ValueError("window_size must be between 2 and 32")
    if not 1 <= step <= window_size:
        raise ValueError("time_step_size must be between 1 and window_size")
    if frame_count <= window_size:
        return [0]
    last = frame_count - window_size
    starts = list(range(0, last + 1, step))
    if starts[-1] != last:
        starts.append(last)
    return starts


def _merge_previous(previous, current):
    """Apply the upstream deterministic linear blend to one overlap."""

    import torch

    overlap = previous.shape[1]
    if overlap <= 0:
        return current
    weight = torch.linspace(
        1.0,
        0.0,
        overlap + 2,
        device=current.device,
        dtype=current.dtype,
    )[1:-1].view(1, -1, 1, 1, 1)
    current[:, :overlap] = previous * weight + current[:, :overlap] * (1.0 - weight)
    return current


def _read_prepared(path: Path) -> np.ndarray:
    return np.asarray(np.load(path, mmap_mode="r", allow_pickle=False), dtype=np.float32)


def infer_sequence(
    frames: Iterable[np.ndarray],
    *,
    max_resolution: int = 1024,
    window_size: int = 14,
    time_step_size: int = 10,
    decode_chunk_size: int = 7,
    strength: float = 1.0,
    flip_y: bool = False,
    progress: Progress | None = None,
) -> NormalSequenceResult:
    """Infer a typed normal stream from RGBA frames using bounded GPU memory."""

    if not 1 <= int(decode_chunk_size) <= 32:
        raise ValueError("decode_chunk_size must be between 1 and 32")
    temporary = tempfile.TemporaryDirectory(prefix="cooksprite-normalcrafter-")
    root = Path(temporary.name)
    prepared_paths: list[Path] = []
    alpha_paths: list[Path] = []
    transform: SpatialTransform | None = None
    try:
        for index, rgba in enumerate(frames):
            if index >= 240:
                raise ValueError("NormalCrafter accepts at most 240 frames")
            prepared, alpha, current = prepare_rgba(rgba, max_resolution=int(max_resolution))
            if transform is None:
                transform = current
            elif current != transform:
                raise ValueError("NormalCrafter FrameSeq frames must share one canvas")
            prepared_path = root / f"prepared-{index:04d}.npy"
            alpha_path = root / f"alpha-{index:04d}.npy"
            np.save(prepared_path, prepared.astype(np.float16), allow_pickle=False)
            np.save(alpha_path, alpha.astype(np.float32), allow_pickle=False)
            prepared_paths.append(prepared_path)
            alpha_paths.append(alpha_path)
            if progress:
                progress("Preparing sequence", index + 1, 0)
        if not prepared_paths or transform is None:
            raise ValueError("NormalCrafter received no frames")

        import torch
        from comfy import model_management

        output_paths: list[tuple[Path, Path]] = []
        with _PIPE_LOCK:
            pipe = _load_pipe(_normalcrafter_root())
            device = model_management.get_torch_device()
            pipe.to(device)
            try:
                starts = _window_starts(len(prepared_paths), int(window_size), int(time_step_size))
                previous = None
                emitted = 0
                with torch.inference_mode():
                    for window_index, start in enumerate(starts):
                        source_indices = list(
                            range(start, min(start + int(window_size), len(prepared_paths)))
                        )
                        window = [_read_prepared(prepared_paths[index]) for index in source_indices]
                        while len(window) < int(window_size):
                            window.append(window[-1])
                        current = _infer_window(
                            pipe,
                            window,
                            previous=previous,
                            decode_chunk_size=int(decode_chunk_size),
                            device=device,
                        )
                        if previous is not None:
                            current = _merge_previous(previous, current)
                        next_start = (
                            starts[window_index + 1]
                            if window_index + 1 < len(starts)
                            else len(prepared_paths)
                        )
                        emit_count = max(0, min(current.shape[1], next_start - start))
                        if window_index + 1 == len(starts):
                            emit_count = len(prepared_paths) - start
                        if emit_count:
                            decoded = _decode(pipe, current[:, :emit_count], int(decode_chunk_size))
                            for offset, prediction in enumerate(decoded):
                                frame_index = start + offset
                                normal = restore_normal(
                                    prediction,
                                    np.load(alpha_paths[frame_index], allow_pickle=False),
                                    transform,
                                    strength=float(strength),
                                    flip_y=bool(flip_y),
                                )
                                normal_path = root / f"normal-{frame_index:04d}.npy"
                                np.save(normal_path, normal.astype(np.float32), allow_pickle=False)
                                output_paths.append((normal_path, alpha_paths[frame_index]))
                                emitted += 1
                            if progress:
                                progress("Inferring temporal normals", emitted, len(prepared_paths))
                        overlap = max(0, int(window_size) - (next_start - start))
                        previous = current[:, -overlap:].detach() if overlap else None
                if emitted != len(prepared_paths):
                    raise RuntimeError("NormalCrafter did not emit every source frame")
            finally:
                # User-selected lifecycle policy: models stay cached on CPU,
                # but this job relinquishes GPU VRAM before any other task.
                pipe.to("cpu")
                model_management.soft_empty_cache()
        return NormalSequenceResult(temporary, tuple(output_paths))
    except Exception:
        temporary.cleanup()
        raise


__all__ = ["NormalSequenceResult", "infer_sequence"]
