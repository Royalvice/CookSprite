"""Export a CookSprite workflow to ComfyUI API-format JSON.

ComfyUI API format: {node_id: {"class_type": str, "inputs": {...}}}, where
node-to-node links are ["node_id", output_index] arrays and widget values are
inline. This is a structural translation (our tools -> ComfyUI class_types,
our edges -> link arrays), not a runtime integration.

Tools without a known ComfyUI equivalent are reported in `unmapped` rather
than silently dropped (per docs/04). The caller decides whether a partial export
is acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..schema import WorkflowSpec, topo_order

# Map a CookSprite tool id -> a ComfyUI class_type. Only tools with a
# reasonable ComfyUI counterpart are listed; others are reported as unmapped.
TOOL_TO_COMFY: dict[str, str] = {
    "text2img": "CookSpriteText2Img",
    "img2img": "CookSpriteImg2Img",
    "pixelize": "CookSpritePixelize",
    "pixel_perfect": "CookSpritePixelPerfect",
    "crop_to_content": "CookSpriteCropToContent",
    "center_align": "CookSpriteCenterAlign",
    "normal_estimate": "CookSpriteNormalEstimate",
    "pack_sheet": "CookSpritePackSheet",
    "make_sprite_pair": "CookSpriteMakeSpritePair",
}

# For each tool, the ordered list of output port names, so a downstream link's
# output_index can be computed. Kept here (export-local) to avoid coupling the
# core tool model to ComfyUI details.
TOOL_OUTPUT_ORDER: dict[str, list[str]] = {
    "text2img": ["image"],
    "img2img": ["image"],
    "pixelize": ["image"],
    "pixel_perfect": ["image"],
    "crop_to_content": ["image"],
    "center_align": ["image"],
    "normal_estimate": ["normal"],
    "pack_sheet": ["sheet"],
    "make_sprite_pair": ["pair"],
}


@dataclass
class ExportResult:
    graph: dict[str, Any]
    unmapped: list[str] = field(default_factory=list)


def export_to_comfyui(spec: WorkflowSpec) -> ExportResult:
    node_by_id = {n.id: n for n in spec.nodes}
    # Stable numeric ids in topological order (ComfyUI keys are numeric strings).
    numeric_id: dict[str, str] = {nid: str(i + 1) for i, nid in enumerate(topo_order(spec))}

    graph: dict[str, Any] = {}
    unmapped: list[str] = []

    for nid, num in numeric_id.items():
        node = node_by_id[nid]
        class_type = TOOL_TO_COMFY.get(node.tool)
        if class_type is None:
            unmapped.append(node.tool)
            continue

        inputs: dict[str, Any] = {}
        # Widget values (params) inline.
        for k, v in node.params.items():
            inputs[k] = v
        # Links: port -> ["upstream_numeric_id", output_index].
        for port, ref in node.inputs.items():
            up_id, up_port = ref.split(".", 1)
            out_order = TOOL_OUTPUT_ORDER.get(node_by_id[up_id].tool, [])
            out_index = out_order.index(up_port) if up_port in out_order else 0
            inputs[port] = [numeric_id[up_id], out_index]

        graph[num] = {
            "class_type": class_type,
            "inputs": inputs,
            "_meta": {"title": nid},
        }

    return ExportResult(graph=graph, unmapped=unmapped)
