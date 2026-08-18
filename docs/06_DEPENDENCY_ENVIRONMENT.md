# 06 · Dependency and Environment Contract

CookSprite has two and only two owned Python environments:

```text
repo/.venv
  CookSprite API · CLI · compiler · development checks

~/.cooksprite/runtime/.venv
  ComfyUI · PyTorch/accelerator · CookSprite custom nodes · Tool dependencies
```

The browser and models are not Python environments. The browser is built by
the Web workspace; model files are explicit user data and are never installed
as a startup side effect.

## Lock sources

| Environment | Human input | Locked output | Synchronizer |
| --- | --- | --- | --- |
| CookSprite | `pyproject.toml` | `uv.lock` | `uv sync --frozen` |
| ComfyUI | `cooksprite/comfy/requirements.in` plus generated node requirements | `cooksprite/comfy/requirements.lock` | `cspr comfy sync` |

`requirements.in` pins the ComfyUI ref's direct runtime requirements and
includes the generated requirements of every registered Tool Package. The
lock file pins the complete transitive set. It intentionally excludes optional
ComfyUI extras and all models until a capability proves they are required.

## Adding a custom node

1. Add the Tool Package manifest requirement, if the node needs a Python
   package. Do not add it to the CookSprite API `pyproject.toml`.
2. Implement the node and update its Tool manifest/contract.
3. Run `cspr dev sync` to generate the node requirement input and registry
   projections.
4. Run `cspr comfy lock` to resolve the new complete ComfyUI lock.
5. Run `cspr comfy sync ~/.cooksprite/runtime` to install the lock and copy the
   current CookSprite node pack.
6. Run `cspr dev check` and a real ComfyUI API conformance test.

For a one-step local developer update, use:

```bash
cspr env lock
cspr env sync --comfy-dir ~/.cooksprite/runtime
```

`cspr env sync --update-lock` is available when a deliberate dependency
refresh is wanted. A normal sync refuses a stale lock instead of silently
upgrading packages.

Remote or user-owned ComfyUI runtimes do not share the local `.venv`; they
must be registered and inspected through the same CookSprite API, and their
Python environment remains owned by the remote host.
