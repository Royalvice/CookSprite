from __future__ import annotations

from ..domain import ToolPackageManifest
from .contracts import tool

MANIFEST = ToolPackageManifest(
    id="prompt",
    version="1.0.0",
    license="Apache-2.0",
    tools=[
        tool(
            "prompt",
            "compile_prompt_packet",
            "Compile a Sprite prompt packet",
            [],
            [("positive", "Text", False), ("negative", "Text", False)],
            {
                "action_id": {"type": "string"},
                "prompt": {"type": "string"},
                "category": {"type": "string"},
                "style": {"type": "string"},
                "animation": {"type": "string"},
                "view": {"type": "string"},
                "direction": {"type": "string"},
            },
        )
    ],
    lowerings={"cooksprite.compile_prompt_packet": "CS_CompilePromptPacket"},
    node_classes=["CS_CompilePromptPacket"],
)
