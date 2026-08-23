"""Bind stable Actions to typed Task/Workflow definitions.

This module may choose and compose immutable definitions, but it never emits a
Comfy graph. Only :mod:`cooksprite.compiler` performs that lowering.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from .domain import (
    TaskDefinition,
    TaskRevision,
    ToolNode,
    ValueRef,
    WorkflowCall,
    WorkflowDefinition,
    WorkflowRevision,
)
from .prompting import PromptCompiler, compile_action_values
from .recipe_assembler import (
    assemble_recipe_workflow,
    input_ref,
    literal,
    output_ref,
)
from .recipes import OFFICIAL_ALPHA_MODEL, Recipe, recipe_mode, recipe_variants
from .registry import CookSpriteRegistry
from .store import Store
from .tool_packages import tool_packages

IMAGE_RESOLUTIONS = (64, 128, 256, 512, 1024)
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ACTION_REGISTRY = CookSpriteRegistry()


def _image_resolution(value: Any) -> int:
    try:
        resolution = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"resolution must be one of {IMAGE_RESOLUTIONS}") from exc
    if resolution not in IMAGE_RESOLUTIONS:
        raise ValueError(f"resolution must be one of {IMAGE_RESOLUTIONS}")
    return resolution


def _outline_color(value: Any) -> str:
    color = str(value or "#000000").strip()
    if _HEX_COLOR.fullmatch(color) is None:
        raise ValueError("outline_color must be a six-digit RGB hex color")
    return color.upper()


def _core_image_workflow(
    runtime_id: str,
    recipe: Recipe,
    action_id: str,
    mode: str,
    source_slot: str | None = None,
) -> WorkflowDefinition:
    if not recipe.checkpoint:
        raise ValueError("core image recipe requires a checkpoint")
    inputs: dict[str, str] = {
        "prompt": "Text",
        "count": "Number",
        "seed": "Number",
        "strength": "Number",
        "pixel_enabled": "Boolean",
        "resolution": "Number",
    }
    if mode in {"i2i", "i2v"}:
        if source_slot is None:
            raise ValueError(f"{action_id}:{mode} requires an explicit image input slot")
        inputs[source_slot] = "Image"

    nodes = [
        ToolNode(
            id="model",
            tool="comfy.CheckpointLoaderSimple",
            inputs={"ckpt_name": literal(recipe.checkpoint)},
        ),
        ToolNode(
            id="positive",
            tool="comfy.CLIPTextEncode",
            inputs={
                "text": input_ref("prompt"),
                "clip": output_ref("model", "output_1"),
            },
        ),
        ToolNode(
            id="negative",
            tool="comfy.CLIPTextEncode",
            inputs={
                # KSampler still requires a conditioning input. Encode an
                # empty string; CookSprite has no negative-prompt product API.
                "text": literal(""),
                "clip": output_ref("model", "output_1"),
            },
        ),
    ]
    if source_slot:
        nodes.extend(
            [
                ToolNode(
                    id="scale",
                    tool="comfy.ImageScale",
                    inputs={
                        "image": input_ref(source_slot),
                        "upscale_method": literal("nearest-exact"),
                        "width": input_ref("resolution"),
                        "height": input_ref("resolution"),
                        "crop": literal("disabled"),
                    },
                ),
                ToolNode(
                    id="encode",
                    tool="comfy.VAEEncode",
                    inputs={
                        "pixels": output_ref("scale", "output_0"),
                        "vae": output_ref("model", "output_2"),
                    },
                ),
                ToolNode(
                    id="latent",
                    tool="comfy.RepeatLatentBatch",
                    inputs={
                        "samples": output_ref("encode", "output_0"),
                        "amount": input_ref("count"),
                    },
                ),
            ]
        )
        latent = output_ref("latent", "output_0")
        denoise = input_ref("strength")
    else:
        nodes.append(
            ToolNode(
                id="latent",
                tool="comfy.EmptyLatentImage",
                inputs={
                    "width": input_ref("resolution"),
                    "height": input_ref("resolution"),
                    "batch_size": input_ref("count"),
                },
            )
        )
        latent = output_ref("latent", "output_0")
        denoise = literal(1.0)

    nodes.extend(
        [
            ToolNode(
                id="sample",
                tool="comfy.KSampler",
                inputs={
                    "model": output_ref("model", "output_0"),
                    "seed": input_ref("seed"),
                    "steps": literal(20),
                    "cfg": literal(7.0),
                    "sampler_name": literal("euler"),
                    "scheduler": literal("normal"),
                    "positive": output_ref("positive", "output_0"),
                    "negative": output_ref("negative", "output_0"),
                    "latent_image": latent,
                    "denoise": denoise,
                },
            ),
            ToolNode(
                id="decode",
                tool="comfy.VAEDecode",
                inputs={
                    "samples": output_ref("sample", "output_0"),
                    "vae": output_ref("model", "output_2"),
                },
            ),
        ]
    )
    final_ref = output_ref("decode", "output_0")
    if _ACTION_REGISTRY.execution(action_id).get("postprocess") == "pixelize":
        nodes.extend(
            [
                ToolNode(
                    id="isolate",
                    tool="cooksprite.isolate_on_green",
                    inputs={"image": output_ref("decode", "output_0")},
                    params={"tolerance": literal(0.22)},
                ),
                ToolNode(
                    id="pixel",
                    tool="cooksprite.pixelize",
                    inputs={"image": output_ref("isolate", "image")},
                    params={
                        "target_size": literal(128),
                        "target_width": literal(128),
                        "target_height": literal(128),
                        "profile": literal("fidelity"),
                        "palette_budget": literal(0),
                        "padding_x": literal(-1),
                        "padding_y": literal(-1),
                        "variants": literal(False),
                        "outline": literal(True),
                        "outline_color": literal("#000000"),
                        "enabled": input_ref("pixel_enabled"),
                    },
                ),
            ]
        )
        final_ref = output_ref("pixel", "image")
    return WorkflowDefinition(
        id=f"{recipe.id}.{action_id}.{mode}",
        title=f"{recipe.label} · {action_id} · {mode}",
        runtime_id=runtime_id,
        inputs=inputs,
        nodes=nodes,
        outputs={"image": final_ref},
        output_sources={"image": input_ref(source_slot)} if source_slot else {},
    )


def _normal_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=f"{recipe.id}.normal.generate",
        title=f"{recipe.label} · normal.generate",
        runtime_id=runtime_id,
        inputs={"source": "ImageBatch", "strength": "Number", "flip_y": "Boolean"},
        nodes=[
            ToolNode(
                id="normal",
                tool="cooksprite.normal_estimate",
                inputs={"image": input_ref("source")},
                params={"strength": input_ref("strength"), "flip_y": input_ref("flip_y")},
            )
        ],
        outputs={"normal": output_ref("normal", "normal")},
        output_sources={"normal": input_ref("source")},
    )


def _normal_pixel_plan_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    """Lotus followed by a source-verified PixelGeometryPlan projection."""

    return WorkflowDefinition(
        id=f"{recipe.id}.normal.generate.pixel-plan",
        title=f"{recipe.label} · normal.generate · pixel plan",
        runtime_id=runtime_id,
        inputs={
            "source": "ImageBatch",
            "pixel_plan": "PixelGeometryPlan",
            "frame_index": "Number",
            "strength": "Number",
            "flip_y": "Boolean",
        },
        nodes=[
            ToolNode(
                id="normal",
                tool="cooksprite.normal_estimate",
                inputs={"image": input_ref("source")},
                params={"strength": input_ref("strength"), "flip_y": input_ref("flip_y")},
            ),
            ToolNode(
                id="project",
                tool="cooksprite.project_normal_to_pixel_plan",
                inputs={
                    "source": input_ref("source"),
                    "normal": output_ref("normal", "normal"),
                    "pixel_plan": input_ref("pixel_plan"),
                },
                params={"frame_index": input_ref("frame_index")},
            ),
        ],
        outputs={"normal": output_ref("project", "normal")},
        output_sources={"normal": input_ref("source")},
    )


def _normal_temporal_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    """Lower the typed FrameSeq directly into the streamed NormalCrafter node."""

    return WorkflowDefinition(
        id=f"{recipe.id}.normal.generate.temporal",
        title=f"{recipe.label} · normal.generate · temporal",
        runtime_id=runtime_id,
        inputs={
            "source": "FrameSeq",
            "max_resolution": "Number",
            "window_size": "Number",
            "time_step_size": "Number",
            "decode_chunk_size": "Number",
            "strength": "Number",
            "flip_y": "Boolean",
        },
        nodes=[
            ToolNode(
                id="normal",
                tool="cooksprite.normal_estimate_temporal",
                inputs={"source": input_ref("source")},
                params={
                    "max_resolution": input_ref("max_resolution"),
                    "window_size": input_ref("window_size"),
                    "time_step_size": input_ref("time_step_size"),
                    "decode_chunk_size": input_ref("decode_chunk_size"),
                    "strength": input_ref("strength"),
                    "flip_y": input_ref("flip_y"),
                },
            )
        ],
        outputs={"normal": output_ref("normal", "normal")},
        output_sources={"normal": input_ref("source")},
    )


def _sprite_pixel_workflow(
    runtime_id: str,
    recipe: Recipe,
    *,
    temporal: bool = False,
) -> WorkflowDefinition:
    normal_inputs = {"image": input_ref("source")}
    normal_params: dict[str, ValueRef] = {
        "strength": input_ref("strength"),
        "flip_y": input_ref("flip_y"),
    }
    inputs: dict[str, str] = {
        "source": "ImageBatch",
        "strength": "Number",
        "flip_y": "Boolean",
        "target_size": "Number",
        "palette_budget": "Number",
        "outline": "Boolean",
        "outline_color": "Text",
    }
    normal_tool = "cooksprite.normal_estimate"
    if temporal:
        normal_tool = "cooksprite.normal_estimate_temporal_batch"
        inputs.update(
            {
                "max_resolution": "Number",
                "window_size": "Number",
                "time_step_size": "Number",
                "decode_chunk_size": "Number",
            }
        )
        normal_params.update(
            {
                "max_resolution": input_ref("max_resolution"),
                "window_size": input_ref("window_size"),
                "time_step_size": input_ref("time_step_size"),
                "decode_chunk_size": input_ref("decode_chunk_size"),
            }
        )
    return WorkflowDefinition(
        id=f"{recipe.id}.sprite.pixelize{'.temporal' if temporal else ''}",
        title=f"{recipe.label} · sprite.pixelize",
        runtime_id=runtime_id,
        inputs=inputs,
        nodes=[
            ToolNode(
                id="normal",
                tool=normal_tool,
                inputs=normal_inputs,
                params=normal_params,
            ),
            ToolNode(
                id="pixel",
                tool="cooksprite.pixelize_pair",
                inputs={
                    "image": input_ref("source"),
                    "normal": output_ref("normal", "normal"),
                    "normal_mask": output_ref("normal", "mask"),
                },
                params={
                    "target_size": input_ref("target_size"),
                    "target_width": literal(128),
                    "target_height": literal(128),
                    "profile": literal("fidelity"),
                    "palette_budget": input_ref("palette_budget"),
                    "padding_x": literal(-1),
                    "padding_y": literal(-1),
                    "variants": literal(False),
                    "enabled": literal(True),
                    "outline": input_ref("outline"),
                    "outline_color": input_ref("outline_color"),
                    "sequence_mode": literal("auto"),
                },
            ),
        ],
        outputs={
            "image": output_ref("pixel", "image"),
            "normal": output_ref("pixel", "normal"),
        },
        output_sources={"image": input_ref("source"), "normal": input_ref("source")},
    )


def _sheet_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    controls = {
        "columns": "Number",
        "rows": "Number",
        "frame_width": "Number",
        "frame_height": "Number",
        "margin": "Number",
        "spacing": "Number",
        "exclude_empty": "Boolean",
    }
    return WorkflowDefinition(
        id=f"{recipe.id}.sheet.slice",
        title=f"{recipe.label} · sheet.slice",
        runtime_id=runtime_id,
        inputs={"sheet": "SpriteSheet", **controls},
        nodes=[
            ToolNode(
                id="slice",
                tool="cooksprite.slice_sprite_sheet",
                inputs={"image": input_ref("sheet")},
                params={key: input_ref(key) for key in controls},
            )
        ],
        outputs={"frames": output_ref("slice", "frames")},
        output_sources={"frames": input_ref("sheet")},
    )


def _video_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=f"{recipe.id}.video.sample",
        title=f"{recipe.label} · video.sample",
        runtime_id=runtime_id,
        inputs={"video": "Video", "sample_fps": "Number", "max_frames": "Number"},
        nodes=[
            ToolNode(
                id="sample",
                tool="cooksprite.sample_video",
                inputs={"video": input_ref("video")},
                params={
                    "sample_fps": input_ref("sample_fps"),
                    "max_frames": input_ref("max_frames"),
                },
            )
        ],
        outputs={"frames": output_ref("sample", "frames")},
        output_sources={"frames": input_ref("video")},
    )


def _pixel_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    """Build the smallest standalone graph for the pixelize Action."""

    return WorkflowDefinition(
        id=f"{recipe.id}.image.pixelize",
        title=f"{recipe.label} · image.pixelize",
        runtime_id=runtime_id,
        inputs={
            "source": "Image",
            "target_size": "Number",
            "palette_budget": "Number",
            "outline": "Boolean",
            "outline_color": "Text",
        },
        nodes=[
            ToolNode(
                id="pixel",
                tool="cooksprite.pixelize",
                inputs={"image": input_ref("source")},
                params={
                    "target_size": input_ref("target_size"),
                    # Legacy ports stay in the standalone graph for old
                    # ComfyUI node contracts; target_size takes precedence.
                    "target_width": literal(128),
                    "target_height": literal(128),
                    "profile": literal("fidelity"),
                    "palette_budget": input_ref("palette_budget"),
                    "padding_x": literal(-1),
                    "padding_y": literal(-1),
                    "variants": literal(False),
                    "enabled": literal(True),
                    "outline": input_ref("outline"),
                    "outline_color": input_ref("outline_color"),
                },
            )
        ],
        outputs={"image": output_ref("pixel", "image")},
        output_sources={"image": input_ref("source")},
    )


def _pixel_sequence_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    """Stream a FrameSeq through the long-sequence pixel compiler."""

    return WorkflowDefinition(
        id=f"{recipe.id}.image.pixelize.sequence",
        title=f"{recipe.label} · image.pixelize · sequence",
        runtime_id=runtime_id,
        inputs={
            "source": "FrameSeq",
            "target_size": "Number",
            "palette_budget": "Number",
            "outline": "Boolean",
            "outline_color": "Text",
            "temporal_mode": "Text",
        },
        nodes=[
            ToolNode(
                id="pixel",
                tool="cooksprite.pixelize_sequence",
                inputs={"source": input_ref("source")},
                params={
                    "target_size": input_ref("target_size"),
                    "target_width": literal(128),
                    "target_height": literal(128),
                    "profile": literal("fidelity"),
                    "palette_budget": input_ref("palette_budget"),
                    "padding_x": literal(-1),
                    "padding_y": literal(-1),
                    "outline": input_ref("outline"),
                    "outline_color": input_ref("outline_color"),
                    "temporal_mode": input_ref("temporal_mode"),
                },
            )
        ],
        outputs={"frames": output_ref("pixel", "frames"), "plan": output_ref("pixel", "plan")},
        output_sources={"frames": input_ref("source"), "plan": input_ref("source")},
    )


def _cutout_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    """Build ComfyUI's official BiRefNet background-removal workflow."""

    return WorkflowDefinition(
        id=f"{recipe.id}.image.cutout",
        title=f"{recipe.label} · image.cutout",
        runtime_id=runtime_id,
        inputs={"source": "Image"},
        nodes=[
            ToolNode(
                id="load_model",
                tool="comfy.LoadBackgroundRemovalModel",
                inputs={"bg_removal_name": literal(OFFICIAL_ALPHA_MODEL)},
            ),
            ToolNode(
                id="remove_background",
                tool="comfy.RemoveBackground",
                inputs={
                    "bg_removal_model": output_ref("load_model", "output_0"),
                    "image": input_ref("source"),
                },
            ),
            ToolNode(
                id="invert_mask",
                tool="comfy.InvertMask",
                inputs={"mask": output_ref("remove_background", "output_0")},
            ),
            ToolNode(
                id="join_alpha",
                tool="comfy.JoinImageWithAlpha",
                inputs={
                    "image": input_ref("source"),
                    "alpha": output_ref("invert_mask", "output_0"),
                },
            ),
        ],
        outputs={"image": output_ref("join_alpha", "output_0")},
        output_sources={"image": input_ref("source")},
    )


