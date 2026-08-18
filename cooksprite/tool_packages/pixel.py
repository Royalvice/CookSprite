from __future__ import annotations

from ..domain import ToolPackageManifest
from .contracts import tool

PIXEL_PROFILES = ["production", "fidelity", "balanced", "graphic"]

MANIFEST = ToolPackageManifest(
    id="pixel",
    version="1.0.0",
    license="MIT",
    requirements=[
        "numpy>=1.26,<3",
        "opencv-python-headless>=4.9,<5",
        "Pillow>=10,<13",
        "PyYAML>=6,<7",
        "scikit-image>=0.22,<1",
        "scipy>=1.11,<2",
    ],
    tools=[
        tool(
            "pixel",
            "pixelize",
            "Pixelize with the deterministic CookSprite pixel compiler",
            [("image", "Image"), ("mask", "Mask", False)],
            [("image", "Image", True), ("mask", "Mask", False)],
            {
                "target_width": {"type": "integer", "min": 16, "max": 512},
                "target_height": {"type": "integer", "min": 16, "max": 512},
                "profile": {"type": "string", "enum": PIXEL_PROFILES},
                "palette_budget": {"type": "integer", "min": 0, "max": 256},
                "padding_x": {"type": "integer", "min": -1, "max": 256},
                "padding_y": {"type": "integer", "min": -1, "max": 256},
                "variants": {"type": "boolean"},
            },
        ),
        tool(
            "pixel",
            "pixel_snap",
            "Recover a native pseudo-pixel grid",
            [("image", "Image"), ("mask", "Mask", False)],
            [("image", "Image", True), ("mask", "Mask", False)],
            {
                "grid_mode": {"type": "string", "enum": ["auto", "manual"]},
                "pixel_size_x": {"type": "number", "min": 0},
                "pixel_size_y": {"type": "number", "min": 0},
                "phase_x": {"type": "number"},
                "phase_y": {"type": "number"},
                "constrained_warp": {"type": "boolean"},
                "palette_budget": {"type": "integer", "min": 2, "max": 256},
                "target_width": {"type": "integer", "min": 0, "max": 512},
                "target_height": {"type": "integer", "min": 0, "max": 512},
            },
        ),
    ],
    lowerings={
        "cooksprite.pixelize": "CS_Pixelize",
        "cooksprite.pixel_snap": "CS_PixelSnap",
    },
    node_classes=["CS_Pixelize", "CS_PixelSnap"],
    workflows=["image.pixelize:image-to-image"],
    tasks=["image.pixelize"],
    recipes=["cooksprite.pixel"],
)
