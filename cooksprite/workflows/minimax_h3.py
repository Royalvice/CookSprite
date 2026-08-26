"""MiniMax H3 FL2VA first/last-frame graph."""

from __future__ import annotations

from typing import Any


def minimax_h3_first_last_graph() -> dict[str, dict[str, Any]]:
    """Return the fixed 512x512, 124-frame H3 graph.

    The source slot is wired to both keyframe inputs intentionally.  The graph
    returns a Comfy IMAGE batch; CookSprite's FrameSeq finalizer persists each
    decoded frame and creates the public manifest.
    """

    return {
        "model": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                "weight_dtype": "default",
            },
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                "type": "minimax",
                "device": "default",
            },
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"},
        },
        "source_scale": {
            "class_type": "ImageScale",
            "inputs": {
                "image": "",
                "upscale_method": "lanczos",
                "width": 512,
                "height": 512,
                "crop": "disabled",
            },
        },
        "h3": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["clip", 0],
                "vae": ["vae", 0],
                "prompt": "",
                "width": 512,
                "height": 512,
                "length": 124,
                "first_frame": ["source_scale", 0],
                "last_frame": ["source_scale", 0],
            },
        },
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        "sampler_select": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "res_multistep"},
        },
        "schedule": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["model", 0],
                "scheduler": "simple",
                "steps": 20,
                "denoise": 1.0,
            },
        },
        "guide": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["model", 0], "conditioning": ["h3", 0]},
        },
        "latent": {"class_type": "EmptyMiniMaxH3LatentAV", "inputs": {"width": 512, "height": 512, "length": 124}},
        "sample": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["noise", 0],
                "guider": ["guide", 0],
                "sampler": ["sampler_select", 0],
                "sigmas": ["schedule", 0],
                "latent_image": ["h3", 1],
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]},
        },
    }


__all__ = ["minimax_h3_first_last_graph"]