@tool_packages.recipe_builder_for("comfy.core-checkpoint")
def _build_core(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    return {
        "image.generate:t2i": _core_image_workflow(runtime_id, recipe, "image.generate", "t2i"),
        "image.generate:i2i": _core_image_workflow(
            runtime_id, recipe, "image.generate", "i2i", "reference"
        ),
        "frame.redraw:i2i": _core_image_workflow(
            runtime_id, recipe, "frame.redraw", "i2i", "source"
        ),
        "animation.generate:i2v": _core_image_workflow(
            runtime_id, recipe, "animation.generate", "i2v", "source"
        ),
    }


@tool_packages.recipe_builder_for("cooksprite.normal")
def _build_normal(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    normal = _normal_workflow(runtime_id, recipe)
    definitions = {
        "normal.generate:image-to-normal": normal,
        "normal.generate:frames-to-normal": normal,
    }
    if "image-to-pixel-normal" in recipe.modes:
        definitions["normal.generate:image-to-pixel-normal"] = _normal_pixel_plan_workflow(
            runtime_id, recipe
        )
    return definitions


@tool_packages.recipe_builder_for("cooksprite.sprite")
def _build_sprite(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    sprite = _sprite_pixel_workflow(runtime_id, recipe)
    return {
        "sprite.pixelize:image-to-sprite-pair": sprite,
        "sprite.pixelize:frames-to-sprite-pair": sprite,
    }


@tool_packages.recipe_builder_for("cooksprite.normal-temporal")
def _build_normal_temporal(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    return {"normal.generate:frames-to-normal": _normal_temporal_workflow(runtime_id, recipe)}


@tool_packages.recipe_builder_for("cooksprite.sprite-temporal")
def _build_sprite_temporal(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    return {
        "sprite.pixelize:frames-to-sprite-pair": _sprite_pixel_workflow(
            runtime_id, recipe, temporal=True
        )
    }


@tool_packages.recipe_builder_for("cooksprite.pixel")
def _build_pixel(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    definitions = {"image.pixelize:image-to-image": _pixel_workflow(runtime_id, recipe)}
    if "frames-to-frames" in recipe.modes:
        definitions["image.pixelize:frames-to-frames"] = _pixel_sequence_workflow(
            runtime_id, recipe
        )
    return definitions


@tool_packages.recipe_builder_for("comfy.flux2-klein")
def _build_flux(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    definitions: dict[str, WorkflowDefinition] = {}
    for action_id in recipe.actions:
        for mode in recipe.modes:
            if mode != "i2i":
                definitions[f"{action_id}:{mode}"] = assemble_recipe_workflow(
                    runtime_id, recipe, action_id, mode
                )
                continue
            for variant in recipe_variants(recipe):
                count = (
                    "1"
                    if not variant.workflow_variant
                    else variant.workflow_variant.removeprefix("i2i-")
                )
                definitions[f"{action_id}:{mode}:{count}"] = assemble_recipe_workflow(
                    runtime_id, variant, action_id, mode
                )
    return definitions


def _build_imported(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    if recipe.source not in {"imported", "discovered"} or not recipe.workflow:
        return {}
    return {
        f"{action_id}:{mode}": assemble_recipe_workflow(runtime_id, recipe, action_id, mode)
        for action_id in recipe.actions
        for mode in recipe.modes
    }


@tool_packages.recipe_builder_for("cooksprite.sheet")
def _build_sheet(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    return {"sheet.slice:sheet-to-frames": _sheet_workflow(runtime_id, recipe)}


@tool_packages.recipe_builder_for("cooksprite.video")
def _build_video(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    return {"video.sample:video-to-frames": _video_workflow(runtime_id, recipe)}


@tool_packages.recipe_builder_for("cooksprite.alpha")
def _build_alpha(runtime_id: str, recipe: Recipe) -> dict[str, WorkflowDefinition]:
    return {"image.cutout:image-to-image": _cutout_workflow(runtime_id, recipe)}


def materialize_recipe_workflows(
    store: Store,
    runtime_id: str,
    snapshot: str,
    recipe: Recipe,
) -> Recipe:
    builder = tool_packages.recipe_builder(recipe.family)
    definitions = builder(runtime_id, recipe) if builder else _build_imported(runtime_id, recipe)
    if not definitions:
        return recipe.bind_workflows(snapshot, {})
    refs: dict[str, dict[str, Any]] = {}
    for key, definition in definitions.items():
        revision = store.save_definition(
            "workflow",
            definition.id,
            runtime_id,
            snapshot,
            definition.model_dump(mode="json"),
        )
        refs[key] = {"id": definition.id, "revision": revision}
    return recipe.bind_workflows(snapshot, refs)


def _workflow_revision(store: Store, ref: dict[str, Any]) -> WorkflowRevision:
    row = store.definition("workflow", str(ref["id"]), int(ref["revision"]))
    if not row:
        raise ValueError(f"workflow revision is missing: {ref}")
    import json

    return WorkflowRevision.model_validate(
        {
            **json.loads(row["body"]),
            "revision": row["revision"],
            "runtime_snapshot": row["snapshot"],
        }
    )


def bind_action_task(
    store: Store,
    runtime_id: str,
    snapshot: str,
    recipe: Recipe,
    action_id: str,
    artifacts: dict[str, list[str]],
    values: dict[str, Any],
    params: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    prompt_compiler: PromptCompiler | None = None,
    action_mode: str | None = None,
) -> tuple[
    TaskRevision,
    dict[tuple[str, int], WorkflowRevision],
    dict[str, ValueRef],
    dict[str, Any],
]:
    execution = dict(execution or _ACTION_REGISTRY.execution(action_id))
    mode = action_mode or recipe_mode(action_id, artifacts)
    workflow_ref = recipe.workflow_for(action_id, artifacts, mode)
    if not workflow_ref:
        raise ValueError(f"recipe {recipe.id} has no typed workflow for {action_id}:{mode}")
    workflow = _workflow_revision(store, workflow_ref)
    params = dict(params or {})
    reserved = {
        "prompt",
        "category",
        "style",
        "action",
        "animation",
        "view",
        "direction",
        "prompt_compile",
        "count",
        "seed",
        "strength",
        "pixel_enabled",
        "resolution",
        "target_size",
        "palette_budget",
        "outline",
        "outline_color",
        "temporal_mode",
        "frame_index",
        "flip_y",
        "columns",
        "rows",
        "frame_width",
        "frame_height",
        "margin",
        "spacing",
        "exclude_empty",
        "sample_fps",
        "max_frames",
        "model",
        "runtime",
    }
    conflicts = sorted(set(params).intersection(reserved))
    if conflicts:
        raise ValueError(f"workflow params conflict with stable Action values: {conflicts}")
    unknown = sorted(set(params) - set(workflow.inputs))
    if unknown:
        raise ValueError(f"workflow params are not declared by the selected Recipe: {unknown}")
    for name, value in params.items():
        port_type = workflow.inputs[name]
        if port_type == "Text":
            valid = isinstance(value, str)
        elif port_type == "Number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif port_type == "Boolean":
            valid = isinstance(value, bool)
        else:
            valid = False
        if not valid:
            raise ValueError(f"workflow param {name} must be a scalar {port_type}")
    calls: list[WorkflowCall] = []
    task_inputs: dict[str, str] = {}
    run_inputs: dict[str, ValueRef] = {}
    task_outputs: dict[str, ValueRef] = {}

    source_slots: list[tuple[str, str, str]] = []
    static_artifact_slots: list[tuple[str, str]] = []
    binding = execution.get("artifact_binding") or {}
    expand_slot = str(binding.get("expand_slot") or "")
    aliases = {
        str(name): str(target) for name, target in (execution.get("artifact_aliases") or {}).items()
    }
    if expand_slot and artifacts.get(expand_slot):
        workflow_slot = str(binding.get("workflow_slot") or "")
        prefix = str(binding.get("input_prefix") or expand_slot)
        index_start = int(binding.get("index_start") or 0)
        source_slots = [
            (f"{prefix}_{index}", workflow_slot or f"{prefix}_{index}", artifact_id)
            for index, artifact_id in enumerate(artifacts[expand_slot], start=index_start)
        ]
        static_artifact_slots = [
            (slot, artifacts[slot][0])
            for slot in binding.get("static_slots", [])
            if artifacts.get(slot)
        ]
    else:
        for slot, artifact_ids in artifacts.items():
            if slot.startswith("__"):
                continue
            if artifact_ids:
                source_slots.append((slot, aliases.get(slot, slot), artifact_ids[0]))
        if not source_slots:
            source_slots = [("", "", "")]

    seed = int(values.get("seed", -1))
    if seed < 0:
        seed = secrets.randbelow(2**63 - 1)
    # Keep every Action value so a Recipe can declare a new workflow slot
    # without another API-side allow-list.  The aliases below normalize the
    # stable product controls to the semantic names used by old recipes.
    final_prompt, prompt_metadata = compile_action_values(action_id, mode, values, prompt_compiler)
    prepared = {**values, **params}
    prepared.update(
        {
            "prompt": final_prompt,
            "category": str(values.get("category") or ""),
            "style": str(values.get("style") or "2d_action_game"),
            "animation": str(values.get("action") or "idle"),
            "view": str(values.get("view") or "level"),
            "direction": str(values.get("direction") or "s"),
            "prompt_compile": bool(values.get("prompt_compile", True)),
            "count": max(1, min(int(values.get("count", 1)), 16)),
            "seed": seed,
            "strength": float(values.get("strength", 0.65)),
            "pixel_enabled": bool(execution.get("pixel_enabled", values.get("style") == "pixel")),
            "flip_y": bool(values.get("flip_y", False)),
            "columns": int(values.get("columns", 0)),
            "rows": int(values.get("rows", 0)),
            "frame_width": int(values.get("frame_width", 64)),
            "frame_height": int(values.get("frame_height", 64)),
            "margin": int(values.get("margin", 0)),
            "spacing": int(values.get("spacing", 0)),
            "exclude_empty": bool(values.get("exclude_empty", True)),
            "sample_fps": float(values.get("sample_fps", 12)),
            "max_frames": int(values.get("max_frames", 48)),
            "resolution": _image_resolution(values.get("resolution", 512)),
            "target_size": int(values.get("target_size", 128)),
            "palette_budget": int(values.get("palette_budget", 32)),
            "outline": bool(values.get("outline", False)),
            "outline_color": _outline_color(values.get("outline_color", "#000000")),
            "temporal_mode": str(values.get("temporal_mode", "auto")),
            "frame_index": int(values.get("frame_index", -1)),
        }
    )
    for name, schema in (recipe.params_schema.get("properties") or {}).items():
        if name not in prepared and isinstance(schema, dict) and "default" in schema:
            prepared[name] = schema["default"]

    for index, (input_name, workflow_slot, artifact_id) in enumerate(source_slots):
        call_id = f"step_{index + 1}"
        call_inputs: dict[str, ValueRef] = {}
        if input_name:
            port_type = workflow.inputs.get(workflow_slot)
            if not port_type:
                raise ValueError(
                    f"Recipe {recipe.id} does not declare artifact input {workflow_slot}"
                )
            task_inputs[input_name] = port_type
            run_inputs[input_name] = ValueRef(artifact=artifact_id)
            call_inputs[workflow_slot] = input_ref(input_name)
        for static_slot, artifact_id in static_artifact_slots:
            port_type = workflow.inputs.get(static_slot)
            if not port_type:
                raise ValueError(
                    f"Recipe {recipe.id} does not declare artifact input {static_slot}"
                )
            task_inputs.setdefault(static_slot, port_type)
            run_inputs.setdefault(static_slot, ValueRef(artifact=artifact_id))
            call_inputs[static_slot] = input_ref(static_slot)
        for name, port_type in workflow.inputs.items():
            if name in call_inputs:
                continue
            if name not in prepared:
                continue
            task_name = name
            task_inputs.setdefault(task_name, port_type)
            run_inputs.setdefault(task_name, literal(prepared[name]))
            call_inputs[name] = input_ref(task_name)
        calls.append(
            WorkflowCall(
                id=call_id,
                workflow_id=workflow.id,
                candidates=[workflow.revision],
                inputs=call_inputs,
            )
        )
        for output_name in workflow.outputs:
            task_outputs[f"{output_name}_{index + 1}"] = output_ref(call_id, output_name)

    definition = TaskDefinition(
        id=f"{action_id}.{recipe.id}",
        title=f"{action_id} · {recipe.label}",
        runtime_id=runtime_id,
        inputs=task_inputs,
        nodes=calls,
        outputs=task_outputs,
    )
    revision = store.save_definition(
        "task", definition.id, runtime_id, snapshot, definition.model_dump(mode="json")
    )
    task = TaskRevision(**definition.model_dump(), revision=revision, runtime_snapshot=snapshot)
    return task, {(workflow.id, workflow.revision): workflow}, run_inputs, prompt_metadata
