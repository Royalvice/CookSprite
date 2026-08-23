# 02 · Action and API contract

The public base path is `/api/v1`. Web, CLI, and agents use stable Actions and
typed Artifact IDs. They do not create generic graph definitions or call
ComfyUI directly.

## Actions and Runs

`GET /actions` and `GET /actions/{id}` expose the stable product contract:

```json
{
  "id": "animation.generate",
  "accepts": {"character": {"type": "Image", "required": false, "max": 1}},
  "produces": ["FrameSeq"],
  "controls": [],
  "available": true,
  "models": []
}
```

`available` requires a live Runtime plus a validated Recipe; a model filename
alone is never enough. A client starts an Action with:

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
    "model": "rt_remote:core-image-<checkpoint-hash>"
  }
}
```

The response is `202 RunView`. Read it through `GET /runs/{id}` or the SSE
stream `GET /runs/{id}/events`; cancel/retry use `POST /runs/{id}/cancel` and
`POST /runs/{id}/retry`. `GET /queue` projects CookSprite Run state plus the
current private Comfy queue status. No Comfy prompt ID, filesystem path, raw
graph, or temporary URL is public.

Internally, each Action lowers through private versioned Task/Workflow/Tool
structures. Public `/tools`, `/workflows`, `/tasks`, and generic `POST /runs`
do not exist.

## Projects and Artifacts

- `POST/GET /projects`, `GET/PATCH /projects/{id}`
- `GET/PUT /projects/{id}/document` with ETag / `If-Match`
- `GET /projects/{id}/artifacts`
- `POST /projects/{id}/sequences`, `/publish`, and `/exports`
- `GET /gallery`

Project state lives in SQLite. It has no automatic project directory or copied
artifact tree.

Upload raw bytes with:

```text
POST /artifacts?project_id=<id>&kind=<kind>&media_type=<mime>
```

The server streams the request into a same-filesystem staging blob, hashes it
incrementally, enforces the configured upload limit, fsyncs it, and atomically
promotes it to `<data-dir>/artifacts/<sha256>`. Repeated content returns the
same Artifact reference. Artifact content and bridge downloads are streamed
from disk. `GET /artifacts/{id}/sequence` expands a typed `FrameSeq` manifest.

## Runtime control plane

Runtime operations only register and inspect existing ComfyUI endpoints:

```text
POST /comfyui/probe                 body: {"base_url": "http(s)://..."}
POST /runtimes                      explicit Runtime registration
POST /runtimes/{id}/doctor          snapshot and Recipe validation
POST /runtimes/{id}/select
GET  /runtimes/{id}/capabilities
GET/PUT /runtimes/{id}/defaults
POST /runtimes/{id}/recipes         controlled Recipe import
```

Probe always needs an explicit URL. It never discovers a directory, starts a
process, installs a node, downloads a model, or assumes loopback means a
remote endpoint.

A remote registration requires an explicit `callback_url` that the ComfyUI
host can reach:

```json
{
  "label": "Remote",
  "base_url": "http://runtime.example.test:8288",
  "location": "remote",
  "transport": "http",
  "callback_url": "https://api.example.test/api/v1",
  "worker_managed": true
}
```

`worker_managed` is valid for either a local or remote Runtime. Its first
successful doctor records the worker identity; later identity drift returns
HTTP 409 `worker_runtime_incompatible` until explicit re-registration. This
prevents a Git/node/lock change from silently using a stale Recipe snapshot.

The API has no endpoint to start/restart ComfyUI, install nodes, download model
bundles, manage a Runtime host filesystem, or run a second Worker service.
Managed operations are local `cspr comfy worker` commands on the ComfyUI host.

## Artifact bridge

The only compute-to-control-plane media channel is:

```text
CS_LoadArtifact  → signed GET /bridge/artifacts/{artifact_id}
CS_StoreArtifact → signed POST /bridge/runs/{run_id}/artifacts
```

Signatures are short-lived and scoped to one Run, declared input/output, and
typed kind. The API accepts only declared bridge output kinds and associates the
stored Artifact with the Run/Project in the active API. ComfyUI output/view/temp folders
cannot become an Artifact URL or a Project asset by themselves.

## Controlled recipes

Doctor discovers node schemas and model folders. A Recipe is the only advanced
graph-facing registration surface: it imports an existing API-format workflow,
declares semantic slots, supported stable Actions/modes, and one typed output.
The shared Recipe Assembler validates it against the doctor snapshot and builds
the private lowering. It is not a public free-form DAG API.

## Errors

Errors use a stable detail object:

```json
{"detail":{"code":"artifact_type_mismatch","message":"...","slot":"source"}}
```

Relevant control-plane errors include `remote_callback_missing`,
`worker_runtime_incompatible`, `runtime_not_doctored`, `comfy_unavailable`,
and `artifact_too_large`.
