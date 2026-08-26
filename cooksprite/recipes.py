"""Runtime capability recipes.

A Recipe is the deliberately small semantic adapter between a stable
CookSprite Action and a concrete ComfyUI graph/model combination.  Runtime
discovery may prove that nodes and model files exist; only a Recipe says what
those pieces mean to a CookSprite user.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from typing import Any

from .workflows.flux2_klein import (
    FLUX2_BUNDLES,
    FLUX2_TEMPLATE_PROVENANCE,
    flux2_klein_graph,
)
from .workflows.lotus_normal import (
    LOTUS_NORMAL_BUNDLE_ID,
    LOTUS_NORMAL_MODEL,
    LOTUS_NORMAL_PROVENANCE,
)
from .workflows.model_bundles import MODEL_BUNDLES
from .workflows.normalcrafter import (
    NORMALCRAFTER_BUNDLE_ID,
    NORMALCRAFTER_MODEL,
    NORMALCRAFTER_PARAMS_SCHEMA,
    NORMALCRAFTER_PROVENANCE,
)
from .workflows.minimax_h3 import minimax_h3_first_last_graph
from .workflows.views import (
    klein_multi_angles_graph,
    pure_prompt_graph,
    quadview_krea_graph,
    tripview_graph,
)

RUNTIME_ASSETS_SCHEMA = "cooksprite.runtime-assets/v1"
RECIPE_SLOT_TYPES = {
    "Image",
    "ImageBatch",
    "SpriteSheet",
    "FrameSeq",
    "Video",
    "Mask",
    "NormalMap",
    "NormalMapSequence",
    "PixelGeometryPlan",
    "Palette",
    "SpritePair",
    "CookSpritePack",
    "Text",
    "Number",
    "Boolean",
}
RECIPE_OUTPUT_TYPES = {
    "Image",
    "ImageBatch",
    "SpriteSheet",
    "FrameSeq",
    "Video",
    "Mask",
    "NormalMap",
    "NormalMapSequence",
    "PixelGeometryPlan",
    "Palette",
    "SpritePair",
    "CookSpritePack",
}
CORE_IMAGE_NODES = {
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "KSampler",
    "EmptyLatentImage",
    "VAEEncode",
    "VAEDecode",
    "RepeatLatentBatch",
    "ImageScale",
    "CS_LoadArtifact",
    "CS_StoreArtifact",
}

CORE_PIXEL_NODES = {"CS_LoadArtifact", "CS_StoreArtifact", "CS_Pixelize"}
CORE_PIXEL_PAIR_NODES = CORE_PIXEL_NODES | {"CS_PixelizePair"}
CORE_PIXEL_SEQUENCE_NODES = CORE_PIXEL_NODES | {"CS_PixelizeSequence"}
CORE_ALPHA_NODES = {
    "CS_LoadArtifact",
    "CS_StoreArtifact",
    "LoadBackgroundRemovalModel",
    "RemoveBackground",
    "InvertMask",
    "JoinImageWithAlpha",
}
FLUX2_T2I_NODES = {
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "CLIPTextEncode",
    "ConditioningZeroOut",
    "PrimitiveInt",
    "EmptyFlux2LatentImage",
    "RandomNoise",
    "KSamplerSelect",
    "Flux2Scheduler",
    "CFGGuider",
    "SamplerCustomAdvanced",
    "VAEDecode",
}
FLUX2_I2I_NODES = FLUX2_T2I_NODES | {
    "ImageScaleToTotalPixels",
    "VAEEncode",
    "ReferenceLatent",
}
FLUX2_I2I_COMPATIBLE_SCALE_NODES = ("ImageScaleToTotalPixels", "ImageScale")
LOTUS_NORMAL_NODES = {
    "CS_LoadArtifact",
    "CS_StoreArtifact",
    "CS_LotusModelLoader",
    "CS_LotusNormalPrepare",
    "CS_LotusNormalFinalize",
    "VAELoader",
    "VAEEncode",
    "LotusConditioning",
    "BasicGuider",
    "DisableNoise",
    "BasicScheduler",
    "SetFirstSigma",
    "KSamplerSelect",
    "SamplerCustomAdvanced",
    "VAEDecode",
}
LOTUS_NORMAL_PLAN_NODES = LOTUS_NORMAL_NODES | {"CS_ProjectNormalToPixelPlan"}
NORMALCRAFTER_SEQUENCE_NODES = {
    "CS_LoadArtifact",
    "CS_StoreArtifact",
    "CS_NormalCrafterSequence",
}
NORMALCRAFTER_BATCH_NODES = NORMALCRAFTER_SEQUENCE_NODES | {"CS_NormalCrafterBatch"}
OFFICIAL_ALPHA_MODEL = "birefnet.safetensors"
VIEW_COMMON_NODES = {
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "ImageScale",
    "VAEEncode",
    "ReferenceLatent",
    "ConditioningZeroOut",
    "CLIPTextEncode",
    "KSamplerSelect",
    "Flux2Scheduler",
    "BasicScheduler",
    "CFGGuider",
    "SamplerCustomAdvanced",
    "EmptyFlux2LatentImage",
    "VAEDecode",
    "ImageBatch",
    "ImageFromBatch",
    "ImagePadForOutpaint",
    "CS_SliceSpriteSheet",
}
H3_NODES = {
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "ImageScale",
    "MiniMaxH3ImageToVideo",
    "RandomNoise",
    "KSamplerSelect",
    "BasicScheduler",
    "BasicGuider",
    "EmptyMiniMaxH3LatentAV",
    "SamplerCustomAdvanced",
    "VAEDecode",
}
T2I_ONLY_CHECKPOINTS = frozenset(
    {
        "z_image_turbo_bf16.safetensors",
        "krea2_turbo_bf16.safetensors",
    }
)


@dataclass(frozen=True)
class Recipe:
    id: str
    label: str
    family: str
    actions: list[str]
    modes: list[str]
    priority: int = 0
    max_frames: int | None = None
    checkpoint: str | None = None
    workflow: dict[str, Any] | None = None
    slots: dict[str, str] = field(default_factory=dict)
    slot_types: dict[str, str] = field(default_factory=dict)
    output: list[Any] | None = None
    output_name: str = "image"
    output_type: str = "Image"
    source: str = "discovered"
    runtime_snapshot: str | None = None
    workflows: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflow_variants: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflow_variant: str | None = None
    model_bundle: str | None = None
    model_files: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    params_schema: dict[str, Any] = field(default_factory=dict)
    expose_dimensions: bool = True

    def dump(self) -> dict[str, Any]:
        return asdict(self)

    def bind_workflows(self, runtime_snapshot: str, workflows: dict[str, dict[str, Any]]) -> Recipe:
        return replace(self, runtime_snapshot=runtime_snapshot, workflows=workflows)

    def workflow_for(
        self,
        action_id: str,
        inputs: dict[str, list[str]],
        mode: str | None = None,
    ) -> dict[str, Any] | None:
        mode = mode or recipe_mode(action_id, inputs)
        if mode == "i2i":
            count = len(inputs.get("reference") or [])
            variant = self.workflows.get(f"{action_id}:{mode}:{count}")
            if variant:
                return variant
        return self.workflows.get(f"{action_id}:{mode}")

    @classmethod
    def load(cls, raw: dict[str, Any]) -> Recipe:
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


def _model_map(report: dict[str, Any]) -> dict[str, list[str]]:
    models = report.get("models") or {}
    if isinstance(models, dict):
        return {
            str(folder): sorted({str(item) for item in values})
            for folder, values in models.items()
            if isinstance(values, list)
        }
    return {}


def _choices(report: dict[str, Any], node_id: str, name: str) -> set[str]:
    spec = (report.get("object_info") or {}).get(node_id) or {}
    value = ((spec.get("input") or {}).get("required") or {}).get(name) or []
    if isinstance(value, list) and value and isinstance(value[0], list):
        choices = value[0]
    elif isinstance(value, list) and len(value) > 1 and isinstance(value[1], dict):
        choices = value[1].get("options") or value
    else:
        choices = value
    return {str(item) for item in choices} if isinstance(choices, list) else set()


def _model_names(report: dict[str, Any], folder: str) -> set[str]:
    values = _model_map(report).get(folder, [])
    return {str(item) for item in values}


def _bundle_file_name(file: dict[str, Any]) -> str:
    """Return a safe, runtime-discovery-relative member name."""

    relative = str(file.get("relative_path") or "").strip("/")
    name = str(file.get("name") or "")
    return f"{relative}/{name}" if relative else name


def _bundle_file_available(report: dict[str, Any], file: dict[str, Any]) -> bool:
    folder = str(file.get("folder") or "")
    name = _bundle_file_name(file)
    if not folder or not name:
        return False
    if name in _model_names(report, folder):
        return True
    loader = {
        "diffusion_models": ("UNETLoader", "unet_name"),
        "text_encoders": ("CLIPLoader", "clip_name"),
        "vae": ("VAELoader", "vae_name"),
    }.get(folder)
    return bool(loader and name in _choices(report, *loader))


def model_bundle_status(report: dict[str, Any], bundle_id: str) -> dict[str, Any]:
    """Project runtime model discovery into one stable bundle view."""

    bundle = MODEL_BUNDLES.get(bundle_id)
    if not bundle:
        raise KeyError(f"unknown model bundle: {bundle_id}")
    files = [
        {
            **file,
            "path": f"models/{file['folder']}/{_bundle_file_name(file)}",
            "present": _bundle_file_available(report, file),
        }
        for file in bundle["files"]
    ]
    return {
        "id": bundle_id,
        "label": bundle["label"],
        "license": bundle["license"],
        "recommended": bool(bundle.get("recommended")),
        "ready": all(file["present"] for file in files),
        "files": files,
    }


def model_bundles(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [model_bundle_status(report, bundle_id) for bundle_id in MODEL_BUNDLES]


def recipe_variants(recipe: Recipe) -> list[Recipe]:
    """Return one sealed-tool view per raw graph variant."""

    variants = [recipe]
    for key, workflow in sorted(recipe.workflow_variants.items()):
        slots = dict(recipe.slots)
        slot_types = dict(recipe.slot_types)
        for node_id in workflow:
            if not str(node_id).startswith("scale_ref_"):
                continue
            index = str(node_id).removeprefix("scale_ref_")
            slots[f"reference_{index}"] = f"{node_id}.image"
            slot_types[f"reference_{index}"] = "Image"
        variants.append(
            replace(
                recipe,
                workflow=workflow,
                slots=slots,
                slot_types=slot_types,
                workflow_variant=f"i2i-{key}",
            )
        )
    return variants


def _unet_recipe(
    report: dict[str, Any],
    model: str,
    *,
    recipe_id: str,
    label: str,
    clip: str,
    clip_type: str,
    vae: str,
    shift: float | None,
    sampler_name: str,
    provenance: dict[str, Any],
    i2i: bool = False,
) -> Recipe | None:
    """Create a sealed adapter only when every selected loader input is real."""

    nodes = set((report.get("object_info") or {}).keys())
    required = {
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "CLIPTextEncode",
        "ConditioningZeroOut",
        "KSampler",
        "VAEDecode",
    }
    latent_node = "EmptySD3LatentImage"
    required.add("RepeatLatentBatch" if i2i else latent_node)
    if shift is not None:
        required.add("ModelSamplingAuraFlow")
    if i2i:
        required.update({"ImageScale", "VAEEncode"})
    if not required.issubset(nodes):
        return None
    if model not in _choices(report, "UNETLoader", "unet_name"):
        return None
    if clip not in _choices(report, "CLIPLoader", "clip_name"):
        return None
    if clip_type not in _choices(report, "CLIPLoader", "type"):
        return None
    if vae not in _choices(report, "VAELoader", "vae_name"):
        return None
    sampler_choices = _choices(report, "KSampler", "sampler_name")
    if sampler_choices and sampler_name not in sampler_choices:
        return None
    workflow: dict[str, Any] = {
        "model": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": model, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip, "type": clip_type},
        },
        "positive": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["clip", 0], "text": ""},
        },
        "negative": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["positive", 0]},
        },
        "sample": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["model", 0],
                "seed": 0,
                "steps": 8,
                "cfg": 1.0,
                "sampler_name": sampler_name,
                "scheduler": "simple",
                "positive": ["positive", 0],
                "negative": ["negative", 0],
                "latent_image": ["latent", 0],
                "denoise": 1.0,
            },
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]},
        },
    }
    if shift is not None:
        workflow["shift"] = {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["model", 0], "shift": shift},
        }
        workflow["sample"]["inputs"]["model"] = ["shift", 0]
    if i2i:
        workflow["source"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": "",
                "upscale_method": "nearest-exact",
                "width": 1024,
                "height": 1024,
                "crop": "disabled",
            },
        }
        workflow["encode"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["source", 0], "vae": ["vae", 0]},
        }
        workflow["latent"] = {
            "class_type": "RepeatLatentBatch",
            "inputs": {"samples": ["encode", 0], "amount": 1},
        }
        workflow["sample"]["inputs"]["latent_image"] = ["latent", 0]
        workflow["sample"]["inputs"]["denoise"] = 0.65
        slots = {
            "text": "positive.text",
            "seed": "sample.seed",
            "count": "latent.amount",
            "image": "source.image",
            "strength": "sample.denoise",
        }
    else:
        workflow["latent"] = {
            "class_type": latent_node,
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        }
        slots = {
            "text": "positive.text",
            "seed": "sample.seed",
            "count": "latent.batch_size",
        }
    slot_types = {
        "text": "Text",
        "seed": "Number",
        "count": "Number",
    }
    if i2i:
        slot_types.update({"image": "Image", "strength": "Number"})
    return Recipe(
        id=recipe_id,
        label=label,
        family="comfy.image.unet",
        actions=["image.generate"],
        modes=["i2i" if i2i else "t2i"],
        checkpoint=model,
        workflow=workflow,
        slots=slots,
        slot_types=slot_types,
        output=["decode", 0],
        source="discovered",
        provenance=provenance,
    )


def _flux2_recipes(report: dict[str, Any]) -> list[Recipe]:
    nodes = set((report.get("object_info") or {}).keys())
    result: list[Recipe] = []
    for bundle_id, bundle in FLUX2_BUNDLES.items():
        if not all(_bundle_file_available(report, file) for file in bundle["files"]):
            continue
        if not FLUX2_T2I_NODES.issubset(nodes):
            continue
        t2i_graph, t2i_slots, t2i_slot_types = flux2_klein_graph(bundle_id, "t2i")
        result.append(
            Recipe(
                id=f"{bundle_id}-t2i",
                label=f"{bundle['label']} · T2I",
                family="comfy.flux2-klein",
                actions=["image.generate"],
                modes=["t2i"],
                priority=100 if "9b" in bundle_id else 80,
                checkpoint=bundle["files"][0]["name"],
                workflow=t2i_graph,
                slots=t2i_slots,
                slot_types=t2i_slot_types,
                output=["decode", 0],
                source="discovered",
                model_bundle=bundle_id,
                model_files=list(bundle["files"]),
                provenance=dict(FLUX2_TEMPLATE_PROVENANCE),
            )
        )
        scale_node = next(
            (candidate for candidate in FLUX2_I2I_COMPATIBLE_SCALE_NODES if candidate in nodes),
            None,
        )
        if not scale_node or not {"VAEEncode", "ReferenceLatent"}.issubset(nodes):
            continue
        variants: dict[str, dict[str, Any]] = {}
        first_graph: dict[str, Any] | None = None
        first_slots: dict[str, str] | None = None
        first_slot_types: dict[str, str] | None = None
        for count in range(1, 5):
            graph, slots, slot_types = flux2_klein_graph(
                bundle_id, "i2i", reference_count=count, scale_node=scale_node
            )
            if count == 1:
                first_graph, first_slots, first_slot_types = graph, slots, slot_types
            else:
                variants[str(count)] = graph
        result.append(
            Recipe(
                id=f"{bundle_id}-i2i",
                label=f"{bundle['label']} · I2I",
                family="comfy.flux2-klein",
                actions=["image.generate"],
                modes=["i2i"],
                priority=100 if "9b" in bundle_id else 80,
                checkpoint=bundle["files"][0]["name"],
                workflow=first_graph,
                slots=first_slots or {},
                slot_types=first_slot_types or {},
                output=["decode", 0],
                source="discovered",
                workflow_variants=variants,
                model_bundle=bundle_id,
                model_files=list(bundle["files"]),
                provenance=dict(FLUX2_TEMPLATE_PROVENANCE),
            )
        )
    return result


def _view_recipe(
    report: dict[str, Any],
    *,
    recipe_id: str,
    label: str,
    workflow: dict[str, Any],
    required_nodes: set[str],
    required_choices: tuple[tuple[str, str, str], ...],
    provenance: dict[str, Any],
    checkpoint: str,
    output_type: str = "ImageBatch",
) -> Recipe | None:
    nodes = set((report.get("object_info") or {}).keys())
    if not required_nodes.issubset(nodes):
        return None
    for node_id, name, expected in required_choices:
        if expected not in _choices(report, node_id, name):
            return None
    return Recipe(
        id=recipe_id,
        label=label,
        family="comfy.views",
        actions=["image.views"],
        modes=["i2i"],
        checkpoint=checkpoint,
        workflow=workflow,
        slots={"source": "source_scale.image"},
        slot_types={"source": "Image"},
        output=["batch_123", 0],
        output_name="images",
        output_type=output_type,
        source="discovered",
        provenance=provenance,
        expose_dimensions=False,
    )


def _view_recipes(report: dict[str, Any]) -> list[Recipe]:
    """Discover all four declared single-image view adapters."""

    common_choices = (
        ("UNETLoader", "unet_name", "flux-2-klein-9b-fp8.safetensors"),
        ("CLIPLoader", "clip_name", "qwen_3_8b_fp8mixed.safetensors"),
        ("CLIPLoader", "type", "flux2"),
        ("VAELoader", "vae_name", "flux2-vae.safetensors"),
    )
    result: list[Recipe] = []
    multi = _view_recipe(
        report,
        recipe_id="views-multi-angles-klein9b",
        label="Klein 9B · Multi-Angles LoRA",
        workflow=klein_multi_angles_graph(),
        required_nodes=VIEW_COMMON_NODES | {"LoraLoaderModelOnly"},
        required_choices=common_choices
        + (("LoraLoaderModelOnly", "lora_name", "multiple-angles-flux-klein-9b.safetensors"),),
        provenance={"method": "multi_angles_klein9b", "lora": "multiple-angles-flux-klein-9b.safetensors"},
        checkpoint="flux-2-klein-9b-fp8.safetensors",
    )
    if multi:
        result.append(multi)

    trip = _view_recipe(
        report,
        recipe_id="views-tripview-klein9b",
        label="Klein 9B · TripView LoRA",
        workflow=tripview_graph(),
        required_nodes=VIEW_COMMON_NODES | {"LoraLoaderModelOnly", "ImageCrop"},
        required_choices=common_choices
        + (("LoraLoaderModelOnly", "lora_name", "charactersheet_tripleview_klein9b_v1.safetensors"),),
        provenance={"method": "tripview_klein9b", "lora": "charactersheet_tripleview_klein9b_v1.safetensors"},
        checkpoint="flux-2-klein-9b-fp8.safetensors",
    )
    if trip:
        result.append(trip)

    krea = _view_recipe(
        report,
        recipe_id="views-quadview-krea2",
        label="Krea-2 · QuadView LoRA",
        workflow=quadview_krea_graph(),
        required_nodes={
            "UNETLoader",
            "CLIPLoader",
            "VAELoader",
            "FluxKontextImageScale",
            "TextEncodeKrea2OstrisEdit",
            "FluxKontextMultiReferenceLatentMethod",
            "Krea2OstrisEditModelPatch",
            "LoraLoaderModelOnly",
            "EmptyLatentImage",
            "KSampler",
            "VAEDecode",
            "CS_SliceSpriteSheet",
            "ImageFromBatch",
            "ImageScale",
            "ImageCrop",
            "ImagePadForOutpaint",
            "ImageBatch",
        },
        required_choices=(
            ("UNETLoader", "unet_name", "krea2_turbo_bf16.safetensors"),
            ("CLIPLoader", "clip_name", "qwen3vl_4b_bf16.safetensors"),
            ("CLIPLoader", "type", "krea2"),
            ("VAELoader", "vae_name", "qwen_image_vae.safetensors"),
            ("LoraLoaderModelOnly", "lora_name", "charactersheet_quadview_krea2_v1.safetensors"),
        ),
        provenance={"method": "quadview_krea2", "lora": "charactersheet_quadview_krea2_v1.safetensors"},
        checkpoint="krea2_turbo_bf16.safetensors",
    )
    if krea:
        result.append(krea)

    pure = _view_recipe(
        report,
        recipe_id="views-pure-prompt-klein9b",
        label="Klein 9B · Reference I2I Prompt",
        workflow=pure_prompt_graph(),
        required_nodes=VIEW_COMMON_NODES - {"CS_SliceSpriteSheet", "ImageFromBatch", "ImagePadForOutpaint"},
        required_choices=common_choices,
        provenance={"method": "pure_prompt_klein9b", "lora": None},
        checkpoint="flux-2-klein-9b-fp8.safetensors",
    )
    if pure:
        result.append(pure)
    return result


def _h3_recipe(report: dict[str, Any]) -> Recipe | None:
    nodes = set((report.get("object_info") or {}).keys())
    if not H3_NODES.issubset(nodes):
        return None
    choices = (
        ("UNETLoader", "unet_name", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
        ("CLIPLoader", "clip_name", "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"),
        ("CLIPLoader", "type", "minimax"),
        ("VAELoader", "vae_name", "minimax_h3_video_vae_fp16.safetensors"),
        ("KSamplerSelect", "sampler_name", "res_multistep"),
    )
    if any(expected not in _choices(report, node_id, name) for node_id, name, expected in choices):
        return None
    return Recipe(
        id="animation-minimax-h3-fl2va-first-last",
        label="MiniMax H3 · FL2VA first/last frame",
        family="comfy.minimax-h3",
        actions=["animation.generate"],
        modes=["i2v"],
        priority=200,
        checkpoint="minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        workflow=minimax_h3_first_last_graph(),
        slots={"source": "source_scale.image", "prompt": "h3.prompt", "seed": "noise.noise_seed"},
        slot_types={"source": "Image", "prompt": "Text", "seed": "Number"},
        output=["decode", 0],
        output_name="images",
        output_type="ImageBatch",
        source="discovered",
        provenance={
            "method": "minimax_h3_fl2va_first_last",
            "fps": 24,
            "length": 124,
            "temporal_source": "generated_video",
            "loop": "linear",
        },
        expose_dimensions=False,
    )


def discover_recipes(report: dict[str, Any]) -> list[Recipe]:
    """Build only recipes whose complete structural requirements are present."""

    nodes = set((report.get("object_info") or {}).keys())
    models = _model_map(report)
    checkpoints = models.get("checkpoints", [])
    if not checkpoints:
        loader = (report.get("object_info") or {}).get("CheckpointLoaderSimple", {})
        choice = ((loader.get("input") or {}).get("required") or {}).get("ckpt_name") or []
        if isinstance(choice, list) and choice and isinstance(choice[0], list):
            checkpoints = sorted({str(item) for item in choice[0]})

    recipes: list[Recipe] = []
    if CORE_IMAGE_NODES.issubset(nodes):
        for checkpoint in checkpoints:
            checkpoint_key = hashlib.sha256(checkpoint.encode()).hexdigest()[:12]
            recipes.append(
                Recipe(
                    id=f"core-image-{checkpoint_key}",
                    label=checkpoint,
                    family="comfy.core-checkpoint",
                    actions=["image.generate", "frame.redraw", "animation.generate"],
                    modes=["t2i", "i2i", "i2i-sequence"],
                    checkpoint=checkpoint,
                )
            )

    # These are explicit model/workflow adapters, not filename-only guesses:
    # each candidate must be present in ComfyUI's loader choices and every
    # graph node/port used by the adapter must be discovered in object_info.
    diffusion_models = set(models.get("diffusion_models", []))
    if {"UNETLoader", "CLIPLoader", "VAELoader"}.issubset(nodes):
        profiles = (
            (
                "z_image_turbo_bf16.safetensors",
                "Z-Image-Turbo · BF16",
                "qwen_3_4b.safetensors",
                "lumina2",
                "ae.safetensors",
                3.0,
                "res_multistep",
                "z-image-turbo-bf16",
                {
                    "repository": "https://github.com/Comfy-Org/workflow_templates",
                    "template": "templates/image_z_image_turbo.json",
                    "notes": "Official eight-step, CFG 1, zero-negative, res_multistep graph with AuraFlow shift 3.",
                },
            ),
            (
                "krea2_turbo_bf16.safetensors",
                "Krea-2 Turbo · BF16",
                "qwen3vl_4b_bf16.safetensors",
                "krea2",
                "qwen_image_vae.safetensors",
                None,
                "euler",
                "krea2-turbo-bf16",
                {
                    "repository": "https://github.com/Comfy-Org/workflow_templates",
                    "template": "templates/image_krea2_turbo_t2i.json",
                    "notes": "Official eight-step, CFG 1, zero-negative, Euler graph.",
                },
            ),
        )
        for model, label, clip, clip_type, vae, shift, sampler_name, stem, provenance in profiles:
            if model not in diffusion_models:
                continue
            # These official model adapters are text-to-image only.  Do not
            # synthesize an image-to-image graph from their T2I components.
            recipe = _unet_recipe(
                report,
                model,
                recipe_id=f"{stem}-t2i",
                label=label,
                clip=clip,
                clip_type=clip_type,
                vae=vae,
                shift=shift,
                sampler_name=sampler_name,
                provenance=provenance,
                i2i=False,
            )
            if recipe:
                recipes.append(recipe)

    recipes.extend(_flux2_recipes(report))
    recipes.extend(_view_recipes(report))
    if (h3 := _h3_recipe(report)) is not None:
        recipes.append(h3)

    lotus_bundle = MODEL_BUNDLES[LOTUS_NORMAL_BUNDLE_ID]
    if LOTUS_NORMAL_NODES.issubset(nodes) and all(
        _bundle_file_available(report, file) for file in lotus_bundle["files"]
    ):
        recipes.append(
            Recipe(
                id=LOTUS_NORMAL_BUNDLE_ID,
                label="Lotus Normal D v1.1 · BF16",
                family="cooksprite.normal",
                actions=["normal.generate"],
                modes=(
                    ["image-to-normal", "frames-to-normal", "image-to-pixel-normal"]
                    if LOTUS_NORMAL_PLAN_NODES.issubset(nodes)
                    else ["image-to-normal", "frames-to-normal"]
                ),
                priority=50,
                max_frames=32,
                checkpoint=LOTUS_NORMAL_MODEL,
                source="discovered",
                model_bundle=LOTUS_NORMAL_BUNDLE_ID,
                model_files=list(lotus_bundle["files"]),
                provenance=dict(LOTUS_NORMAL_PROVENANCE),
            )
        )
        if CORE_PIXEL_PAIR_NODES.issubset(nodes):
            recipes.append(
                Recipe(
                    id="cooksprite-sprite-pixel-v2",
                    label="Lotus Normal + CookSprite Pixelize",
                    family="cooksprite.sprite",
                    actions=["sprite.pixelize"],
                    modes=["image-to-sprite-pair", "frames-to-sprite-pair"],
                    checkpoint=LOTUS_NORMAL_MODEL,
                    source="discovered",
                    model_bundle=LOTUS_NORMAL_BUNDLE_ID,
                    model_files=list(lotus_bundle["files"]),
                    provenance={
                        "normal": dict(LOTUS_NORMAL_PROVENANCE),
                        "pixel": {"package": "cooksprite.pixel", "version": "2.1.0"},
                    },
                )
            )
    normalcrafter_bundle = MODEL_BUNDLES[NORMALCRAFTER_BUNDLE_ID]
    if NORMALCRAFTER_SEQUENCE_NODES.issubset(nodes) and all(
        _bundle_file_available(report, file) for file in normalcrafter_bundle["files"]
    ):
        recipes.append(
            Recipe(
                id=NORMALCRAFTER_BUNDLE_ID,
                label="NormalCrafter · FP16 · Temporal",
                family="cooksprite.normal-temporal",
                actions=["normal.generate"],
                modes=["frames-to-normal"],
                priority=100,
                checkpoint=NORMALCRAFTER_MODEL,
                source="discovered",
                model_bundle=NORMALCRAFTER_BUNDLE_ID,
                model_files=list(normalcrafter_bundle["files"]),
                provenance=dict(NORMALCRAFTER_PROVENANCE),
                params_schema=dict(NORMALCRAFTER_PARAMS_SCHEMA),
            )
        )
        if NORMALCRAFTER_BATCH_NODES.issubset(nodes) and CORE_PIXEL_PAIR_NODES.issubset(nodes):
            recipes.append(
                Recipe(
                    id="cooksprite-sprite-pixel-normalcrafter-v1",
                    label="NormalCrafter + CookSprite Pixelize",
                    family="cooksprite.sprite-temporal",
                    actions=["sprite.pixelize"],
                    modes=["frames-to-sprite-pair"],
                    checkpoint=NORMALCRAFTER_MODEL,
                    source="discovered",
                    model_bundle=NORMALCRAFTER_BUNDLE_ID,
                    model_files=list(normalcrafter_bundle["files"]),
                    provenance={
                        "normal": dict(NORMALCRAFTER_PROVENANCE),
                        "pixel": {"package": "cooksprite.pixel", "version": "2.1.0"},
                    },
                    params_schema=dict(NORMALCRAFTER_PARAMS_SCHEMA),
                )
            )
    if CORE_PIXEL_NODES.issubset(nodes):
        recipes.append(
            Recipe(
                id="cooksprite-pixel-v1",
                label="CookSprite Pixelize · ComfyUI",
                family="cooksprite.pixel",
                actions=["image.pixelize"],
                modes=(
                    ["image-to-image", "frames-to-frames"]
                    if CORE_PIXEL_SEQUENCE_NODES.issubset(nodes)
                    else ["image-to-image"]
                ),
            )
        )
    if CORE_ALPHA_NODES.issubset(nodes) and OFFICIAL_ALPHA_MODEL in set(
        models.get("background_removal", [])
    ):
        recipes.append(
            Recipe(
                id="cooksprite-alpha-v1",
                label="CookSprite Cutout · ComfyUI",
                family="cooksprite.alpha",
                actions=["image.cutout"],
                modes=["image-to-image"],
            )
        )
    if {"CS_LoadArtifact", "CS_StoreArtifact", "CS_SliceSpriteSheet"}.issubset(nodes):
        recipes.append(
            Recipe(
                id="cooksprite-sheet-v1",
                label="CookSprite Sheet Slicer · ComfyUI",
                family="cooksprite.sheet",
                actions=["sheet.slice"],
                modes=["sheet-to-frames"],
            )
        )
    if {"CS_LoadVideoArtifact", "CS_StoreArtifact"}.issubset(nodes):
        recipes.append(
            Recipe(
                id="cooksprite-video-sample-v1",
                label="CookSprite Video Sampler · ComfyUI",
                family="cooksprite.video",
                actions=["video.sample"],
                modes=["video-to-frames"],
            )
        )
    return recipes


def imported_recipe_is_compatible(recipe: Recipe, report: dict[str, Any]) -> bool:
    """Revalidate an imported API graph against every fresh runtime snapshot."""

    if recipe.source != "imported" or not isinstance(recipe.workflow, dict):
        return False
    if not recipe_contract_is_valid(recipe):
        return False
    nodes = set((report.get("object_info") or {}).keys())
    required_bridge_nodes = {"CS_StoreArtifact"}
    if set(recipe.slots).intersection({"image", "source", "reference", "video"}):
        required_bridge_nodes.add("CS_LoadArtifact")
    if "image.generate" in recipe.actions:
        required_bridge_nodes.add("CS_Pixelize")
    if not required_bridge_nodes.issubset(nodes):
        return False
    workflow_nodes = {
        str(node_id): node for node_id, node in recipe.workflow.items() if isinstance(node, dict)
    }
    if len(workflow_nodes) != len(recipe.workflow):
        return False
    if any(str(node.get("class_type")) not in nodes for node in workflow_nodes.values()):
        return False
    if (
        not recipe.output
        or len(recipe.output) != 2
        or str(recipe.output[0]) not in workflow_nodes
        or not isinstance(recipe.output[1], int)
    ):
        return False
    if any(
        "." not in address or address.split(".", 1)[0] not in workflow_nodes
        for address in recipe.slots.values()
    ):
        return False
    if recipe.checkpoint:
        checkpoints = _model_map(report).get("checkpoints", [])
        if recipe.checkpoint not in checkpoints:
            return False
    return True


def recipe_contract_is_valid(recipe: Recipe) -> bool:
    """Validate the small semantic contract used by the generic assembler."""

    if recipe.family in {"comfy.core-checkpoint", "comfy.image.unet"} and recipe.checkpoint in T2I_ONLY_CHECKPOINTS and any(mode != "t2i" for mode in recipe.modes):
        return False
    if not recipe.workflow:
        return True
    if not recipe.output_name or recipe.output_type not in RECIPE_OUTPUT_TYPES:
        return False
    if set(recipe.slot_types) - set(recipe.slots):
        return False
    if any(value not in RECIPE_SLOT_TYPES for value in recipe.slot_types.values()):
        return False
    graphs = [recipe.workflow, *recipe.workflow_variants.values()]
    if any(graph is not None and not isinstance(graph, dict) for graph in graphs):
        return False
    if "image.generate" in recipe.actions:
        if recipe.output_type != "Image":
            return False
        if not set(recipe.slots).intersection({"text", "prompt"}):
            return False
        if any(
            mode in {"i2i", "i2v", "i2i-sequence"}
            and not (
                set(recipe.slots).intersection({"image", "source", "reference"})
                or any(name.startswith("reference_") for name in recipe.slots)
            )
            for mode in recipe.modes
        ):
            return False
    return True


def runtime_manifest(
    report: dict[str, Any], recipes: list[Recipe], *, callback_url: str | None = None
) -> dict[str, Any]:
    return {
        "schema": RUNTIME_ASSETS_SCHEMA,
        "models": _model_map(report),
        "model_bundles": model_bundles(report),
        "model_sources": {},
        "workflow_templates": report.get("workflow_templates") or {},
        "features": report.get("features") or {},
        "system": (report.get("system_stats") or {}).get("system") or {},
        "defaults": {},
        "recipes": [recipe.dump() for recipe in recipes],
        "callback_url": callback_url,
    }


def manifest_from_assets(assets: Any) -> dict[str, Any]:
    if not isinstance(assets, list):
        return {}
    return next(
        (
            item
            for item in assets
            if isinstance(item, dict) and item.get("schema") == RUNTIME_ASSETS_SCHEMA
        ),
        {},
    )


def _runtime_recipe_allowed(recipe: Recipe) -> bool:
    """Prevent stale manifests from reviving unsupported model modes."""

    return not (
        recipe.family in {"comfy.core-checkpoint", "comfy.image.unet"}
        and recipe.checkpoint in T2I_ONLY_CHECKPOINTS
        and any(mode != "t2i" for mode in recipe.modes)
    )


def recipes_from_runtime(runtime: dict[str, Any] | None) -> list[Recipe]:
    if not runtime:
        return []
    try:
        assets = json.loads(runtime.get("assets") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    manifest = manifest_from_assets(assets)
    return [
        recipe
        for item in manifest.get("recipes", [])
        if isinstance(item, dict)
        for recipe in [Recipe.load(item)]
        if _runtime_recipe_allowed(recipe)
    ]


def recipe_for_model(
    recipes: list[Recipe],
    model_id: str,
    action_id: str,
    inputs: dict[str, list[str]],
    mode: str | None = None,
    selector_value: str | None = None,
) -> Recipe | None:
    """Resolve one model identity to the Recipe matching the current input mode."""

    selected_model = model_id
    legacy = next((recipe for recipe in recipes if recipe.id == model_id), None)
    if legacy:
        selected_model = str(legacy.checkpoint or legacy.id)
    candidates = [
        recipe
        for recipe in recipes
        if str(recipe.checkpoint or recipe.id) == selected_model
        and supports(recipe, action_id, inputs, mode=mode)
    ]
    if selector_value is not None:
        candidates = [
            recipe for recipe in candidates if recipe.provenance.get("method") == selector_value
        ]
    return max(candidates, key=lambda item: item.priority, default=None)


def recipe_mode(action_id: str, inputs: dict[str, list[str]]) -> str:
    return _action_registry().mode(action_id, inputs)


@lru_cache(maxsize=1)
def _action_registry():
    from .registry import CookSpriteRegistry

    return CookSpriteRegistry()


def supports(
    recipe: Recipe,
    action_id: str,
    inputs: dict[str, list[str]] | None = None,
    *,
    mode: str | None = None,
) -> bool:
    if action_id not in recipe.actions:
        return False
    if recipe.workflow and not recipe_contract_is_valid(recipe):
        return False
    if inputs is None:
        return True
    mode = mode or recipe_mode(action_id, inputs)
    compatible = {
        "i2v": {"i2v", "i2i-sequence"},
        "t2v": {"t2v", "t2i-sequence"},
    }.get(mode, {mode})
    if not compatible.intersection(recipe.modes):
        return False
    if recipe.workflow and action_id == "image.generate":
        if mode in {"i2i", "i2v", "i2i-sequence"} and not inputs.get("reference"):
            return False
        if mode in {"i2i", "i2v", "i2i-sequence"} and not (
            set(recipe.slots).intersection({"image", "source", "reference"})
            or any(name.startswith("reference_") for name in recipe.slots)
        ):
            return False
        if mode == "i2i" and len(inputs.get("reference") or []) > 4:
            return False
    return True
