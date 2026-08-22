# H20 Compute Worker

CookSprite has one product control plane and one optional compute worker role.
In the standard Mac + H20 deployment, Mac owns the API, projects, artifacts,
and exports. H20 owns ComfyUI, models, Custom Nodes, and GPU execution only.

## Source and runtime layout

```text
<worker-root>/
├── source/                 # one normal CookSprite Git clone
│   └── .git/
├── runtime/                # non-Git managed ComfyUI runtime
│   ├── worker.json
│   ├── cooksprite-runtime.json
│   ├── ComfyUI/
│   └── .venv/
└── models/                 # optional local model data, never Git source
```

Only Git `push`/`pull` synchronizes source between Mac and H20. Installing
files from `source/cooksprite/nodes` into `runtime/ComfyUI/custom_nodes` is an
intentional local deployment step performed by `cspr worker sync`; it is not a
cross-machine copy.

## Lifecycle

Run from `source/`:

```bash
cspr worker init --runtime-dir ../runtime --cuda-device 0
cspr worker install --runtime-dir ../runtime
cspr worker sync --runtime-dir ../runtime
cspr worker start --runtime-dir ../runtime
cspr worker status --runtime-dir ../runtime --json
cspr worker doctor --runtime-dir ../runtime --json
```

`sync` performs `git pull --ff-only origin <configured-branch>` before it
synchronizes the locked local ComfyUI environment and node pack. It rejects
dirty source, a running worker, an occupied listener, and a runtime outside the
configured source/runtime pair. `stop` only acts on the PID recorded for this
runtime and refuses to touch an unknown ComfyUI process.

Use `restart` only after the ComfyUI queue is empty. Models are explicit data:
they are never downloaded at worker startup and never committed to Git.

## Mac runtime registration

The Mac API registers H20 as a remote Runtime. Its `callback_url` must be an
explicit URL reachable from H20. CookSprite compiles a private ComfyUI graph on
Mac, H20 loads only run-scoped bridge inputs, and `CS_StoreArtifact` uploads
declared outputs back to Mac. The API and H20 must reject a configuration that
would silently make `127.0.0.1` on H20 the artifact authority.

The worker does not expose a second CookSprite API. If remote invocation is
needed, SSH is only a transport for the established `cspr worker` CLI.
