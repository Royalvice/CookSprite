from __future__ import annotations

from ..domain import ToolPackageManifest
from ..workflows.lotus_normal import lotus_normal_tool_graph
from .contracts import tool

MANIFEST = ToolPackageManifest(
    id="normal",
    version="1.1.3",
    license="Apache-2.0",
    tools=[
        tool(
            "normal",
            "normal_estimate",
            "Estimate normal map",
            [("image", "ImageBatch"), ("mask", "Mask", False)],
            [("normal", "NormalMap", True), ("mask", "Mask", False)],
            {"strength": {"type": "number"}, "flip_y": {"type": "boolean"}},
        ),
        tool(
            "normal",
            "make_sprite_pair",
            "Make a SpritePair",
            [("diffuse", "Image"), ("normal", "NormalMap")],
            [("diffuse", "Image", True), ("normal", "NormalMap", True)],
        ),
        tool(
            "normal",
            "project_normal_to_pixel_plan",
            "Project a normal map through a verified PixelGeometryPlan",
            [
                ("source", "Image"),
                ("normal", "NormalMap"),
                ("mask", "Mask", False),
                ("pixel_plan", "PixelGeometryPlan"),
            ],
            [("normal", "NormalMap", True), ("mask", "Mask", False)],
            {"frame_index": {"type": "integer", "min": 0, "max": 239}},
        ),
    ],
    lowerings={
        "cooksprite.make_sprite_pair": "CS_MakeSpritePair",
        "cooksprite.project_normal_to_pixel_plan": "CS_ProjectNormalToPixelPlan",
    },
    sealed_graphs={"cooksprite.normal_estimate": lotus_normal_tool_graph()},
    node_classes=[
        "CS_LotusModelLoader",
        "CS_LotusNormalPrepare",
        "CS_LotusNormalFinalize",
        "CS_ProjectNormalToPixelPlan",
        "CS_MakeSpritePair",
    ],
    workflows=["normal.generate:image-to-normal", "normal.generate:image-to-pixel-normal"],
    tasks=["normal.generate"],
    recipes=["cooksprite.normal"],
)
