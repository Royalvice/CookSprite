"""Versioned public CookSprite contracts.

The models in this module are deliberately free of ComfyUI implementation
identifiers.  Comfy graphs are private compilation output.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PortType = Literal[
    "Image",
    "ImageBatch",
    "SpriteSheet",
    "FrameSeq",
    "Video",
    "Mask",
    "NormalMap",
    "Palette",
    "SpritePair",
    "CookSpritePack",
    "Text",
    "Number",
    "Boolean",
    "MODEL",
    "CLIP",
    "VAE",
    "LATENT",
    "CONDITIONING",
]
PersistableType = Literal[
    "Image",
    "ImageBatch",
    "SpriteSheet",
    "FrameSeq",
    "Video",
    "Mask",
    "NormalMap",
    "Palette",
    "SpritePair",
    "CookSpritePack",
]
ClipAction = Literal["idle", "walk", "run", "attack", "cast", "hit", "jump", "death"]
ViewId = Literal["level", "top45"]
Direction = Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"]


class RuntimeAssetRef(BaseModel):
    runtime_id: str
    kind: str
    asset_id: str


class ValueRef(BaseModel):
    """A structured value edge. Exactly one source is selected."""

    input: str | None = None
    node: str | None = None
    output: str | None = None
    asset: RuntimeAssetRef | None = None
    literal: Any | None = None
    artifact: str | None = None

    @model_validator(mode="after")
    def one_source(self) -> ValueRef:
        choices = [
            self.input is not None,
            self.node is not None,
            self.asset is not None,
            self.literal is not None,
            self.artifact is not None,
        ]
        if sum(choices) != 1:
            raise ValueError(
                "value reference needs exactly one of input, node, asset, literal, artifact"
            )
        if self.node is not None and not self.output:
            raise ValueError("node reference requires output")
        if self.output is not None and self.node is None:
            raise ValueError("output may only accompany node")
        return self


class PortDescriptor(BaseModel):
    name: str
    type: PortType
    required: bool = True
    persistable: bool = False


class ToolDescriptor(BaseModel):
    id: str
    version: int = 1
    source: Literal["cooksprite", "comfy"]
    package_id: str | None = None
    title: str
    inputs: list[PortDescriptor] = Field(default_factory=list)
    outputs: list[PortDescriptor] = Field(default_factory=list)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    schema_hash: str = ""

    @model_validator(mode="after")
    def hash_schema(self) -> ToolDescriptor:
        if not self.schema_hash:
            body = self.model_dump(exclude={"schema_hash"}, mode="json")
            self.schema_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        return self


class ToolPackageManifest(BaseModel):
    """One cohesive, versioned set of CookSprite Tools and Comfy nodes."""

    id: str
    version: str
    license: str
    requirements: list[str] = Field(default_factory=list)
    tools: list[ToolDescriptor] = Field(default_factory=list)
    lowerings: dict[str, str] = Field(default_factory=dict)
    node_classes: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)
    recipes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_lowerings(self) -> ToolPackageManifest:
        tool_ids = {tool.id for tool in self.tools}
        if set(self.lowerings) != tool_ids:
            missing = sorted(tool_ids - set(self.lowerings))
            unknown = sorted(set(self.lowerings) - tool_ids)
            raise ValueError(f"package lowerings mismatch; missing={missing}, unknown={unknown}")
        if len(self.node_classes) != len(set(self.node_classes)):
            raise ValueError("package node classes must be unique")
        return self


class DefinitionRef(BaseModel):
    id: str
    revision: int = Field(ge=1)


class ToolNode(BaseModel):
    id: str
    tool: str
    inputs: dict[str, ValueRef] = Field(default_factory=dict)
    params: dict[str, ValueRef] = Field(default_factory=dict)


class WorkflowDefinition(BaseModel):
    id: str
    title: str
    runtime_id: str
    inputs: dict[str, PortType] = Field(default_factory=dict)
    nodes: list[ToolNode]
    outputs: dict[str, ValueRef]
    output_sources: dict[str, ValueRef] = Field(default_factory=dict)
    description: str = ""


class WorkflowRevision(WorkflowDefinition):
    revision: int
    runtime_snapshot: str
    model_config = ConfigDict(extra="forbid")


class WorkflowCall(BaseModel):
    id: str
    workflow_id: str
    candidates: list[int] = Field(min_length=1)
    inputs: dict[str, ValueRef] = Field(default_factory=dict)


class TaskDefinition(BaseModel):
    id: str
    title: str
    runtime_id: str
    inputs: dict[str, PortType] = Field(default_factory=dict)
    nodes: list[WorkflowCall]
    outputs: dict[str, ValueRef]
    description: str = ""


class TaskRevision(TaskDefinition):
    revision: int
    runtime_snapshot: str
    model_config = ConfigDict(extra="forbid")


class RunTarget(BaseModel):
    kind: Literal["workflow", "task"]
    id: str
    revision: int = Field(ge=1)


class RunCreate(BaseModel):
    target: RunTarget
    runtime_id: str
    inputs: dict[str, ValueRef] = Field(default_factory=dict)
    candidate_selection: dict[str, int] = Field(default_factory=dict)


class LocalizedText(BaseModel):
    name: str
    description: str = ""


class ArtifactRef(BaseModel):
    """The only public handle for persisted visual material.

    UI surfaces receive this object rather than an untyped media URL.  The URL
    remains an implementation detail of the reference and is never sufficient
    on its own to participate in a CookSprite workflow.
    """

    id: str
    sha256: str
    media_type: str
    size: int
    kind: str = "Image"
    url: str
    title: str = ""
    project_id: str | None = None
    favorite: bool = False
    trashed: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class ActionInput(BaseModel):
    type: PersistableType | list[PersistableType]
    required: bool = False
    max: int = Field(default=1, ge=1, le=64)


class ActionOption(BaseModel):
    id: str
    i18n: dict[str, LocalizedText]
    example_key: str | None = Field(default=None, exclude=True)
    example: ArtifactRef | None = None


class ActionControl(BaseModel):
    id: str
    type: Literal["select", "multi-select", "toggle", "range", "number", "text", "seed"]
    default: Any = None
    options: list[ActionOption] = Field(default_factory=list)
    # Compact numeric select support.  The API and Web projections can expand
    # this range without storing hundreds of repeated option objects.
    options_range: list[int] | None = Field(default=None, min_length=3, max_length=3)
    advanced: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    i18n: dict[str, LocalizedText]


class ModelOption(BaseModel):
    id: str
    label: str
    runtime_id: str
    family: str
    modes: list[str] = Field(default_factory=list)


class ActionDescriptor(BaseModel):
    id: str
    i18n: dict[str, LocalizedText]
    accepts: dict[str, ActionInput] = Field(default_factory=dict)
    produces: list[PersistableType]
    controls: list[ActionControl] = Field(default_factory=list)
    available: bool = False
    unavailable_reason: str | None = None
    models: list[ModelOption] = Field(default_factory=list)


class ActionRunCreate(BaseModel):
    project: str
    inputs: dict[str, str | list[str]] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)
    # Runtime Recipe slots that are not stable Action controls. This keeps
    # model/workflow-specific knobs out of the public Action registry while
    # giving CLI, agents, and future Web controls one typed path.
    params: dict[str, Any] = Field(default_factory=dict)


class ProjectExportCreate(BaseModel):
    allow_incomplete: bool = False


class ArtifactPatch(BaseModel):
    favorite: bool | None = None
    title: str | None = Field(default=None, max_length=160)


class FrameSequenceManifest(BaseModel):
    schema_id: Literal["cooksprite.frame-sequence/v1"] = Field(
        default="cooksprite.frame-sequence/v1", alias="schema", serialization_alias="schema"
    )
    action: ClipAction | None = None
    view: ViewId | None = None
    direction: Direction | None = None
    frames: list[str] = Field(min_length=1)


class FrameSequenceView(BaseModel):
    artifact: ArtifactRef
    sequence: FrameSequenceManifest
    frames: list[ArtifactRef]


class TrackSequenceCreate(BaseModel):
    """Materialize one curated SpriteDocument track as a reusable FrameSeq."""

    action: ClipAction
    view: ViewId
    direction: Direction


RuntimePhase = Literal[
    "queued",
    "starting",
    "loading_model",
    "sampling",
    "processing",
    "saving",
    "completed",
    "failed",
    "cancelled",
    "unknown",
]
RuntimeModelStatus = Literal["unknown", "loading", "ready", "failed"]
RuntimeNodeKind = Literal["model", "conditioning", "sampling", "processing", "artifact", "other"]
RuntimeNodeStatus = Literal["queued", "executing", "cached", "completed", "failed"]


class RuntimeErrorView(BaseModel):
    code: str
    message: str
    node: str | None = None
    type: str | None = None
    detail: str | None = None


class RuntimeNodeView(BaseModel):
    label: str
    kind: RuntimeNodeKind = "other"
    status: RuntimeNodeStatus = "executing"
    step: int | None = None
    total: int | None = None
    progress: float = Field(default=0, ge=0, le=1)


class RunRuntimeState(BaseModel):
    """The provider-neutral live execution state shown by Web and CLI clients."""

    event: str = "queued"
    phase: RuntimePhase = "queued"
    message: str = "queued"
    queue_remaining: int | None = Field(default=None, ge=0)
    current: RuntimeNodeView | None = None
    model_status: RuntimeModelStatus = "unknown"
    cached_nodes: int = Field(default=0, ge=0)
    completed_nodes: int = Field(default=0, ge=0)
    error: RuntimeErrorView | None = None
    updated_at: str = ""


class RunView(BaseModel):
    id: str
    status: Literal["queued", "running", "cancel_requested", "cancelled", "succeeded", "failed"]
    progress: float = 0
    message: str = ""
    action_id: str | None = None
    project_id: str | None = None
    runtime_id: str | None = None
    runtime_snapshot: str | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    runtime_state: RunRuntimeState = Field(default_factory=RunRuntimeState)
    error: dict[str, Any] | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


ProjectType = Literal["static", "character", "tileset"]


class ProjectCreate(BaseModel):
    name: str = ""
    type: ProjectType = "static"


class ProjectPatch(BaseModel):
    name: str | None = None
    type: ProjectType | None = None
    favorite: bool | None = None


class ProjectView(BaseModel):
    id: str
    name: str
    type: ProjectType
    directory: str | None = None
    favorite: bool = False
    published: bool = False
    cover_artifact_id: str | None = None
    created_at: str
    updated_at: str


class Pivot(BaseModel):
    x: float = 0.5
    y: float = 1.0


class FrameRef(BaseModel):
    id: str
    artifact: str
    normal: str | None = None
    duration_ms: int = Field(default=100, ge=16, le=60_000)
    offset_x: int = 0
    offset_y: int = 0
    source_artifact: str | None = None
    variant_of: str | None = None


class DirectionTrack(BaseModel):
    direction: Direction
    frames: list[FrameRef] = Field(default_factory=list)


class ViewTrack(BaseModel):
    id: ViewId
    enabled: bool = True
    tracks: list[DirectionTrack] = Field(default_factory=list)


class AnimationClip(BaseModel):
    id: str
    name: str
    action: ClipAction
    loop: Literal["none", "linear", "pingpong"] = "linear"
    views: list[ViewTrack] = Field(default_factory=list)


class StaticDocument(BaseModel):
    primary: str | None = None
    normal: str | None = None
    pivot: Pivot = Field(default_factory=Pivot)


class CharacterDocument(BaseModel):
    pivot: Pivot = Field(default_factory=Pivot)
    clips: list[AnimationClip] = Field(default_factory=list)


class TileSetDocument(BaseModel):
    source: str | None = None
    normal: str | None = None
    tile_width: int = Field(default=32, ge=1)
    tile_height: int = Field(default=32, ge=1)
    margin: int = Field(default=0, ge=0)
    spacing: int = Field(default=0, ge=0)
    exclude_empty: bool = True


class SpriteDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_id: Literal["cooksprite.sprite-document/v1"] = Field(
        default="cooksprite.sprite-document/v1", alias="schema", serialization_alias="schema"
    )
    type: ProjectType
    canvas: dict[str, int] = Field(default_factory=lambda: {"width": 64, "height": 64})
    static: StaticDocument | None = None
    character: CharacterDocument | None = None
    tileset: TileSetDocument | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def primary_shape(self) -> SpriteDocument:
        if self.type == "static" and self.static is None:
            self.static = StaticDocument()
        if self.type == "character" and self.character is None:
            self.character = CharacterDocument()
        if self.type == "tileset" and self.tileset is None:
            self.tileset = TileSetDocument()
        return self


class DocumentView(BaseModel):
    document: SpriteDocument
    revision: int
    etag: str


class GalleryItem(BaseModel):
    project: ProjectView
    cover: ArtifactRef | None = None
    published_at: str
