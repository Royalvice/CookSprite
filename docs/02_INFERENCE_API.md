# 02 · Action and API contract

The public base path is `/api/v1`. Ordinary Web, CLI, and agent clients should
start with Actions; Tool/Workflow/Task endpoints are a contributor surface.

## Action descriptor

`GET /actions` and `GET /actions/{id}` return the same minimal contract:

```json
{
  "id": "animation.generate",
  "i18n": {
    "zh-CN": {"name": "创作动画", "description": "从角色图生成动作候选帧"},
    "en": {"name": "Create animation", "description": "Generate motion candidates from a character image"}
  },
  "accepts": {"character": {"type": "Image", "required": false, "max": 1}},
  "produces": ["FrameSeq"],
  "controls": [],
  "available": true,
  "models": []
}
```

`type` can be one artifact kind or a list when an input deliberately accepts
multiple kinds. Users do not choose file formats: the UI only highlights inputs
compatible with the dragged Artifact. An option may contain an `example`
`ArtifactRef`; examples use the same storage, rendering, and drag contract as
user-created material. `available` requires the validated runtime snapshot,
required node schemas, and a current live probe.

Stable IDs in `cooksprite/actions.yaml`:

| Action | Purpose | Output |
|---|---|---|
| `image.generate` | text-to-image or one-reference image-to-image | `Image` |
| `animation.generate` | action/view/direction frame candidates | `FrameSeq` |
| `frame.redraw` | replacement variants while retaining the source | `ImageBatch` |
| `sheet.slice` | grid-based SpriteSheet extraction | `FrameSeq` |
| `video.sample` | GIF/video sampling | `FrameSeq` |
| `normal.generate` | same-size normal maps for image/sequence/sheet | `NormalMap` |
| `sprite.export` | validate and build one canonical package | `CookSpritePack` |

## Run an Action

```http
POST /api/v1/actions/animation.generate/runs
Content-Type: application/json

{
  "project": "prj_x",
  "inputs": {"character": "art_x"},
  "values": {
    "action": "walk",
    "view": "level",
    "direction": "s",
    "count": 8,
    "model": "rt_local:core-image-<checkpoint-hash>"
  }
}
```

The response is `202 RunView`. Follow it through
`GET /runs/{id}/events` (SSE) or `GET /runs/{id}`. Cancel and retry use
`POST /runs/{id}/cancel` and `POST /runs/{id}/retry`. `GET /queue` normalizes
CookSprite run state and also includes the current private Comfy queue snapshot.
No Comfy prompt ID or filesystem path is public.

`GET /health` reports `runtime: unconfigured | offline | ready`, `runtime_id`,
`checked_at`, and a readable live-probe error. A stored capability snapshot is
not proof that ComfyUI is currently online.

Errors use a stable detail object:

```json
{"detail":{"code":"artifact_type_mismatch","message":"...","slot":"source"}}
```

## Project and document

- `POST/GET /projects`, `GET/PATCH /projects/{id}`
- `GET/PUT /projects/{id}/document`
- `GET /projects/{id}/artifacts`
- `POST /projects/{id}/sequences` materializes one curated document track as a reusable `FrameSeq`
- `POST /projects/{id}/publish`
- `GET /gallery`

Document GET returns an `ETag`. PUT must send `If-Match`; stale edits receive
`409 document_conflict`. This is the only mutable animation authority.

## Artifacts

Upload bytes directly with `POST /artifacts?project_id=...&kind=...&media_type=...`.
List, download, trash, restore, and garbage-collect through `/artifacts`.
Animation, sheet slicing, and video sampling return one `FrameSeq` artifact:

```json
{"schema":"cooksprite.frame-sequence/v1","action":"walk","view":"level","direction":"s","frames":["art_frame_01","art_frame_02"]}
```

Each frame remains an independent `Image`. Expand the ordered manifest and its
typed frame references with `GET /artifacts/{id}/sequence`.
Application drag-and-drop carries only:

```json
{"artifact_id":"art_x","kind":"Image"}
```

The receiver fetches bytes/metadata through the API and validates the kind.

OpenAPI is available at `/api/v1/openapi.json`; contract tests assert these routes.

## Contributor execution

Contributor `POST /runs` accepts only an immutable Workflow or Task target, so
both `kind` and `revision` are explicit. A standalone Tool is descriptive and
cannot run without a Workflow that declares persistable outputs. Actions,
Workflows, and Tasks all compile to the same private execution plan and use the
same ComfyUI submission, cancellation, failure normalization, and typed artifact
storage path. Product clients should continue to use Actions.

## Runtime recipes

Doctor reads live node schemas and model folders from ComfyUI. A model is not
shown merely because a filename exists: CookSprite emits a selectable model
only when one small `Recipe` proves the complete node/model/input contract.
Core checkpoints are discovered as text-to-image, image-to-image, and
image-to-image sequence recipes. Contributor workflows can be registered with
`POST /runtimes/{id}/recipes`; their API-format graph, semantic slots, output,
actions, and input modes are validated against that runtime snapshot.

The private artifact bridge uses short-lived HMAC URLs scoped to one Run and
one declared input/output kind. `CS_LoadArtifact` and `CS_StoreArtifact` are the
only bridge nodes. This works unchanged when the CookSprite API and ComfyUI are
co-located on another Linux machine; the browser still calls only `/api/v1`.

## Prompt Packet policy

Clients submit stable option IDs, never hidden prompt fragments. For
`image.generate`, CookSprite currently compiles them as follows:

| Control | API value | CookSprite policy |
|---|---|---|
| Asset type | `character`, `weapon`, `prop`, `terrain`, `scene`, `vfx` | Adds a server-owned composition phrase such as complete silhouette, fully visible single object, or seamless orthographic tile. `terrain` also converts a new/static project to `tileset`. |
| Style | `pixel` | Adds pixel-art intent and applies `CS_Pixelize` after the selected Recipe output. A Recipe is not advertised as compatible unless that node exists. |
| Style | `smooth` | Adds clean game-concept-art intent and skips pixel post-processing. |

Both styles use the same current green-background/isolation policy. A Recipe
may replace the private graph, checkpoint, or input mechanism, but it receives
the same compiled text and must satisfy the same typed output/bridge contract.
Changing a runtime from localhost to another Linux device therefore changes
only the selected Runtime/Recipe; it does not change the Web, CLI, or Skill
request shape.

Project-shape changes are enforced at this same API boundary, not by Web-only
logic. `animation.generate` converts a non-character project to `character`;
`image.generate` with `category=terrain` converts a `static` project to
`tileset`. CLI and agent callers therefore receive the same document semantics.
