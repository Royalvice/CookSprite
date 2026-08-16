# CookSprite

CookSprite is a local-first, open-source AI Sprite studio. Its signature asset
is a `SpritePair`: diffuse art plus a same-size normal map that can be tested
under live light before export.

The product has one execution boundary:

```text
Vue Web UI / cspr CLI / agent Skill
                  │
                  ▼
       CookSprite /api/v1 Actions
                  │
                  ▼
               ComfyUI
                  │
                  ▼
    SHA-256 CookSprite artifacts
```

The browser never talks to ComfyUI and never contains inference logic. The API
validates a registered Action, compiles its private graph, tracks the run, and
stores only declared outputs. ComfyUI is the sole media execution runtime.

## Included in v0.1

- Vue 3 + TypeScript workbench with image, same-page animation curation, normal
  preview, library, queue, gallery, and export stages.
- Six bilingual inference Actions shared by Web, CLI, and agents:
  `image.generate`, `animation.generate`, `frame.redraw`, `sheet.slice`,
  `video.sample`, and `normal.generate`. Project export is a separate project
  operation because it does not execute media computation in ComfyUI.
- Eight real direction tracks, level/top-45 views, per-frame timing and offsets,
  original-preserving redraw variants, and undo/redo.
- Typed `FrameSeq` manifests with ordered reusable `Image` frames.
- Three.js normal-map preview with direct light dragging, a visible light gizmo,
  full normal-map view, and neutral/day/night CC0 HDR environments.
- One canonical `.cooksprite` ZIP package and a Godot 4.4+ importer.
- Local SQLite metadata and SHA-256 content-addressed blobs. No account,
  telemetry, remote gallery, or download without an explicit setup command/click.

## Run locally

Release wheels contain the built Vue frontend, CLI, API, node pack, and Agent
Skill. End users do not need Node.js:

```bash
python -m pip install cooksprite
cspr install --accept-model-license
cspr start
```

Open `http://127.0.0.1:8000`. The workbench remains visible without a runtime,
but generation Actions are disabled until a trusted ComfyUI is registered and
checked:

```bash
cspr comfy import --runtime local --label "Local ComfyUI" --url http://127.0.0.1:8188
cspr comfy doctor --runtime local
```

If ComfyUI is not installed, the Settings page or this explicit command installs
an isolated pinned runtime, the CookSprite node pack, and a hash-verified default
SD 1.5 checkpoint. `cspr install` first displays the model identity, source,
license, size, and destination and requires `--accept-model-license`.

Pass `--no-models` when an existing ComfyUI already has compatible models or
when only installing the runtime/node pack. Existing ComfyUI installations and
model directories are never modified by the managed installer. Contributors
may still run the API and Vite development server separately.
Every compatible checkpoint already visible to that ComfyUI becomes a selectable
text/image Recipe. Existing image-to-video or text-to-video API workflows stay
in ComfyUI and can be registered as a small Recipe adapter; their declared
`i2v`/`t2v` modes appear in the same animation model selector. On NVIDIA Linux,
the managed installer pins a CUDA 12.6 PyTorch build instead of accepting an
incompatible newest-CUDA wheel from a partial package mirror.

Start with [architecture](docs/01_ARCHITECTURE.md), the
[Action/API contract](docs/02_INFERENCE_API.md), and the
[authoring workflow](docs/03_WORKFLOW.md). Contributor-level Comfy details live
in [runtime integration](docs/04_COMFYUI_EXPORT.md); the existing
[capability map](docs/05_COMFYUI_CAPABILITY_MAP.md) is preserved separately.
