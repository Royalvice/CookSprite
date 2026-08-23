# 06 · Dependency and environment contract

CookSprite owns exactly two isolated Python environments. They are never merged
for convenience.

```text
Product or source development clone
  source/.venv
  CookSprite API · CLI · compiler · development checks

Managed ComfyUI worker runtime
  ../worker-runtime/.venv
  ComfyUI · PyTorch/accelerator · Custom Node dependencies
```

The API/CLI environment must not import Torch, ComfyUI, ONNX runtimes, rembg,
or media-compute libraries. The ComfyUI environment must not depend on the
CookSprite API process. Models, outputs, and caches are data, never Python
dependencies.

## Lock sources

| Environment | Human input | Locked output | Installer / verifier |
| --- | --- | --- | --- |
| API/CLI | `pyproject.toml` | `uv.lock` | `uv sync --frozen` |
| Managed ComfyUI | `cooksprite/comfy/requirements.in` plus generated node requirements | `cooksprite/comfy/requirements.lock` | `cspr comfy worker install` / `cspr comfy worker sync` |

Tool Package manifests are the only source for Custom Node dependencies.
`cooksprite/nodes/requirements.txt` and the ComfyUI lock are generated outputs,
not hand-maintained lists. Models never appear in Git locks and are never
downloaded as a worker startup side effect.

## Change a Custom Node or Tool Package

1. Add a node-only dependency to the relevant Tool Package manifest, never to
   API `pyproject.toml` unless the API itself needs it.
2. Implement the typed node and Tool/Recipe contract.
3. In the source clone run:

   ```bash
   cspr dev package sync
   cspr dev package lock
   cspr dev check
   ```

4. Commit and push from any authorized clone. Every other clone obtains the
   change only through Git.
5. On the managed ComfyUI host, with no running managed listener, run:

   ```bash
   cspr comfy worker sync --runtime-dir ../worker-runtime
   cspr comfy worker doctor --runtime-dir ../worker-runtime --json
   ```

`comfy worker install` and `comfy worker sync` fast-forward the configured source branch,
pin its credential-redacted origin, and reject a local-only `HEAD`. `sync`
always deploys the locked environment and node pack locally through an atomic
node-pack swap. It does not copy source across machines and it does not create
a product data directory.

## External versus managed Runtime

An arbitrary local or remote ComfyUI can be read through API probe/doctor and
may be registered as an external Runtime. Its Python environment is owned by
that host; CookSprite will not mutate it remotely.

A `cspr comfy worker` Runtime is explicitly managed on its own host. Any CookSprite
API may register it as `worker_managed`—local or remote—and check the safe
runtime identity during doctor. Browser/API calls never invoke another host's
package installer or process manager.
