from __future__ import annotations

from ..domain import ToolPackageManifest
from .contracts import tool

_PARAMS = {
    "action_id": {"type": "string"},
    "prompt": {"type": "string"},
    "category": {"type": "string"},
    "style": {"type": "string"},
    "animation": {"type": "string"},
    "view": {"type": "string"},
    "direction": {"type": "string"},
    "task": {"type": "string"},
    "mode": {"type": "string"},
    "caption": {"type": "string"},
    "compile_prompt": {"type": "boolean"},
    "action": {"type": "string"},
    "camera_preset": {"type": "string"},
    "orientation": {"type": "string"},
    "facing": {"type": "string"},
    "model": {"type": "string"},
    "width": {"type": "integer"},
    "height": {"type": "integer"},
    "background": {"type": "string"},
    "edit_instruction": {"type": "string"},
    "negative_terms": {"type": "string"},
}

MANIFEST = ToolPackageManifest(
    id="prompt",
    version="1.3.0",
    license="Apache-2.0",
    tools=[
        tool(
            "prompt",
            "compile_prompt_packet",
            "Compile a model-neutral Sprite prompt packet",
            [],
            [
                ("prompt", "Text", False),
                ("negative_prompt", "Text", False),
                ("metadata", "Text", False),
            ],
            _PARAMS,
        )
    ],
    lowerings={"cooksprite.compile_prompt_packet": "CS_CompilePromptPacket"},
    node_classes=["CS_CompilePromptPacket"],
)
