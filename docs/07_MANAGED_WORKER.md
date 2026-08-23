# Managed ComfyUI Worker

`cspr comfy worker` manages one ComfyUI Runtime on the host where the command runs.
It does not restrict what else that CookSprite clone can run.

## Layout

```text
<worker-root>/
├── source/                 # ordinary CookSprite Git clone
│   └── .git/
├── worker-runtime/         # non-Git managed ComfyUI Runtime
│   ├── worker.json
│   ├── cooksprite-runtime.json
│   ├── ComfyUI/
│   │   └── custom_nodes/cooksprite/
│   │       ├── VERSION
│   │       └── RUNTIME.json
│   └── .venv/
└── models/                 # optional local model data, never Git source
```

Source clones synchronize only through the shared Git remote. Installing the
node pack from a clone into its sibling Runtime is a local deployment step, not
a source synchronization channel.

## Lifecycle

Run from `source/`:

```bash
cspr comfy worker init --runtime-dir ../worker-runtime --port 8288 --device auto
cspr comfy worker install --runtime-dir ../worker-runtime
cspr comfy worker sync --runtime-dir ../worker-runtime
cspr comfy worker start --runtime-dir ../worker-runtime
cspr comfy worker status --runtime-dir ../worker-runtime --json
cspr comfy worker doctor --runtime-dir ../worker-runtime --json
```

`install` and `sync` perform
`git pull --ff-only origin <configured-branch>` against the origin pinned at
initialization and require `HEAD == FETCH_HEAD`. They reject dirty source,
local-only commits, a changed remote, a running listener, and an invalid
source/Runtime pair. Dependency synchronization and node-pack activation are
one stopped-only deployment transaction.

The complete node tree is assembled in staging and atomically renamed into
place. `RUNTIME.json` exposes only source branch/revision, node-pack version,
dependency-lock hash, and ComfyUI URL at `GET /cooksprite/runtime-info`.
Worker start and Doctor reject identity mismatch.

`stop` acts only when PID, checkout path, and live Runtime identity agree, and
refuses unknown processes. `restart` requires an empty queue. Device selection
supports `auto`, `cpu`, `cuda[:N]`, `rocm[:N]`, and `mps`. Shared mode does not
require a vendor CLI. Optional CUDA exclusivity uses the NVIDIA inspector;
future backends register another inspector without changing lifecycle code.

Models are explicit data. They are never downloaded during worker startup and
never committed to Git.

## API connection

The worker lifecycle never starts a CookSprite API or creates a product data
directory. The same source clone may run an API independently.

A local Runtime may use the API's local callback. A remote Runtime must be
registered with an explicit API `/api/v1` callback URL reachable from ComfyUI.
`CS_LoadArtifact` reads only signed Run inputs; `CS_StoreArtifact` returns only
declared typed outputs to the API-owned Blob Store.

`cspr comfy worker init` accepts only a new empty Runtime directory. It never adopts
a legacy or user-owned ComfyUI directory. The default is `../worker-runtime`
with listener `127.0.0.1:8288`.
