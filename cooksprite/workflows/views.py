"""Built-in API graphs for the four single-image view workflows.

These graphs are deliberately ordinary ComfyUI API graphs.  Recipes only
expose the single semantic ``source`` slot; model and camera details stay in
this runtime adapter and never cross the public Action boundary.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


VIEW_ORDER = ("front", "side", "back")
VIEW_YAWS = {"front": 0, "side": 90, "back": 180}

_IDENTITY = (
    "Preserve the exact identity, proportions, colors, materials, clothing, armor and equipment "
    "from the reference image. Show one complete full-body character, centered, with a clean plain "
    "light gray background, no floor, no cast shadow, no scenery, no text, no logo and no border."
)


def _camera_prompt(view: str, *, pure_prompt: bool = False) -> str:
    yaw = VIEW_YAWS[view]
    prefix = "Use the reference image as the single identity source. Change only the camera viewpoint. " if pure_prompt else ""
    return (
        f"{prefix}Camera contract: orthographic projection, yaw={yaw} degrees, pitch=+0 degrees, "
        f"roll=0 degrees; {view} view, eye-level camera, fixed camera. {_IDENTITY}"
    )


def _common_klein(model: str, clip: str, vae: str) -> dict[str, dict[str, Any]]:
    return {
        "model": {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "flux2", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "source_scale": {
            "class_type": "ImageScale",
            "inputs": {"image": "", "upscale_method": "nearest-exact", "width": 512, "height": 512, "crop": "disabled"},
        },
        "source_encode": {"class_type": "VAEEncode", "inputs": {"pixels": ["source_scale", 0], "vae": ["vae", 0]}},
        "sampler_select": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "schedule": {"class_type": "Flux2Scheduler", "inputs": {"steps": 4, "width": 512, "height": 512}},
    }


def klein_multi_angles_graph() -> dict[str, dict[str, Any]]:
    nodes = _common_klein(
        "flux-2-klein-9b-fp8.safetensors",
        "qwen_3_8b_fp8mixed.safetensors",
        "flux2-vae.safetensors",
    )
    nodes["lora"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"model": ["model", 0], "lora_name": "multiple-angles-flux-klein-9b.safetensors", "strength_model": 1.0},
    }
    outputs: list[str] = []
    for index, view in enumerate(VIEW_ORDER, start=1):
        pos = f"positive_{index}"
        ref = f"reference_{index}"
        neg = f"negative_{index}"
        noise = f"noise_{index}"
        guide = f"guide_{index}"
        sample = f"sample_{index}"
        decode = f"decode_{index}"
        nodes[pos] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": _camera_prompt(view)}}
        nodes[ref] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": [pos, 0], "latent": ["source_encode", 0]}}
        nodes[neg] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": [ref, 0]}}
        nodes[noise] = {"class_type": "RandomNoise", "inputs": {"noise_seed": 1000 + index}}
        nodes[guide] = {"class_type": "CFGGuider", "inputs": {"model": ["lora", 0], "positive": [ref, 0], "negative": [neg, 0], "cfg": 1.0}}
        nodes[sample] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": [noise, 0], "guider": [guide, 0], "sampler": ["sampler_select", 0], "sigmas": ["schedule", 0], "latent_image": ["latent", 0]}}
        nodes[decode] = {"class_type": "VAEDecode", "inputs": {"samples": [sample, 0], "vae": ["vae", 0]}}
        outputs.append(decode)
    nodes["batch_12"] = {"class_type": "ImageBatch", "inputs": {"image1": [outputs[0], 0], "image2": [outputs[1], 0]}}
    nodes["batch_123"] = {"class_type": "ImageBatch", "inputs": {"image1": ["batch_12", 0], "image2": [outputs[2], 0]}}
    return nodes


def pure_prompt_graph() -> dict[str, dict[str, Any]]:
    nodes = _common_klein(
        "flux-2-klein-9b-fp8.safetensors",
        "qwen_3_8b_fp8mixed.safetensors",
        "flux2-vae.safetensors",
    )
    outputs: list[str] = []
    for index, view in enumerate(VIEW_ORDER, start=1):
        pos = f"positive_{index}"
        neg = f"negative_{index}"
        ref_pos = f"reference_positive_{index}"
        ref_neg = f"reference_negative_{index}"
        noise = f"noise_{index}"
        guide = f"guide_{index}"
        sample = f"sample_{index}"
        decode = f"decode_{index}"
        nodes[pos] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": _camera_prompt(view, pure_prompt=True)}}
        nodes[neg] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": [pos, 0]}}
        nodes[ref_pos] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": [pos, 0], "latent": ["source_encode", 0]}}
        nodes[ref_neg] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": [neg, 0], "latent": ["source_encode", 0]}}
        nodes[noise] = {"class_type": "RandomNoise", "inputs": {"noise_seed": 2000 + index}}
        nodes[guide] = {"class_type": "CFGGuider", "inputs": {"model": ["model", 0], "positive": [ref_pos, 0], "negative": [ref_neg, 0], "cfg": 1.0}}
        nodes[sample] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": [noise, 0], "guider": [guide, 0], "sampler": ["sampler_select", 0], "sigmas": ["schedule", 0], "latent_image": ["latent", 0]}}
        nodes[decode] = {"class_type": "VAEDecode", "inputs": {"samples": [sample, 0], "vae": ["vae", 0]}}
        outputs.append(decode)
    nodes["batch_12"] = {"class_type": "ImageBatch", "inputs": {"image1": [outputs[0], 0], "image2": [outputs[1], 0]}}
    nodes["batch_123"] = {"class_type": "ImageBatch", "inputs": {"image1": ["batch_12", 0], "image2": [outputs[2], 0]}}
    return nodes


def tripview_graph() -> dict[str, dict[str, Any]]:
    nodes = {
        "model": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux-2-klein-9b-fp8.safetensors", "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_8b_fp8mixed.safetensors", "type": "flux2", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "source_scale": {"class_type": "ImageScale", "inputs": {"image": "", "upscale_method": "nearest-exact", "width": 512, "height": 512, "crop": "disabled"}},
        "source_encode": {"class_type": "VAEEncode", "inputs": {"pixels": ["source_scale", 0], "vae": ["vae", 0]}},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["model", 0], "lora_name": "charactersheet_tripleview_klein9b_v1.safetensors", "strength_model": 1.0}},
        "positive": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": "Convert the character in the image to a Character Sheet showing front, strict side profile and rear full-body views. Preserve the character identity, proportions, materials and distinctive features exactly. Preserve the eye-level orthographic camera height across all views. Use one plain light gray background, no floor, no cast shadow, no scenery, no text, no logo and no border."}},
        "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": "bad quality, noise, blurry, distortion, unnatural blending"}},
        "positive_ref": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["positive", 0], "latent": ["source_encode", 0]}},
        "negative_ref": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["negative", 0], "latent": ["source_encode", 0]}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 3001}},
        "sampler_select": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "schedule": {"class_type": "BasicScheduler", "inputs": {"model": ["lora", 0], "scheduler": "beta", "steps": 8, "denoise": 1.0}},
        "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": 1536, "height": 1024, "batch_size": 1}},
        "guide": {"class_type": "CFGGuider", "inputs": {"model": ["lora", 0], "positive": ["positive_ref", 0], "negative": ["negative_ref", 0], "cfg": 1.0}},
        "sample": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["noise", 0], "guider": ["guide", 0], "sampler": ["sampler_select", 0], "sigmas": ["schedule", 0], "latent_image": ["latent", 0]}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "slice": {"class_type": "CS_SliceSpriteSheet", "inputs": {"image": ["decode", 0], "columns": 3, "rows": 1, "frame_width": 512, "frame_height": 1024, "margin": 0, "spacing": 0, "exclude_empty": False}},
    }
    return _append_panel_normalization(nodes, panel_indices=(0, 1, 2), panel_width=512)


def quadview_krea_graph() -> dict[str, dict[str, Any]]:
    nodes = {
        "model": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_bf16.safetensors", "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_bf16.safetensors", "type": "krea2", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "source_scale": {"class_type": "FluxKontextImageScale", "inputs": {"image": ""}},
        "positive": {"class_type": "TextEncodeKrea2OstrisEdit", "inputs": {"clip": ["clip", 0], "prompt": "Convert the character in the image to a Character Sheet showing a face close-up, front full body, side full body and back full body views. Preserve the character identity, proportions, materials and distinctive features exactly. Preserve the eye-level orthographic camera height across all views. Use one plain light gray background, no floor, no cast shadow, no scenery, no text, no logo and no border.", "vae": ["vae", 0], "image1": ["source_scale", 0]}},
        "positive_ref": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["positive", 0], "reference_latents_method": "index_timestep_zero"}},
        "negative": {"class_type": "TextEncodeKrea2OstrisEdit", "inputs": {"clip": ["clip", 0], "prompt": "", "vae": ["vae", 0], "image1": ["source_scale", 0]}},
        "negative_ref": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["negative", 0], "reference_latents_method": "index_timestep_zero"}},
        "patch": {"class_type": "Krea2OstrisEditModelPatch", "inputs": {"model": ["model", 0], "kv_cache": True}},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["patch", 0], "lora_name": "charactersheet_quadview_krea2_v1.safetensors", "strength_model": 1.0}},
        "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": 1536, "height": 1024, "batch_size": 1}},
        "sample": {"class_type": "KSampler", "inputs": {"model": ["lora", 0], "seed": 4001, "steps": 10, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["positive_ref", 0], "negative": ["negative_ref", 0], "latent_image": ["latent", 0], "denoise": 1.0}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "slice": {"class_type": "CS_SliceSpriteSheet", "inputs": {"image": ["decode", 0], "columns": 4, "rows": 1, "frame_width": 384, "frame_height": 1024, "margin": 0, "spacing": 0, "exclude_empty": False}},
    }
    return _append_panel_normalization(nodes, panel_indices=(1, 2, 3), panel_width=384)


def _append_panel_normalization(
    nodes: dict[str, dict[str, Any]], *, panel_indices: tuple[int, int, int], panel_width: int
) -> dict[str, dict[str, Any]]:
    normalized = deepcopy(nodes)
    refs: list[str] = []
    inner_width = 256 if panel_width == 512 else 192
    pad = (512 - inner_width) // 2
    for index, panel_index in enumerate(panel_indices, start=1):
        pick = f"panel_{index}"
        scale = f"panel_scale_{index}"
        pad_node = f"panel_pad_{index}"
        normalized[pick] = {"class_type": "ImageFromBatch", "inputs": {"image": ["slice", 0], "batch_index": panel_index, "length": 1}}
        normalized[scale] = {"class_type": "ImageScale", "inputs": {"image": [pick, 0], "upscale_method": "lanczos", "width": inner_width, "height": 512, "crop": "disabled"}}
        normalized[pad_node] = {"class_type": "ImagePadForOutpaint", "inputs": {"image": [scale, 0], "left": pad, "top": 0, "right": pad, "bottom": 0, "feathering": 0}}
        refs.append(pad_node)
    normalized["batch_12"] = {"class_type": "ImageBatch", "inputs": {"image1": [refs[0], 0], "image2": [refs[1], 0]}}
    normalized["batch_123"] = {"class_type": "ImageBatch", "inputs": {"image1": ["batch_12", 0], "image2": [refs[2], 0]}}
    return normalized


__all__ = [
    "VIEW_ORDER",
    "klein_multi_angles_graph",
    "pure_prompt_graph",
    "quadview_krea_graph",
    "tripview_graph",
]
