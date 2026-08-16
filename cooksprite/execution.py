"""Private execution plan shared by every ComfyUI-backed CookSprite run.

The public API deals in Actions, Workflows, and Tasks.  They all lower to this
small internal contract before anything is submitted to ComfyUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import ArtifactBridge


@dataclass(frozen=True)
class ExecutionPlan:
    graph: dict[str, dict[str, Any]]
    sinks: list[str]


class PlanBuilder:
    """Build one API-format graph with the canonical artifact bridge."""

    def __init__(
        self,
        bridge: ArtifactBridge | None,
        run_id: str | None,
        *,
        node_prefix: str = "n",
    ) -> None:
        self.bridge = bridge
        self.run_id = run_id
        self.node_prefix = node_prefix
        self.sequence = 0
        self.graph: dict[str, dict[str, Any]] = {}
        self.sinks: list[str] = []

    def add(self, class_type: str, inputs: dict[str, Any]) -> str:
        self.sequence += 1
        node_id = f"{self.node_prefix}_{self.sequence}"
        self.graph[node_id] = {"class_type": class_type, "inputs": inputs}
        return node_id

    def load_artifact(self, artifact_id: str, *, video: bool = False) -> list[Any]:
        if not self.bridge or not self.run_id:
            raise ValueError("artifact references require a signed runtime bridge")
        class_type = "CS_LoadVideoArtifact" if video else "CS_LoadArtifact"
        input_name = "video" if video else "artifact_url"
        node_id = self.add(
            class_type,
            {input_name: self.bridge.download_url(artifact_id, self.run_id)},
        )
        return [node_id, 0]

    def store_artifact(
        self,
        value: list[Any],
        kind: str,
        *,
        source_artifact: str = "",
    ) -> str:
        if not self.bridge or not self.run_id:
            raise ValueError("persisted outputs require a signed runtime bridge")
        storage_kind = "Image" if kind == "ImageBatch" else kind
        node_id = self.add(
            "CS_StoreArtifact",
            {
                "value": value,
                "upload_url": self.bridge.upload_url(
                    self.run_id,
                    storage_kind,
                    source_artifact,
                ),
            },
        )
        self.sinks.append(node_id)
        return node_id

    def build(self) -> ExecutionPlan:
        return ExecutionPlan(graph=self.graph, sinks=self.sinks)
