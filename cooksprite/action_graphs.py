"""Bind stable Actions to typed Task/Workflow definitions.

This module may choose and compose immutable definitions, but it never emits a
Comfy graph. Only :mod:`cooksprite.compiler` performs that lowering.
"""

from __future__ import annotations

import secrets
from typing import Any

from .domain import (
    PortDescriptor,
    TaskDefinition,
    TaskRevision,
    ToolDescriptor,
    ToolNode,
    ValueRef,
    WorkflowCall,
    WorkflowDefinition,
    WorkflowRevision,
)
from .prompting import DEFAULT_GREEN_SCREEN_BACKGROUND
from .recipes import Recipe, recipe_mode
from .store import Store


def literal(value: Any) -> ValueRef:
    return ValueRef(literal=value)


def input_ref(name: str) -> ValueRef:
    return ValueRef(input=name)


def output_ref(node: str, output: str) -> ValueRef:
    return ValueRef(node=node, output=output)


def _core_image_workflow(
    runtime_id: str,
    recipe: Recipe,
    action_id: str,
    mode: str,
) -> WorkflowDefinition:
    if not recipe.checkpoint:
        raise ValueError("core image recipe requires a checkpoint")
    inputs: dict[str, str] = {
        "prompt": "Text",
        "category": "Text",
        "style": "Text",
        "animation": "Text",
        "view": "Text",
        "direction": "Text",
        "prompt_compile": "Boolean",
        "count": "Number",
        "seed": "Number",
        "strength": "Number",
        "pixel_enabled": "Boolean",
    }
    source_slot = ""
    if mode in {"i2i", "i2v"}:
        source_slot = "reference" if action_id == "image.generate" else "source"
        inputs[source_slot] = "Image"

    nodes = [
        ToolNode(
            id="packet",
            tool="cooksprite.compile_prompt_packet",
            params={
                "action_id": literal(action_id),
                "prompt": input_ref("prompt"),
                "category": input_ref("category"),
                "style": input_ref("style"),
                "animation": input_ref("animation"),
                "view": input_ref("view"),
                "direction": input_ref("direction"),
                "task": literal("video" if action_id == "animation.generate" else "image"),
                "mode": literal(mode),
                "caption": input_ref("prompt"),
                "compile_prompt": input_ref("prompt_compile"),
                "action": input_ref("animation"),
                "camera_preset": input_ref("view"),
                "orientation": literal("front"),
                "facing": literal("right"),
                "model": literal("generic"),
                "width": literal(512),
                "height": literal(512),
                "background": literal(DEFAULT_GREEN_SCREEN_BACKGROUND),
                "edit_instruction": literal(""),
                "negative_terms": literal(""),
            },
        ),
        ToolNode(
            id="model",
            tool="comfy.CheckpointLoaderSimple",
            inputs={"ckpt_name": literal(recipe.checkpoint)},
        ),
        ToolNode(
            id="positive",
            tool="comfy.CLIPTextEncode",
            inputs={
                "text": output_ref("packet", "prompt"),
                "clip": output_ref("model", "output_1"),
            },
        ),
        ToolNode(
            id="negative",
            tool="comfy.CLIPTextEncode",
            inputs={
                "text": output_ref("packet", "negative_prompt"),
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
                        "width": literal(512),
                        "height": literal(512),
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
                    "width": literal(512),
                    "height": literal(512),
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
                    "target_width": literal(128),
                    "target_height": literal(128),
                    "enabled": input_ref("pixel_enabled"),
                },
            ),
        ]
    )
    return WorkflowDefinition(
        id=f"{recipe.id}.{action_id}.{mode}",
        title=f"{recipe.label} · {action_id} · {mode}",
        runtime_id=runtime_id,
        inputs=inputs,
        nodes=nodes,
        outputs={"image": output_ref("pixel", "image")},
        output_sources={"image": input_ref(source_slot)} if source_slot else {},
    )


def _normal_workflow(runtime_id: str, recipe: Recipe) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=f"{recipe.id}.normal.generate",
        title=f"{recipe.label} · normal.generate",
        runtime_id=runtime_id,
        inputs={"source": "Image", "strength": "Number", "flip_y": "Boolean"},
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


