"""Runtime capability recipes.

A Recipe is the deliberately small semantic adapter between a stable
CookSprite Action and a concrete ComfyUI graph/model combination.  Runtime
discovery may prove that nodes and model files exist; only a Recipe says what
those pieces mean to a CookSprite user.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, replace
from typing import Any

RUNTIME_ASSETS_SCHEMA = "cooksprite.runtime-assets/v1"
RECIPE_SLOT_TYPES = {
    "Image",
    "ImageBatch",
    "SpriteSheet",
    "FrameSeq",
    "Video",
    "Mask",
    "NormalMap",
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
    "CS_CompilePromptPacket",
}

CORE_PIXEL_NODES = {"CS_LoadArtifact", "CS_StoreArtifact", "CS_Pixelize"}
CORE_ALPHA_NODES = {
    "CS_LoadArtifact",
    "CS_StoreArtifact",
    "LoadBackgroundRemovalModel",
    "RemoveBackground",
    "InvertMask",
    "JoinImageWithAlpha",
}
OFFICIAL_ALPHA_MODEL = "birefnet.safetensors"


@dataclass(frozen=True)
class Recipe:
    id: str
    label: str
    family: str
    actions: list[str]
    modes: list[str]
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

    def dump(self) -> dict[str, Any]:
        return asdict(self)

    def bind_workflows(self, runtime_snapshot: str, workflows: dict[str, dict[str, Any]]) -> Recipe:
        return replace(self, runtime_snapshot=runtime_snapshot, workflows=workflows)

    def workflow_for(self, action_id: str, inputs: dict[str, list[str]]) -> dict[str, Any] | None:
        return self.workflows.get(f"{action_id}:{recipe_mode(action_id, inputs)}")

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
    choices = (
        value[0] if isinstance(value, list) and value and isinstance(value[0], list) else value
    )
    return {str(item) for item in choices} if isinstance(choices, list) else set()


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
                "sampler_name": "res_multistep"
                if "res_multistep" in _choices(report, "KSampler", "sampler_name")
                else "euler",
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
        slots = {"text": "positive.text", "seed": "sample.seed", "count": "latent.batch_size"}
    return Recipe(
        id=recipe_id,
        label=label,
        family="comfy.image.unet",
        actions=["image.generate"],
        modes=["i2i" if i2i else "t2i"],
        checkpoint=model,
        workflow=workflow,
        slots=slots,
        output=["decode", 0],
        source="discovered",
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
                "z-image-turbo-bf16",
            ),
            (
                "krea2_turbo_bf16.safetensors",
                "Krea-2 Turbo · BF16",
                "qwen3vl_4b_bf16.safetensors",
                "krea2",
                "qwen_image_vae.safetensors",
                None,
                "krea2-turbo-bf16",
            ),
        )
        for model, label, clip, clip_type, vae, shift, stem in profiles:
            if model not in diffusion_models:
                continue
            for i2i in (False, True):
                recipe = _unet_recipe(
                    report,
                    model,
                    recipe_id=f"{stem}-{'i2i' if i2i else 't2i'}",
                    # The UI selects the model identity only.  T2I/I2I is an
                    # API-side mode decision based on the supplied reference.
                    label=label,
                    clip=clip,
                    clip_type=clip_type,
                    vae=vae,
                    shift=shift,
                    i2i=i2i,
                )
                if recipe:
                    recipes.append(recipe)

    if {"CS_LoadArtifact", "CS_StoreArtifact", "CS_NormalEstimate"}.issubset(nodes):
        recipes.append(
            Recipe(
                id="cooksprite-normal-v1",
                label="CookSprite Normal · ComfyUI",
                family="cooksprite.normal",
                actions=["normal.generate"],
                modes=["image-to-normal"],
            )
        )
    if CORE_PIXEL_NODES.issubset(nodes):
        recipes.append(
            Recipe(
                id="cooksprite-pixel-v1",
                label="CookSprite Pixelize · ComfyUI",
                family="cooksprite.pixel",
                actions=["image.pixelize"],
                modes=["image-to-image"],
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

    if not recipe.workflow:
        return True
    if not recipe.output_name or recipe.output_type not in RECIPE_OUTPUT_TYPES:
        return False
    if set(recipe.slot_types) - set(recipe.slots):
        return False
    if any(value not in RECIPE_SLOT_TYPES for value in recipe.slot_types.values()):
        return False
    if "image.generate" in recipe.actions:
        if recipe.output_type != "Image":
            return False
        if not set(recipe.slots).intersection({"text", "prompt"}):
            return False
        if any(
            mode in {"i2i", "i2v", "i2i-sequence"}
            and not set(recipe.slots).intersection({"image", "source", "reference"})
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


def recipes_from_runtime(runtime: dict[str, Any] | None) -> list[Recipe]:
    if not runtime:
        return []
    import json

    try:
        assets = json.loads(runtime.get("assets") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    manifest = manifest_from_assets(assets)
    return [Recipe.load(item) for item in manifest.get("recipes", []) if isinstance(item, dict)]


def recipe_for(runtime: dict[str, Any], recipe_id: str) -> Recipe | None:
    return next((item for item in recipes_from_runtime(runtime) if item.id == recipe_id), None)


def recipe_mode(action_id: str, inputs: dict[str, list[str]]) -> str:
    if action_id == "image.generate":
        return "i2i" if inputs.get("reference") else "t2i"
    if action_id == "frame.redraw":
        return "i2i"
    if action_id == "animation.generate":
        return "i2v" if inputs.get("character") else "t2v"
    return {
        "normal.generate": "image-to-normal",
        "sheet.slice": "sheet-to-frames",
        "video.sample": "video-to-frames",
        "image.pixelize": "image-to-image",
        "image.cutout": "image-to-image",
    }.get(action_id, "")


def supports(recipe: Recipe, action_id: str, inputs: dict[str, list[str]] | None = None) -> bool:
    if action_id not in recipe.actions:
        return False
    if recipe.workflow and not recipe_contract_is_valid(recipe):
        return False
    if inputs is None:
        return True
    mode = recipe_mode(action_id, inputs)
    compatible = {
        "i2v": {"i2v", "i2i-sequence"},
        "t2v": {"t2v", "t2i-sequence"},
    }.get(mode, {mode})
    if not compatible.intersection(recipe.modes):
        return False
    if recipe.workflow and action_id == "image.generate":
        if mode in {"i2i", "i2v", "i2i-sequence"} and not inputs.get("reference"):
            return False
        if mode in {"i2i", "i2v", "i2i-sequence"} and not set(recipe.slots).intersection(
            {"image", "source", "reference"}
        ):
            return False
    return True
