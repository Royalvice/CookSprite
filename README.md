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

## Mac control plane + H20 compute worker

The production two-machine topology is deliberately asymmetric:

```text
Mac: CookSprite Web + API + CLI + Project/Artifact Store
                         │ typed Run + scoped bridge URLs
                         ▼
H20: one Git clone + one managed ComfyUI runtime + nodes + models + GPU
                         │ CS_StoreArtifact
                         ▼
Mac: immutable SHA-256 artifacts and export packages
```

The API host owns every Project, SQLite record, and final artifact. The H20
host owns GPU compute only; it does not run the CookSprite product API or keep
a second artifact database. The two machines synchronize source only through
the shared Git remote (`git push` and `git pull --ff-only`), never through
directory copies or ad-hoc deployment scripts.

On H20, run the compute worker from the single Git clone:

```bash
cspr worker init --runtime-dir ../runtime --cuda-device 0
cspr worker install --runtime-dir ../runtime
cspr worker sync --runtime-dir ../runtime
cspr worker start --runtime-dir ../runtime
cspr worker doctor --runtime-dir ../runtime --json
```

`cspr worker` is the only supported H20 lifecycle interface. It records a
non-secret worker/runtime identity, requires a clean Git source, fast-forwards
from the configured remote branch, and refuses to alter a running or unknown
ComfyUI listener. Node deployment is staged and atomically renamed while the
worker is stopped; the loaded node pack exposes its safe source/lock/version
identity at `/cooksprite/runtime-info`, which worker start/doctor verify.
Model downloads remain explicit and are not part of worker startup.

On Mac, register H20 as a remote Runtime with an explicit callback URL that
H20 can reach. The callback is where the artifact bridge reads source inputs
and writes final output bytes; no H20 Comfy output directory is a product
artifact store.

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
cspr install
cspr start
```

`cspr start` starts the managed ComfyUI runtime, CookSprite API, and the Vue
development frontend. Open `http://127.0.0.1:5173`; the frontend proxies
CookSprite API requests to port `8000`. If any requested port is occupied,
CookSprite selects the next available port and prints the actual URLs.
All server entry points use the same data directory from
`~/.cooksprite/config.toml` (default `~/.cooksprite/data`). Passing
`--data-dir <path>` once makes that path the default for later `start` and
`serve` commands, preventing separate databases or artifact stores.

To browse the workbench without starting a local ComfyUI, start the API and
frontend only:

```bash
cspr start --no-comfy
```

The workbench remains visible without a runtime, but generation Actions are
disabled until a trusted ComfyUI is registered and checked:

```bash
cspr comfy probe-local --url http://127.0.0.1:8188
cspr comfy import --label "Local ComfyUI" --url http://127.0.0.1:8188 --location local
cspr comfy doctor --runtime <runtime-id-from-the-response>
```

The Settings page uses the same compact flow: enter one ComfyUI URL, then
choose local or remote. Runtime IDs are generated from the endpoint when
omitted, existing local directories are inferred from the process serving the
URL, and a remote URL is probe-only. If a remote host lacks CookSprite nodes,
install them on that host with `cspr comfy install-nodes <ComfyUI directory>`
and reconnect; CookSprite never writes into a remote filesystem from a URL.

If ComfyUI is not installed, the Settings page or this explicit command installs
only an isolated pinned runtime and the CookSprite node pack from the locked
ComfyUI dependency set. It never downloads a starter model. Select or register models through the connected ComfyUI;
existing ComfyUI installations and model directories are never modified by the installer. Contributors
may still run the API and Vite development server separately with `cspr serve`
and `npm run dev` when they need independent reload control. Use
`cspr start --no-frontend` when only the API and ComfyUI are needed.

Dependency updates are explicit and locked. After adding a Tool Package or
custom node, run `cspr dev sync`, then `cspr env lock` and
`cspr env sync --comfy-dir ~/.cooksprite/runtime`. A normal ComfyUI sync refuses
to use a stale lock.
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
