"""Pure API-format lowerings derived from the official FLUX.2 Klein templates.

The source templates are UI-format ComfyUI subgraphs.  CookSprite stores the
small, API-format graph needed by its sealed-tool compiler so the API never
imports ComfyUI or fetches a workflow at startup.  The node topology follows
the official 4B distilled text-to-image and 4B/9B distilled image-edit
templates; the 9B text-to-image graph uses the same official graph with the
FP8 distilled loader and distilled sampler settings.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

FLUX2_TEMPLATE_PROVENANCE = {
    "repository": "https://github.com/Comfy-Org/workflow_templates",
    "commit": "9b5fbc54d31adf325860cd1dbde9b627f96706e8",
    "templates": {
        "t2i_4b": "templates/image_flux2_klein_text_to_image.json",
        "t2i_9b": "templates/image_flux2_text_to_image_9b.json",
        "i2i_4b": "templates/image_flux2_klein_image_edit_4b_distilled.json",
        "i2i_9b": "templates/image_flux2_klein_image_edit_9b_distilled.json",
    },
    "comfyui": "v0.33.2",
    "notes": "Normalized API graphs retain official Flux2Scheduler, CFGGuider, ReferenceLatent, and sampler topology.",
}

FLUX2_BUNDLES: dict[str, dict[str, Any]] = {
    "flux2-klein-4b-turbo": {
        "label": "FLUX.2 Klein 4B Turbo · FP8",
        "license": "Apache-2.0",
        "recommended": False,
        "files": [
            {
                "folder": "diffusion_models",
                "name": "flux-2-klein-4b-fp8.safetensors",
                "url": "https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/main/flux-2-klein-4b-fp8.safetensors",
            },
            {
                "folder": "text_encoders",
                "name": "qwen_3_4b.safetensors",
                "url": "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors",
            },
            {
                "folder": "vae",
                "name": "flux2-vae.safetensors",
                "url": "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors",
            },
        ],
    },
    "flux2-klein-9b-turbo": {
        "label": "FLUX.2 Klein 9B Turbo · FP8",
        "license": "BFL FLUX.2 [klein] 9B license",
        "recommended": True,
        "files": [
            {
                "folder": "diffusion_models",
                "name": "flux-2-klein-9b-fp8.safetensors",
                "url": "https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8/resolve/main/flux-2-klein-9b-fp8.safetensors",
            },
            {
                "folder": "text_encoders",
                "name": "qwen_3_8b_fp8mixed.safetensors",
                "url": "https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors",
            },
            {
                "folder": "vae",
                "name": "full_encoder_small_decoder.safetensors",
                "url": "https://huggingface.co/black-forest-labs/FLUX.2-small-decoder/resolve/main/full_encoder_small_decoder.safetensors",
            },
        ],
    },
}

_COMMON_NODES = {
    "model": {"class_type": "UNETLoader", "inputs": {}},
    "clip": {"class_type": "CLIPLoader", "inputs": {}},
    "vae": {"class_type": "VAELoader", "inputs": {}},
    "positive": {"class_type": "CLIPTextEncode", "inputs": {}},
    "negative": {"class_type": "ConditioningZeroOut", "inputs": {}},
    "width": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
    "height": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
    "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {}},
    "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
    "sampler_select": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
    "schedule": {"class_type": "Flux2Scheduler", "inputs": {}},
    "guider": {"class_type": "CFGGuider", "inputs": {}},
    "sample": {"class_type": "SamplerCustomAdvanced", "inputs": {}},
    "decode": {"class_type": "VAEDecode", "inputs": {}},
}


def _link(node: dict[str, Any], name: str, source: str, output: int = 0) -> None:
    node["inputs"][name] = [source, output]


def _graph_base(bundle_id: str, *, cfg: float = 1.0) -> dict[str, Any]:
    bundle = FLUX2_BUNDLES[bundle_id]
    files = {item["folder"]: item["name"] for item in bundle["files"]}
    nodes = deepcopy(_COMMON_NODES)
    nodes["model"]["inputs"] = {
        "unet_name": files["diffusion_models"],
        "weight_dtype": "default",
    }
    nodes["clip"]["inputs"] = {
        "clip_name": files["text_encoders"],
        "type": "flux2",
        "device": "default",
    }
    nodes["vae"]["inputs"] = {"vae_name": files["vae"]}
    nodes["positive"]["inputs"] = {"clip": ["clip", 0], "text": ""}
    nodes["negative"]["inputs"] = {"conditioning": ["positive", 0]}
    nodes["latent"]["inputs"] = {
        "width": ["width", 0],
        "height": ["height", 0],
        "batch_size": 1,
    }
    nodes["schedule"]["inputs"] = {
        "steps": 4,
        "width": ["width", 0],
        "height": ["height", 0],
    }
    nodes["guider"]["inputs"] = {
        "model": ["model", 0],
        "positive": ["positive", 0],
        "negative": ["negative", 0],
        "cfg": cfg,
    }
    nodes["sample"]["inputs"] = {
        "noise": ["noise", 0],
        "guider": ["guider", 0],
        "sampler": ["sampler_select", 0],
        "sigmas": ["schedule", 0],
        "latent_image": ["latent", 0],
    }
    nodes["decode"]["inputs"] = {"samples": ["sample", 0], "vae": ["vae", 0]}
    return nodes


def _t2i_graph(bundle_id: str) -> dict[str, Any]:
    return _graph_base(bundle_id)


def _i2i_graph(bundle_id: str, reference_count: int) -> dict[str, Any]:
    if not 1 <= reference_count <= 4:
        raise ValueError("FLUX.2 Klein supports one to four reference images")
    nodes = _graph_base(bundle_id)
    nodes["latent"]["inputs"]["batch_size"] = 1
    previous_positive = "positive"
    previous_negative = "negative"
    for index in range(1, reference_count + 1):
        scale_id = f"scale_ref_{index}"
        encode_id = f"encode_ref_{index}"
        positive_id = f"positive_ref_{index}"
        negative_id = f"negative_ref_{index}"
        nodes[scale_id] = {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {
                "image": "",
                "upscale_method": "nearest-exact",
                "megapixels": 1.0,
                "resolution_steps": 1,
            },
        }
        nodes[encode_id] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": [scale_id, 0], "vae": ["vae", 0]},
        }
        nodes[positive_id] = {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": [previous_positive, 0], "latent": [encode_id, 0]},
        }
        nodes[negative_id] = {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": [previous_negative, 0], "latent": [encode_id, 0]},
        }
        previous_positive = positive_id
        previous_negative = negative_id
    nodes["guider"]["inputs"]["positive"] = [previous_positive, 0]
    nodes["guider"]["inputs"]["negative"] = [previous_negative, 0]
    return nodes


def flux2_klein_graph(
    bundle_id: str,
    mode: str,
    *,
    reference_count: int = 0,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Return graph, semantic slots, and slot types for one Recipe variant."""

    if bundle_id not in FLUX2_BUNDLES:
        raise KeyError(f"unknown FLUX.2 Klein bundle: {bundle_id}")
    if mode == "t2i":
        graph = _t2i_graph(bundle_id)
        references: dict[str, str] = {}
    elif mode == "i2i":
        graph = _i2i_graph(bundle_id, reference_count)
        references = {
            f"reference_{index}": f"scale_ref_{index}.image"
            for index in range(1, reference_count + 1)
        }
    else:
        raise ValueError(f"unsupported FLUX.2 Klein mode: {mode}")
    slots = {
        "prompt": "positive.text",
        "seed": "noise.noise_seed",
        "count": "latent.batch_size",
        "width": "width.value",
        "height": "height.value",
        **references,
    }
    slot_types = {
        "prompt": "Text",
        "seed": "Number",
        "count": "Number",
        "width": "Number",
        "height": "Number",
        **{name: "Image" for name in references},
    }
    return graph, slots, slot_types


__all__ = ["FLUX2_BUNDLES", "FLUX2_TEMPLATE_PROVENANCE", "flux2_klein_graph"]
