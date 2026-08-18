# 04 · ComfyUI runtime and package import

## Runtime

Register an already running trusted ComfyUI, then doctor it:

```bash
cspr comfy import --runtime local --label "Local ComfyUI" --url http://127.0.0.1:8188
cspr comfy doctor --runtime local
```

Doctor reads `/object_info`, `/system_stats`, `/features`, and live model
folders, stores a snapshot hash, and discovers `comfy.*` Tool schemas plus
compatible Recipes. CookSprite Actions compile to a private Comfy
prompt. Completion maps from Comfy history, queue state maps from `/queue`, and
cancellation maps to queue deletion plus `/interrupt`. Public clients receive
only CookSprite Run IDs and normalized state through HTTP/SSE.

`CS_LoadArtifact` reads immutable inputs through short-lived, run-scoped signed
URLs. Only `CS_StoreArtifact` outputs can use the corresponding signed upload
URL and be attached to the Run. The runtime's upload/output folders are never
treated as public artifact storage.

There is no product Demo/Fake runtime, demo node, deterministic image fallback,
or browser-side inference path. Automated unit tests may replace the HTTP
transport with a protocol double, but acceptance uses a real pinned ComfyUI and
real model execution.

`cspr comfy install <directory>` installs the pinned official ComfyUI revision
and this versioned node pack into an isolated environment. It never downloads a
default model. Model files and model paths are selected in the connected
ComfyUI; attaching to an existing ComfyUI never copies or mutates its models.
Install or update the locked node pack with
`cspr comfy sync <managed-runtime>` and restart ComfyUI. Use
`cspr comfy install-nodes <comfy-directory> --no-deps` only when attaching to a
user-owned external ComfyUI whose Python environment CookSprite must not
modify.

Core checkpoints are discovered directly and become `t2i`, `i2i`, and
image-sequence choices without copying model files. Model families whose graph
cannot be inferred safely (for example an existing image-to-video or
text-to-video stack) are registered with `cspr comfy recipe --runtime <id>
recipe.json`. The adapter contains only the existing Comfy API-format workflow,
semantic slot addresses, one typed output, and modes such as `i2v` or `t2v`.
CookSprite revalidates every node and model on each doctor pass; an incompatible
Recipe becomes unavailable rather than falling back to a different graph.

The managed NVIDIA Linux environment pins a CUDA 12.6 PyTorch wheel, which is
compatible with the common 535-series datacenter driver used in remote GPU
hosts. Package installation first honors a configured mirror and retries on
official PyPI only when the mirror is incomplete.

For a remote worker, run CookSprite API beside ComfyUI and set the runtime
callback URL to that API. Frontends then point at the same `/api/v1` contract;
Comfy hostnames, graph JSON, model paths, and prompt IDs remain private.

## `.cooksprite`

The only canonical delivery is a ZIP with MIME
`application/vnd.cooksprite+zip`:

```text
manifest.json
provenance.json
frames/<sha256>.png
normals/<sha256>.png
```

The manifest stores project type, canvas, pivot, static or tileset fields, and
character clips/views/direction tracks with frame order, `duration_ms`, offsets,
normal links, loop mode, and integrity warnings. Media is copied byte-for-byte;
packaging performs no image transform.

The official Godot 4.4+ importer is in `godot/addons/cooksprite_importer`.
It always imports a `PackedScene`: `Sprite2D`, `AnimatedSprite2D`, or
`TileMapLayer` according to project type, pairing diffuse/normal data with
`CanvasTexture` resources. See `godot/README.md`.
