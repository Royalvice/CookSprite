from __future__ import annotations

from ..domain import ToolPackageManifest
from ..workflows.lotus_normal import lotus_normal_tool_graph
from .contracts import tool

MANIFEST = ToolPackageManifest(
    id="normal",
    version="1.2.0",
    license="Apache-2.0 AND MIT",
    requirements=["accelerate>=1.1,<2", "diffusers>=0.35.1,<0.36"],
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
            "normal_estimate_temporal",
            "Estimate temporally stable normal maps with NormalCrafter",
            [("source", "FrameSeq")],
            [("normal", "NormalMapSequence", True)],
            {
                "max_resolution": {"type": "integer", "min": 256, "max": 1024},
                "window_size": {"type": "integer", "min": 2, "max": 32},
                "time_step_size": {"type": "integer", "min": 1, "max": 32},
                "decode_chunk_size": {"type": "integer", "min": 1, "max": 32},
                "strength": {"type": "number", "min": 0, "max": 4},
                "flip_y": {"type": "boolean"},
            },
        ),
        tool(
            "normal",
            "normal_estimate_temporal_batch",
            "Estimate a temporal normal batch with NormalCrafter",
            [("image", "ImageBatch"), ("mask", "Mask", False)],
            [("normal", "NormalMap", True), ("mask", "Mask", False)],
            {
                "max_resolution": {"type": "integer", "min": 256, "max": 1024},
                "window_size": {"type": "integer", "min": 2, "max": 32},
                "time_step_size": {"type": "integer", "min": 1, "max": 32},
                "decode_chunk_size": {"type": "integer", "min": 1, "max": 32},
                "strength": {"type": "number", "min": 0, "max": 4},
                "flip_y": {"type": "boolean"},
            },
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
        "cooksprite.normal_estimate_temporal": "CS_NormalCrafterSequence",
        "cooksprite.normal_estimate_temporal_batch": "CS_NormalCrafterBatch",
        "cooksprite.make_sprite_pair": "CS_MakeSpritePair",
        "cooksprite.project_normal_to_pixel_plan": "CS_ProjectNormalToPixelPlan",
    },
    sealed_graphs={"cooksprite.normal_estimate": lotus_normal_tool_graph()},
    node_classes=[
        "CS_LotusModelLoader",
        "CS_LotusNormalPrepare",
        "CS_LotusNormalFinalize",
        "CS_NormalCrafterSequence",
        "CS_NormalCrafterBatch",
        "CS_ProjectNormalToPixelPlan",
        "CS_MakeSpritePair",
    ],
    workflows=[
        "normal.generate:image-to-normal",
        "normal.generate:image-to-pixel-normal",
        "normal.generate:frames-to-normal",
        "sprite.pixelize:frames-to-sprite-pair",
    ],
    tasks=["normal.generate", "sprite.pixelize"],
    recipes=["cooksprite.normal", "cooksprite.normal-temporal", "cooksprite.sprite-temporal"],
)