def sealed_tool_descriptor(recipe: Recipe) -> ToolDescriptor | None:
    if recipe.source not in {"imported", "discovered"} or not recipe.workflow:
        return None
    inputs = []
    for name in recipe.slots:
        port_type = {
            "image": "Image",
            "seed": "Number",
            "count": "Number",
            "strength": "Number",
        }.get(name, "Text")
        inputs.append(PortDescriptor(name=name, type=port_type, required=False))
    return ToolDescriptor(
        id=f"comfy.sealed.{recipe.id}",
        source="comfy",
        title=f"ComfyUI workflow · {recipe.label}",
        inputs=inputs,
        outputs=[PortDescriptor(name="image", type="Image", persistable=True)],
    )


def _imported_workflow(
    runtime_id: str,
    recipe: Recipe,
    action_id: str,
    mode: str,
) -> WorkflowDefinition:
    descriptor = sealed_tool_descriptor(recipe)
    if not descriptor:
        raise ValueError("imported Recipe has no sealed Tool")
    inputs: dict[str, str] = {
        "prompt": "Text",
        "category": "Text",
        "style": "Text",
        "animation": "Text",
        "view": "Text",
        "direction": "Text",
        "prompt_compile": "Boolean",
        "count": "Number",
        "seed": "Number",
        "strength": "Number",
        "pixel_enabled": "Boolean",
    }
    source_slot = ""
    if mode in {"i2i", "i2v"}:
        source_slot = "reference" if action_id == "image.generate" else "source"
        inputs[source_slot] = "Image"
    sealed_inputs: dict[str, ValueRef] = {}
    for slot in recipe.slots:
        if slot == "text":
            sealed_inputs[slot] = output_ref("packet", "prompt")
        elif slot == "negative":
            sealed_inputs[slot] = output_ref("packet", "negative_prompt")
        elif slot == "model":
            sealed_inputs[slot] = literal(recipe.checkpoint or "")
        elif slot in {"seed", "count"}:
            sealed_inputs[slot] = input_ref(slot)
        elif slot == "strength":
            sealed_inputs[slot] = input_ref("strength")
        elif slot == "image" and source_slot:
            sealed_inputs[slot] = input_ref(source_slot)
    nodes = [
        ToolNode(
            id="packet",
            tool="cooksprite.compile_prompt_packet",
            params={
                "action_id": literal(action_id),
                "prompt": input_ref("prompt"),
                "category": input_ref("category"),
                "style": input_ref("style"),
                "animation": input_ref("animation"),
                "view": input_ref("view"),
                "direction": input_ref("direction"),
                "task": literal("video" if action_id == "animation.generate" else "image"),
                "mode": literal(mode),
                "caption": input_ref("prompt"),
                "compile_prompt": input_ref("prompt_compile"),
                "action": input_ref("animation"),
                "camera_preset": input_ref("view"),
                "orientation": literal("front"),
                "facing": literal("right"),
                "model": literal("generic"),
                "width": literal(512),
                "height": literal(512),
                "background": literal(DEFAULT_GREEN_SCREEN_BACKGROUND),
                "edit_instruction": literal(""),
                "negative_terms": literal(""),
            },
        ),
        ToolNode(id="sealed", tool=descriptor.id, inputs=sealed_inputs),
        ToolNode(
            id="pixel",
            tool="cooksprite.pixelize",
            inputs={"image": output_ref("sealed", "image")},
            params={
                "target_width": literal(128),
                "target_height": literal(128),
                "enabled": input_ref("pixel_enabled"),
            },
        ),
    ]
    return WorkflowDefinition(
        id=f"{recipe.id}.{action_id}.{mode}",
        title=f"{recipe.label} · {action_id} · {mode}",
        runtime_id=runtime_id,
        inputs=inputs,
        nodes=nodes,
        outputs={"image": output_ref("pixel", "image")},
        output_sources={"image": input_ref(source_slot)} if source_slot else {},
    )


