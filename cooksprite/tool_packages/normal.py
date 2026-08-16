from __future__ import annotations

from ..domain import ToolPackageManifest
from .contracts import tool

MANIFEST = ToolPackageManifest(
    id="normal",
    version="1.0.0",
    license="Apache-2.0",
    tools=[
        tool(
            "normal",
            "normal_estimate",
            "Estimate normal map",
            [("image", "Image")],
            [("normal", "NormalMap", True)],
            {"strength": {"type": "number"}, "flip_y": {"type": "boolean"}},
        ),
        tool(
            "normal",
            "make_sprite_pair",
            "Make a SpritePair",
            [("diffuse", "Image"), ("normal", "NormalMap")],
            [("diffuse", "Image", True), ("normal", "NormalMap", True)],
        ),
    ],
    lowerings={
        "cooksprite.normal_estimate": "CS_NormalEstimate",
        "cooksprite.make_sprite_pair": "CS_MakeSpritePair",
    },
    node_classes=["CS_NormalEstimate", "CS_MakeSpritePair"],
    workflows=["normal.generate:image-to-normal"],
    tasks=["normal.generate"],
    recipes=["cooksprite.normal"],
)
