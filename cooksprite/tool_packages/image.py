from __future__ import annotations

from ..domain import ToolPackageManifest
from .contracts import tool

MANIFEST = ToolPackageManifest(
    id="image",
    version="1.0.0",
    license="Apache-2.0",
    requirements=["scipy>=1.11"],
    tools=[
        tool(
            "image",
            "pixelize",
            "Pixelize",
            [("image", "Image")],
            [("image", "Image", True)],
            {
                "target_width": {"type": "integer"},
                "target_height": {"type": "integer"},
                "enabled": {"type": "boolean"},
            },
        ),
        tool(
            "image",
            "isolate_on_green",
            "Isolate on chroma green",
            [("image", "Image")],
            [("image", "Image", True)],
            {"tolerance": {"type": "number"}},
        ),
    ],
    lowerings={
        "cooksprite.pixelize": "CS_Pixelize",
        "cooksprite.isolate_on_green": "CS_IsolateOnGreen",
    },
    node_classes=["CS_Pixelize", "CS_IsolateOnGreen"],
    workflows=[
        "image.generate:t2i",
        "image.generate:i2i",
        "frame.redraw:i2i",
        "animation.generate:i2v",
    ],
    tasks=["image.generate", "frame.redraw", "animation.generate"],
    recipes=["comfy.core-checkpoint"],
)