def materialize_recipe_workflows(
    store: Store,
    runtime_id: str,
    snapshot: str,
    recipe: Recipe,
) -> Recipe:
    definitions: dict[str, WorkflowDefinition] = {}
    if recipe.family == "comfy.core-checkpoint":
        definitions = {
            "image.generate:t2i": _core_image_workflow(runtime_id, recipe, "image.generate", "t2i"),
            "image.generate:i2i": _core_image_workflow(runtime_id, recipe, "image.generate", "i2i"),
            "frame.redraw:i2i": _core_image_workflow(runtime_id, recipe, "frame.redraw", "i2i"),
            "animation.generate:i2v": _core_image_workflow(
                runtime_id, recipe, "animation.generate", "i2v"
            ),
        }
    elif recipe.family == "cooksprite.normal":
        definitions = {"normal.generate:image-to-normal": _normal_workflow(runtime_id, recipe)}
    elif recipe.family == "cooksprite.sheet":
        definitions = {"sheet.slice:sheet-to-frames": _sheet_workflow(runtime_id, recipe)}
    elif recipe.family == "cooksprite.video":
        definitions = {"video.sample:video-to-frames": _video_workflow(runtime_id, recipe)}
    elif recipe.source in {"imported", "discovered"} and recipe.workflow:
        for action_id in recipe.actions:
            for mode in recipe.modes:
                if action_id == "image.generate" and mode not in {"t2i", "i2i"}:
                    continue
                if action_id == "frame.redraw" and mode != "i2i":
                    continue
                if action_id == "animation.generate" and mode not in {"i2v", "t2v"}:
                    continue
                definitions[f"{action_id}:{mode}"] = _imported_workflow(
                    runtime_id, recipe, action_id, mode
                )
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
) -> tuple[TaskRevision, dict[tuple[str, int], WorkflowRevision], dict[str, ValueRef]]:
    workflow_ref = recipe.workflow_for(action_id, artifacts)
    if not workflow_ref:
        mode = recipe_mode(action_id, artifacts)
        raise ValueError(f"recipe {recipe.id} has no typed workflow for {action_id}:{mode}")
    workflow = _workflow_revision(store, workflow_ref)
    calls: list[WorkflowCall] = []
    task_inputs: dict[str, str] = {}
    run_inputs: dict[str, ValueRef] = {}
    task_outputs: dict[str, ValueRef] = {}

    source_slots: list[tuple[str, str]] = []
    if action_id == "normal.generate":
        source_slots = [
            (f"source_{index}", artifact_id)
            for index, artifact_id in enumerate(artifacts["source"])
        ]
    else:
        for slot, artifact_ids in artifacts.items():
            if artifact_ids:
                source_slots.append((slot, artifact_ids[0]))
        if not source_slots:
            source_slots = [("", "")]

    seed = int(values.get("seed", -1))
    if seed < 0:
        seed = secrets.randbelow(2**63 - 1)
    prepared = {
        "prompt": str(values.get("prompt") or ""),
        "category": str(values.get("category") or ""),
        "style": str(values.get("style") or "smooth"),
        "animation": str(values.get("action") or "idle"),
        "view": str(values.get("view") or "level"),
        "direction": str(values.get("direction") or "s"),
        "prompt_compile": bool(values.get("prompt_compile", True)),
        "count": max(1, min(int(values.get("count", 1)), 16)),
        "seed": seed,
        "strength": max(0.01, min(float(values.get("strength", 0.65)), 1.0)),
        "pixel_enabled": action_id == "image.generate" and values.get("style") == "pixel",
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
    }

    for index, (slot, artifact_id) in enumerate(source_slots):
        call_id = f"step_{index + 1}"
        call_inputs: dict[str, ValueRef] = {}
        if slot:
            input_name = slot if action_id != "normal.generate" else f"source_{index}"
            task_inputs[input_name] = "Image" if action_id != "video.sample" else "Video"
            if action_id == "sheet.slice":
                task_inputs[input_name] = "SpriteSheet"
            run_inputs[input_name] = ValueRef(artifact=artifact_id)
            workflow_slot = (
                "source"
                if action_id in {"normal.generate", "frame.redraw", "animation.generate"}
                else slot
            )
            call_inputs[workflow_slot] = input_ref(input_name)
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
    return task, {(workflow.id, workflow.revision): workflow}, run_inputs
