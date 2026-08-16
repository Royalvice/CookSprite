from __future__ import annotations

from ..domain import ToolPackageManifest
from .contracts import tool

MANIFEST = ToolPackageManifest(
    id="frames",
    version="1.0.0",
    license="Apache-2.0",
    requirements=["imageio>=2.34", "imageio-ffmpeg>=0.5"],
    tools=[
        tool(
            "frames",
            "slice_sprite_sheet",
            "Slice a SpriteSheet",
            [("image", "SpriteSheet")],
            [("frames", "ImageBatch", True)],
            {
                "columns": {"type": "integer"},
                "rows": {"type": "integer"},
                "frame_width": {"type": "integer"},
                "frame_height": {"type": "integer"},
                "margin": {"type": "integer"},
                "spacing": {"type": "integer"},
                "exclude_empty": {"type": "boolean"},
            },
        ),
        tool(
            "frames",
            "sample_video",
            "Sample a video artifact",
            [("video", "Video")],
            [("frames", "ImageBatch", True)],
            {
                "sample_fps": {"type": "number"},
                "max_frames": {"type": "integer"},
            },
        ),
    ],
    lowerings={
        "cooksprite.slice_sprite_sheet": "CS_SliceSpriteSheet",
        "cooksprite.sample_video": "CS_LoadVideoArtifact",
    },
    node_classes=["CS_SliceSpriteSheet", "CS_LoadVideoArtifact"],
    workflows=["sheet.slice:sheet-to-frames", "video.sample:video-to-frames"],
    tasks=["sheet.slice", "video.sample"],
    recipes=["cooksprite.sheet", "cooksprite.video"],
)
