from __future__ import annotations

from ..domain import ToolPackageManifest
from .contracts import tool

REMBG_MODELS = ["u2net", "u2netp", "isnet-anime", "birefnet-general"]

MANIFEST = ToolPackageManifest(
    id="alpha",
    version="1.0.0",
    license="MIT",
    requirements=[
        "rembg>=2.0.78,<3",
        "onnxruntime-gpu>=1.23.2,<1.24",
        "Pillow>=10,<13",
    ],
    tools=[
        tool(
            "alpha",
            "remove_background",
            "Remove image background with rembg",
            [("image", "Image")],
            [("image", "Image", True), ("mask", "Mask", False)],
            {
                "model": {"type": "string", "enum": REMBG_MODELS, "default": "u2net"},
                "alpha_matting": {"type": "boolean"},
                "alpha_matting_foreground_threshold": {"type": "integer", "min": 0, "max": 255},
                "alpha_matting_background_threshold": {"type": "integer", "min": 0, "max": 255},
                "alpha_matting_erode_size": {"type": "integer", "min": 0, "max": 64},
                "batch_size": {"type": "integer", "min": 1, "max": 64},
            },
        )
    ],
    lowerings={"cooksprite.remove_background": "CS_RemoveBackground"},
    node_classes=["CS_RemoveBackground"],
    workflows=["image.cutout:image-to-image"],
    tasks=["image.cutout"],
    recipes=["cooksprite.alpha"],
)
