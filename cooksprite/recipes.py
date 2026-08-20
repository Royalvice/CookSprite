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
OFFICIAL_ALPHA_MODEL = "birefnet.safetensors"
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

    def dump(self) -> dict[str, Any]:
        return asdict(self)

    def bind_workflows(self, runtime_snapshot: str, workflows: dict[str, dict[str, Any]]) -> Recipe:
        return replace(self, runtime_snapshot=runtime_snapshot, workflows=workflows)

    def workflow_for(self, action_id: str, inputs: dict[str, list[str]]) -> dict[str, Any] | None:
        mode = recipe_mode(action_id, inputs)
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
    choices = (
        value[0] if isinstance(value, list) and value and isinstance(value[0], list) else value
    )
    return {str(item) for item in choices} if isinstance(choices, list) else set()


def _model_names(report: dict[str, Any], folder: str) -> set[str]:
    values = _model_map(report).get(folder, [])
    return {str(item) for item in values}


def _bundle_file_available(report: dict[str, Any], file: dict[str, Any]) -> bool:
    folder = str(file.get("folder") or "")
    name = str(file.get("name") or "")
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
            "path": f"models/{file['folder']}/{file['name']}",
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
                modes=["image-to-normal"],
                checkpoint=LOTUS_NORMAL_MODEL,
                source="discovered",
                model_bundle=LOTUS_NORMAL_BUNDLE_ID,
                model_files=list(lotus_bundle["files"]),
                provenance=dict(LOTUS_NORMAL_PROVENANCE),
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

    if recipe.checkpoint in T2I_ONLY_CHECKPOINTS and any(
        mode != "t2i" for mode in recipe.modes
    ):
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
        recipe.checkpoint in T2I_ONLY_CHECKPOINTS
        and any(mode != "t2i" for mode in recipe.modes)
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
    return [
        recipe
        for item in manifest.get("recipes", [])
        if isinstance(item, dict)
        for recipe in [Recipe.load(item)]
        if _runtime_recipe_allowed(recipe)
    ]


def recipe_for(runtime: dict[str, Any], recipe_id: str) -> Recipe | None:
    return next((item for item in recipes_from_runtime(runtime) if item.id == recipe_id), None)


def recipe_for_model(
    recipes: list[Recipe],
    model_id: str,
    action_id: str,
    inputs: dict[str, list[str]],
) -> Recipe | None:
    """Resolve one model identity to the Recipe matching the current input mode."""

    selected_model = model_id
    legacy = next((recipe for recipe in recipes if recipe.id == model_id), None)
    if legacy:
        selected_model = str(legacy.checkpoint or legacy.id)
    return next(
        (
            recipe
            for recipe in recipes
            if str(recipe.checkpoint or recipe.id) == selected_model
            and supports(recipe, action_id, inputs)
        ),
        None,
    )


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
        if mode in {"i2i", "i2v", "i2i-sequence"} and not (
            set(recipe.slots).intersection({"image", "source", "reference"})
            or any(name.startswith("reference_") for name in recipe.slots)
        ):
            return False
        if mode == "i2i" and len(inputs.get("reference") or []) > 4:
            return False
    return True
