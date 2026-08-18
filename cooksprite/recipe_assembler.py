"""Generic runtime Recipe -> typed CookSprite Workflow assembly.

Recipes describe a concrete ComfyUI graph through semantic slots.  This
module is the single adapter between that runtime-owned graph and stable
CookSprite Tasks.  It deliberately does not know model families: a new raw
workflow only needs a Recipe contract (graph, slots, and output).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .domain import PortDescriptor, ToolDescriptor, ToolNode, ValueRef, WorkflowDefinition
from .prompting import DEFAULT_GREEN_SCREEN_BACKGROUND
from .recipes import Recipe

SEMANTIC_SLOT_TYPES = {
    "image": "Image",
    "source": "Image",
    "reference": "Image",
    "mask": "Mask",
    "video": "Video",
    "seed": "Number",
    "count": "Number",
    "strength": "Number",
    "width": "Number",
    "height": "Number",
}


def literal(value: Any) -> ValueRef:
    return ValueRef(literal=value)


def input_ref(name: str) -> ValueRef:
    return ValueRef(input=name)


def output_ref(node: str, output: str) -> ValueRef:
    return ValueRef(node=node, output=output)


def _slot_type(recipe: Recipe, slot: str) -> str:
    return str(recipe.slot_types.get(slot) or SEMANTIC_SLOT_TYPES.get(slot) or "Text")


def _sealed_tool_id(recipe: Recipe) -> str:
    suffix = f".{recipe.workflow_variant}" if recipe.workflow_variant else ""
    return f"comfy.sealed.{recipe.id}{suffix}"


def sealed_tool_descriptor(recipe: Recipe) -> ToolDescriptor | None:
    """Expose one raw runtime graph as a typed, sealed Tool."""

    if recipe.source not in {"imported", "discovered"} or not recipe.workflow:
        return None
    inputs = [
        PortDescriptor(name=name, type=_slot_type(recipe, name), required=False)
        for name in recipe.slots
    ]
    return ToolDescriptor(
        id=_sealed_tool_id(recipe),
        source="comfy",
        title=f"ComfyUI workflow · {recipe.label}",
        inputs=inputs,
        outputs=[
            PortDescriptor(
                name=recipe.output_name,
                type=recipe.output_type,
                persistable=True,
            )
        ],
    )


def _source_slot(action_id: str, mode: str) -> str:
    if mode not in {"i2i", "i2v", "i2i-sequence"}:
        return ""
    return "reference" if action_id == "image.generate" else "source"


def _task_inputs(recipe: Recipe, action_id: str, mode: str) -> dict[str, str]:
    """Build the stable semantic inputs shared by all model workflows."""

    inputs = {
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
        "resolution": "Number",
        "target_size": "Number",
        "palette_budget": "Number",
        "detail_level": "Text",
    }
    source_slot = _source_slot(action_id, mode)
    if source_slot and not any(name.startswith("reference_") for name in recipe.slots):
        inputs[source_slot] = "Image"

    # A workflow may expose additional typed controls.  They become Task
    # inputs from the Recipe contract without a new API code path.
    for slot in recipe.slots:
        if slot in {"text", "prompt", "negative", "negative_prompt", "model", "width", "height"}:
            continue
        if slot in {"image", "source", "reference"}:
            continue
        inputs.setdefault(slot, _slot_type(recipe, slot))
    return inputs


def with_dimension_slots(recipe: Recipe) -> Recipe:
    """Expose the first graph node with width and height as resolution slots.

    Runtime-discovered workflows often hard-code their latent or image scale
    dimensions instead of declaring semantic slots.  This small structural
    adapter keeps resolution a CookSprite control without adding a model
    family branch or changing the raw workflow contract.
    """

    if not recipe.workflow:
        return recipe
    slots = dict(recipe.slots)
    slot_types = dict(recipe.slot_types)
    for node_id, node in recipe.workflow.items():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict) or not {"width", "height"}.issubset(inputs):
            continue
        slots.setdefault("width", f"{node_id}.width")
        slots.setdefault("height", f"{node_id}.height")
        slot_types.setdefault("width", "Number")
        slot_types.setdefault("height", "Number")
        break
    if slots == recipe.slots and slot_types == recipe.slot_types:
        return recipe
    return replace(recipe, slots=slots, slot_types=slot_types)


def _bind_recipe_slots(
    recipe: Recipe,
    action_id: str,
    mode: str,
    descriptor: ToolDescriptor,
) -> dict[str, ValueRef]:
    source_slot = _source_slot(action_id, mode)
    bindings: dict[str, ValueRef] = {}
    for slot in recipe.slots:
        if slot in {"text", "prompt"}:
            bindings[slot] = output_ref("packet", "prompt")
        elif slot in {"negative", "negative_prompt"}:
            bindings[slot] = output_ref("packet", "negative_prompt")
        elif slot == "model":
            bindings[slot] = literal(recipe.checkpoint or "")
        elif slot in {"seed", "count", "strength"}:
            bindings[slot] = input_ref(slot)
        elif slot in {"width", "height"}:
            bindings[slot] = input_ref("resolution")
        elif slot in {"image", "source", "reference"}:
            if source_slot:
                bindings[slot] = input_ref(source_slot)
        elif slot.startswith("reference_"):
            bindings[slot] = input_ref(slot)
        else:
            bindings[slot] = input_ref(slot)

    declared = {item.name for item in descriptor.inputs}
    return {name: value for name, value in bindings.items() if name in declared}


def prompt_packet(action_id: str, mode: str) -> ToolNode:
    task = "video" if action_id.startswith("animation.") or mode in {"i2v", "t2v"} else "image"
    return ToolNode(
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
            "task": literal(task),
            "mode": literal(mode),
            "caption": input_ref("prompt"),
            "compile_prompt": input_ref("prompt_compile"),
            "action": input_ref("animation"),
            "camera_option": literal("front_eye_level"),
            "camera_preset": input_ref("view") if task == "video" else literal("eye_level"),
            "orientation": literal("front"),
            "facing": literal("right"),
            "model": literal("generic"),
            "width": input_ref("resolution"),
            "height": input_ref("resolution"),
            "background": literal(DEFAULT_GREEN_SCREEN_BACKGROUND),
            "edit_instruction": literal(""),
            "negative_terms": literal(""),
        },
    )


def assemble_recipe_workflow(
    runtime_id: str,
    recipe: Recipe,
    action_id: str,
    mode: str,
) -> WorkflowDefinition:
    """Wrap any compatible raw Comfy graph with CookSprite Tool modules.

    The raw graph is sealed and only receives semantic slot bindings.  Prompt
    compilation, pixel policy, and persistence therefore remain API-composed
    modules instead of being copied into each model workflow.
    """

    recipe = with_dimension_slots(recipe)
    descriptor = sealed_tool_descriptor(recipe)
    if not descriptor:
        raise ValueError("Recipe has no compatible raw workflow contract")
    source_slot = _source_slot(action_id, mode)
    inputs = _task_inputs(recipe, action_id, mode)
    nodes = [prompt_packet(action_id, mode)]
    nodes.append(
        ToolNode(
            id="sealed",
            tool=descriptor.id,
            inputs=_bind_recipe_slots(recipe, action_id, mode, descriptor),
        )
    )

    output_name = descriptor.outputs[0].name
    final_ref = output_ref("sealed", output_name)
    if descriptor.outputs[0].type == "Image" and action_id != "image.generate":
        nodes.append(
            ToolNode(
                id="pixel",
                tool="cooksprite.pixelize",
                inputs={"image": final_ref},
                params={
                    "target_size": input_ref("target_size"),
                    "target_width": literal(128),
                    "target_height": literal(128),
                    "profile": input_ref("detail_level"),
                    "palette_budget": input_ref("palette_budget"),
                    "padding_x": literal(-1),
                    "padding_y": literal(-1),
                    "variants": literal(False),
                    "enabled": input_ref("pixel_enabled"),
                },
            )
        )
        final_ref = output_ref("pixel", "image")

    return WorkflowDefinition(
        id=f"{recipe.id}.{action_id}.{mode}",
        title=f"{recipe.label} · {action_id} · {mode}",
        runtime_id=runtime_id,
        inputs=inputs,
        nodes=nodes,
        outputs={output_name: final_ref},
        output_sources={
            output_name: input_ref(
                "reference_1"
                if source_slot == "reference" and "reference_1" in inputs
                else source_slot
            )
        }
        if source_slot
        else {},
    )


__all__ = [
    "assemble_recipe_workflow",
    "input_ref",
    "literal",
    "output_ref",
    "prompt_packet",
    "sealed_tool_descriptor",
    "with_dimension_slots",
]
