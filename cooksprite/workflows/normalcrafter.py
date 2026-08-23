"""NormalCrafter temporal-normal model bundle and provenance.

NormalCrafter has no official ComfyUI graph.  CookSprite therefore adapts the
authors' released Diffusers pipeline as a sealed compute-node implementation,
without routing inference through the API process.  The model repository is a
complete, pinned Diffusers snapshot: it contains the SVD image encoder and
scheduler as well as the NormalCrafter UNet and VAE.
"""

from __future__ import annotations

from typing import Any

NORMALCRAFTER_BUNDLE_ID = "normalcrafter-v1"
NORMALCRAFTER_MODEL = NORMALCRAFTER_BUNDLE_ID
NORMALCRAFTER_REVISION = "7e24d68d86ae008fe08ef50b4e51cd2fc2c8cf57"
NORMALCRAFTER_PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "max_resolution": {
            "type": "integer",
            "title": "Maximum resolution",
            "default": 1024,
            "minimum": 256,
            "maximum": 1024,
            "multipleOf": 64,
        },
        "window_size": {
            "type": "integer",
            "title": "Window size",
            "default": 14,
            "minimum": 2,
            "maximum": 32,
        },
        "time_step_size": {
            "type": "integer",
            "title": "Time step",
            "default": 10,
            "minimum": 1,
            "maximum": 32,
        },
        "decode_chunk_size": {
            "type": "integer",
            "title": "Decode chunk",
            "default": 7,
            "minimum": 1,
            "maximum": 32,
        },
    },
    "additionalProperties": False,
}

NORMALCRAFTER_PROVENANCE: dict[str, Any] = {
    "model": {
        "repository": "https://huggingface.co/Yanrui95/NormalCrafter",
        "revision": NORMALCRAFTER_REVISION,
        "license": "Apache-2.0",
    },
    "implementation": {
        "repository": "https://github.com/Binyr/NormalCrafter",
        "revision": "75af9887a2cb14cd1ce3883c5773bc296565777c",
        "license": "MIT",
        "path": "normalcrafter/normal_crafter_ppl.py",
    },
    "wrapper_audit": {
        "repository": "https://github.com/AIWarper/ComfyUI-NormalCrafterWrapper",
        "revision": "cf0d92bc5480e4a2785ebecf878d40bf2eb5f5aa",
        "vendored": False,
        "notes": (
            "Audited only. It downloads models at node runtime, resizes each "
            "dimension independently, and pads short clips without passing the "
            "padded clip to its pipeline. CookSprite uses the original project "
            "as the algorithmic source instead."
        ),
    },
    "notes": (
        "The original 1-step, zero-latent, overlapping-window inference is "
        "kept. CookSprite adds typed artifact streaming, alpha preservation, "
        "aspect-preserving resize/pad, and per-run GPU release."
    ),
}


def _file(source_path: str, name: str, size: int, sha256: str) -> dict[str, Any]:
    """Describe one pinned upstream file and its stable local layout.

    The Diffusers snapshot keeps files at its repository root, while the
    ComfyUI model category needs all members underneath one local model name.
    Keep those two paths distinct: ``source_path`` is the Hugging Face path;
    ``relative_path`` is only the local ComfyUI subdirectory.
    """

    normalized_source = source_path.strip("/")
    relative_path = "/".join(
        part for part in ("normalcrafter-v1", normalized_source) if part
    )
    source_file = "/".join(part for part in (normalized_source, name) if part)
    return {
        "folder": "normalcrafter",
        "relative_path": relative_path,
        "name": name,
        "url": (
            "https://huggingface.co/Yanrui95/NormalCrafter/resolve/"
            f"{NORMALCRAFTER_REVISION}/{source_file}"
        ),
        "size": size,
        "sha256": sha256,
    }


# Every file is explicit.  The custom node passes ``local_files_only=True`` so
# a partial bundle can never turn into an implicit Hugging Face download.
NORMALCRAFTER_BUNDLE: dict[str, Any] = {
    "label": "NormalCrafter · FP16 · Temporal",
    "license": "Apache-2.0",
    "recommended": False,
    "provenance": NORMALCRAFTER_PROVENANCE,
    "files": [
        _file(
            "",
            "model_index.json",
            496,
            "9119b8837600736ae38009c5dc80c76112307cb2d229a2cfb477d54c329ff53d",
        ),
        _file(
            "feature_extractor",
            "preprocessor_config.json",
            518,
            "4db495644e3e5bd8fcac52f70e7fc0b413c911086021acf73ac30e5911166e95",
        ),
        _file(
            "image_encoder",
            "config.json",
            685,
            "65da4496f116d2b297fe864e0f31242fbc57e26a5d95b93310f2034e1e90d0ec",
        ),
        _file(
            "image_encoder",
            "model.fp16.safetensors",
            1_264_217_240,
            "ae616c24393dd1854372b0639e5541666f7521cbe219669255e865cb7f89466a",
        ),
        _file(
            "scheduler",
            "scheduler_config.json",
            533,
            "59aa43afc33395efd40fe94c7369c0477b81698f4b65b63e3ae06f26269876d5",
        ),
        _file(
            "unet",
            "config.json",
            1_028,
            "d35dfa3b19a4c7dcd10a4f72176ebc4e5cd40e463cd16626a13815017e2b6ebc",
        ),
        _file(
            "unet",
            "diffusion_pytorch_model.safetensors",
            3_049_435_868,
            "03095971efc7c439767c3a42d78ded3bc0acb3f51acbfc588c9de76c59bb27cb",
        ),
        _file(
            "vae",
            "config.json",
            553,
            "64c66ac3376c18804b6362024e106660129ad8372f7a368a3a638e133c2b149d",
        ),
        _file(
            "vae",
            "diffusion_pytorch_model.safetensors",
            195_531_910,
            "d3f871a35fabb1522da6cc7a8507f6c53a503c4d535e820620d4341209536943",
        ),
    ],
}


__all__ = [
    "NORMALCRAFTER_BUNDLE",
    "NORMALCRAFTER_BUNDLE_ID",
    "NORMALCRAFTER_MODEL",
    "NORMALCRAFTER_PROVENANCE",
    "NORMALCRAFTER_REVISION",
]
