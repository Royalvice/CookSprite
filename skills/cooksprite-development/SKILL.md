---
name: cooksprite-development
description: Develop CookSprite capabilities through typed Actions, Tasks, Workflows, Tools, ComfyUI nodes, API, CLI, and Vue projections. Use when adding a model, Action, Task, Workflow, Tool package, custom node, runtime binding, or cross-client feature.
---

# CookSprite Development SOP

Use this skill for changes to the CookSprite Python/API/CLI codebase, the Vue
frontend, or the CookSprite custom-node pack.

## Non-negotiable boundary

```text
Action → Task → Workflow → Tool → ComfyUI node → Artifact
```

- CookSprite API validates, versions, compiles, schedules, and stores metadata.
- ComfyUI is the only execution plane for inference and media computation.
- Web, CLI, and agents call CookSprite API; they never call ComfyUI directly.
- `CS_LoadArtifact` and `CS_StoreArtifact` are the artifact bridge.
- Tool ports use CookSprite types (`Image`, `FrameSeq`, `Text`, `Number`, etc.).
- Raw Comfy graphs, node dictionaries, paths, and temporary URLs stay inside adapters.
- `.local/` is reference-only. Never import, package, or copy it at runtime.
- Fake Runtime fixtures may test protocol code only; real capability claims require
  a real ComfyUI API run.

## Dependency boundary

Maintain exactly two Python environments: the repository `.venv` for the
CookSprite API/CLI/compiler and the managed ComfyUI `.venv` for ComfyUI,
PyTorch, and Tool Package node dependencies. Keep them disjoint; never add a
node-only dependency to `pyproject.toml`, and never import CookSprite API code
from a ComfyUI node.

The root `uv.lock` locks CookSprite. `cooksprite/comfy/requirements.in` plus
the generated `cooksprite/nodes/requirements.txt` lock the managed ComfyUI
environment into `cooksprite/comfy/requirements.lock`. After changing a Tool
Package or custom node, run in the source clone:

```bash
cspr dev package sync
cspr dev package lock
cspr dev check
```

Do not install an unpinned package with bare `pip` into either environment.
Commit/push through the authoritative Git remote, then run
`cspr comfy worker sync --runtime-dir ../worker-runtime` on the managed ComfyUI host
while its runtime is stopped. Worker synchronization pins its configured remote, requires
its `HEAD` to match that pull's `FETCH_HEAD`, and always refreshes the locked
environment before atomically replacing the node pack. Remote or user-owned
ComfyUI environments are never synchronized from a CookSprite API.

## SOP

### 1. Onboard a model

1. Run Runtime Doctor and inspect ComfyUI `object_info`, model folders, loader
   choices, required companion files, and actual output ports.
2. Select or create a model-independent Workflow with explicit model slots.
3. Prove the smallest API-format graph with no ComfyUI browser open.
4. Record the model family, loader contract, revision, license, and test result.
5. Do not create an Action merely because a new model exists. A model becomes a
   candidate only when a Workflow's typed input/output contract accepts it.

### 2. Add an Action

Edit `cooksprite/actions.yaml` with a stable ID, bilingual text, typed inputs,
typed outputs, and only user-facing controls. Keep ComfyUI names, model paths,
and hidden prompt text out of the public Action.

Then run:

```bash
cspr dev package sync
cspr dev check
```

The same registry must drive API discovery, CLI help, Vue controls/types, and
the Agent reference. An Action is incomplete until its available/unavailable
state and contract tests exist.

### 3. Build a Task

Bind the Action's artifact inputs and controls to a versioned Task. The Task
chooses a compatible Workflow, binds the current Runtime snapshot, declares
candidate/output behavior, and keeps all values typed. The API may compile
deterministic control-plane text such as Prompt Packets, but never performs
image, video, mask, or other media computation.

### 4. Build a Workflow

Use a flat typed Tool DAG. Declare every input, model slot, output, and
persistable port. A Workflow must compile to an independent ComfyUI API graph;
it must not nest another Workflow or leak raw Comfy values across its boundary.

For a runtime-provided API-format graph, register one compact Recipe instead
of adding an Action/API branch. Declare its semantic `slots`, `slot_types`, and
typed `output`; CookSprite's shared Recipe Assembler injects the API-compiled final prompt,
seals the graph, and adds the declared post-processing policy. Extra scalar
workflow inputs are passed as Action request `params` and are rejected unless
the selected Workflow declares them.

### 5. Build a Tool package and node

Put related Tools in one versioned package. Add the Tool descriptor, lowering,
node class, dependency declaration, node input/output check, and headless test.
The node must work without a ComfyUI browser extension. Reuse an existing Tool
and lowering when the semantic contract is the same.

### 6. Bind Runtime defaults

Store defaults per Runtime, not globally:

```json
{"action_id": {"workflow_id": "...", "model_id": "..."}}
```

Preserve a valid user selection. Otherwise use CookSprite's product priority,
then a stable compatible fallback; if none is valid, report the Action as
unavailable. Never silently switch to an incompatible Workflow.

### 7. Project and verify

Update API, CLI, Vue, generated Agent references, and tests in the same change.
For a media Tool, verify deterministic output, declared types,
provenance, real ComfyUI execution, and artifact persistence on the API host.

Prompt packets are control-plane data, not media computation:

- compile them once in CookSprite API before Workflow lowering;
- pass only the final prompt into declared Workflow text slots;
- keep `CS_CompilePromptPacket` only as a legacy saved-graph compatibility node;
- do not expose, compile, template, or persist negative prompts;
- keep model-specific text encoding inside each Workflow.

Required checks:

```bash
cspr dev package sync
cspr dev check
python -m pytest -q
npm run build
```

Finish with a real managed ComfyUI API acceptance run. For a remote Runtime,
this is an API → ComfyUI → API Artifact Bridge round trip.
Do not claim readiness from a queued request, a fake response, or a successful
frontend build alone.
