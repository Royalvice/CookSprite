# 04 · ComfyUI runtime, Recipe import, and delivery

## Runtime ownership

CookSprite does not turn a connected URL into a managed host. The API/data
directory owns SQLite, Projects, Artifacts, and `.cooksprite` exports. The
selected ComfyUI Runtime owns only execution, models, and runtime caches.

Register an already running remote Runtime, then Doctor it:

```bash
cspr --api https://api.example.test \
  comfy connect import \
  --label Remote \
  --url http://runtime.example.test:8288 \
  --location remote \
  --callback-url https://api.example.test/api/v1 \
  --worker-managed

cspr --api https://api.example.test comfy inspect doctor --runtime <runtime-id>
```

Doctor reads `/object_info`, `/system_stats`, model folders, and the optional
worker identity endpoint. It stores a capability snapshot and discovers
compatible Recipes. It never starts/restarts ComfyUI, installs a package,
downloads a model, or finds a remote directory.

For a managed ComfyUI Runtime, `cspr comfy worker` is the installation and lifecycle
interface. See [07_MANAGED_WORKER.md](07_MANAGED_WORKER.md).

## Recipe import

Core checkpoints can be discovered directly. An image/video/custom graph that
cannot be inferred safely is registered as a compact Recipe:

```bash
cspr --api https://api.example.test \
  comfy inspect recipe --runtime <runtime-id> recipe.json
```

A Recipe contains an existing ComfyUI API-format workflow, semantic slot
addresses/types, supported stable Action IDs/modes, and exactly one typed
output. It is validated against the current doctor snapshot. CookSprite's
shared assembler injects the API-owned prompt and Artifact bridge nodes; it
does not create a product-specific API branch or expose the raw graph to Web,
CLI, or agents.

If a runtime/node/model snapshot changes, doctor must validate again. Any
worker-managed identity change requires an explicit re-registration before
doctor can accept it.

## Artifact bridge

`CS_LoadArtifact` reads declared immutable input bytes through a short-lived,
run-scoped signed bridge URL. `CS_StoreArtifact` uploads declared typed output
bytes to the API that owns the Run. These are the only supported bridge nodes.

```text
API Blob Store → CS_LoadArtifact → selected Comfy graph → CS_StoreArtifact → API Blob Store
```

ComfyUI upload/output/view/temp folders are not public artifact storage. The
API does not expose paths or grant a browser direct ComfyUI access.

## `.cooksprite` delivery

The canonical delivery is a ZIP with MIME
`application/vnd.cooksprite+zip`:

```text
manifest.json
provenance.json
frames/<sha256>.png
normals/<sha256>.png
```

The manifest records project type, canvas, pivot, tracks, frame order,
`duration_ms`, offsets, normal links, loop mode, and integrity warnings. Media
is copied byte-for-byte in bounded chunks from the Blob Store into one
same-filesystem staging ZIP, then atomically promoted as the export Artifact.
Packaging never performs image transforms or creates a new Artifact mirror.

The Godot 4.4+ importer is in `godot/addons/cooksprite_importer`. It maps the
package to `Sprite2D`, `AnimatedSprite2D`, or `TileMapLayer` and pairs diffuse
and normal maps with `CanvasTexture` resources.
