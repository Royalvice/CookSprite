"""Lotus Normal D v1.1 lowering adapted from ComfyUI's official Lotus graph.

Comfy-Org publishes a Lotus depth template, while the official Lotus authors
publish the Normal D v1.1 weights.  This graph keeps the official one-step
Lotus sampling topology, swaps in the official normal weights, removes the
depth-only image inversion, and brackets inference with CookSprite's
alpha/size adapters.
"""

from __future__ import annotations

from typing import Any

LOTUS_NORMAL_BUNDLE_ID = "lotus-normal-d-v1-1"
LOTUS_NORMAL_MODEL = "lotus-normal-d-v1-1.safetensors"
LOTUS_NORMAL_VAE = "lotus-normal-d-v1-1-vae.safetensors"

LOTUS_NORMAL_PROVENANCE = {
    "model": {
        "repository": "https://huggingface.co/jingheya/lotus-normal-d-v1-1",
        "revision": "1d4d52149432060efabb0827e0a9fe4a0b63e817",
    },
    "topology": {
        "repository": "https://github.com/Comfy-Org/workflow_templates",
        "commit": "1121504798345b1bb4e6350991f90512c4ba1ed9",
        "template": "templates/image_lotus_depth_v1_1.json",
    },
    "comfyui": "v0.33.2",
    "notes": (
        "Official Lotus depth topology adapted to the official Normal D v1.1 "
        "weights; the depth-only ImageInvert node is intentionally omitted."
    ),
}

LOTUS_NORMAL_BUNDLE: dict[str, Any] = {
    "label": "Lotus Normal D v1.1 · BF16",
    "license": "Apache-2.0",
    "recommended": False,
    "provenance": LOTUS_NORMAL_PROVENANCE,
    "files": [
        {
            "folder": "diffusion_models",
            "name": LOTUS_NORMAL_MODEL,
            "url": (
                "https://huggingface.co/jingheya/lotus-normal-d-v1-1/resolve/"
                "1d4d52149432060efabb0827e0a9fe4a0b63e817/unet/"
                "diffusion_pytorch_model.safetensors"
            ),
            "size": 3_470_311_272,
            "sha256": "91bf02e9b19d5702e5825ce7ac461ff8b4af5830240b159cfbdae985159b053a",
        },
        {
            "folder": "vae",
            "name": LOTUS_NORMAL_VAE,
            "url": (
                "https://huggingface.co/jingheya/lotus-normal-d-v1-1/resolve/"
                "1d4d52149432060efabb0827e0a9fe4a0b63e817/vae/"
                "diffusion_pytorch_model.safetensors"
            ),
            "size": 167_335_342,
            "sha256": "3e4c08995484ee61270175e9e7a072b66a6e4eeb5f0c266667fe1f45b90daf9a",
        },
    ],
}


def lotus_normal_tool_graph() -> dict[str, Any]:
    """Return the sealed Tool graph and its semantic input/output contract."""

    workflow = {
        "prepare": {
            "class_type": "CS_LotusNormalPrepare",
            "inputs": {"image": ""},
        },
        "model": {
            "class_type": "CS_LotusModelLoader",
            "inputs": {"model_name": LOTUS_NORMAL_MODEL, "precision": "bf16"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": LOTUS_NORMAL_VAE},
        },
        "conditioning": {"class_type": "LotusConditioning", "inputs": {}},
        "encode": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["prepare", 0], "vae": ["vae", 0]},
        },
        "guider": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["model", 0], "conditioning": ["conditioning", 0]},
        },
        "noise": {"class_type": "DisableNoise", "inputs": {}},
        "scheduler": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["model", 0],
                "scheduler": "normal",
                "steps": 1,
                "denoise": 1.0,
            },
        },
        "sigma": {
            "class_type": "SetFirstSigma",
            "inputs": {"sigmas": ["scheduler", 0], "sigma": 999.0000000000002},
        },
        "sampler": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
        "sample": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["noise", 0],
                "guider": ["guider", 0],
                "sampler": ["sampler", 0],
                "sigmas": ["sigma", 0],
                "latent_image": ["encode", 0],
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]},
        },
        "finalize": {
            "class_type": "CS_LotusNormalFinalize",
            "inputs": {
                "prediction": ["decode", 0],
                "reference": ["prepare", 2],
                "mask": ["prepare", 1],
                "strength": 1.0,
                "flip_y": False,
            },
        },
    }
    return {
        "workflow": workflow,
        "slots": {
            "image": "prepare.image",
            "mask": "prepare.mask",
            "strength": "finalize.strength",
            "flip_y": "finalize.flip_y",
        },
        "outputs": [["finalize", 0], ["finalize", 1]],
        "output": ["finalize", 0],
        "shared_nodes": [
            "model",
            "vae",
            "conditioning",
            "guider",
            "noise",
            "scheduler",
            "sigma",
            "sampler",
        ],
    }


__all__ = [
    "LOTUS_NORMAL_BUNDLE",
    "LOTUS_NORMAL_BUNDLE_ID",
    "LOTUS_NORMAL_MODEL",
    "LOTUS_NORMAL_PROVENANCE",
    "LOTUS_NORMAL_VAE",
    "lotus_normal_tool_graph",
]
