from __future__ import annotations

from ..domain import ToolPackageManifest
from .contracts import tool

MANIFEST = ToolPackageManifest(
    id="bridge",
    version="1.0.0",
    license="Apache-2.0",
    tools=[
        tool(
            "bridge",
            "load_artifact",
            "Load an artifact",
            [("artifact_url", "Text")],
            [("image", "Image", False)],
        ),
        tool(
            "bridge",
            "store_artifact",
            "Store an artifact",
            [("value", "Image"), ("upload_url", "Text")],
            [("receipt", "Text", False)],
        ),
    ],
    lowerings={
        "cooksprite.load_artifact": "CS_LoadArtifact",
        "cooksprite.store_artifact": "CS_StoreArtifact",
    },
    node_classes=["CS_LoadArtifact", "CS_StoreArtifact"],
)
