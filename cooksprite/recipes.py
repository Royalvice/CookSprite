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
    "CS_IsolateOnGreen",
    "CS_Pixelize",
    "CS_CompilePromptPacket",
}


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
    output: list[Any] | None = None
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
    nodes = set((report.get("object_info") or {}).keys())
    required_bridge_nodes = {"CS_StoreArtifact"}
    if "image" in recipe.slots:
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
    if not recipe.output or len(recipe.output) != 2 or str(recipe.output[0]) not in workflow_nodes:
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


def runtime_manifest(
    report: dict[str, Any], recipes: list[Recipe], *, callback_url: str | None = None
) -> dict[str, Any]:
    return {
        "schema": RUNTIME_ASSETS_SCHEMA,
        "models": _model_map(report),
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
    }.get(action_id, "")


def supports(recipe: Recipe, action_id: str, inputs: dict[str, list[str]] | None = None) -> bool:
    if action_id not in recipe.actions or inputs is None:
        return action_id in recipe.actions
    mode = recipe_mode(action_id, inputs)
    compatible = {
        "i2v": {"i2v", "i2i-sequence"},
        "t2v": {"t2v", "t2i-sequence"},
    }.get(mode, {mode})
    return bool(compatible.intersection(recipe.modes))
