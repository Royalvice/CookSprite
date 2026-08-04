# AGENTS.md — CookSprite

CookSprite is a general, open-source tool for producing 2D **sprites** with AI.
`CLAUDE.md` is a compatibility symlink to this file; keep one agent contract.

## First Rule

**Lowest usage burden wins.** Design for three audiences, in this priority:

1. **Agents** call it — fast, scriptable, one obvious way to do a thing.
2. **Humans** use the web toolbox — pick a task, run, preview, light-edit.
   Never author node graphs.
3. **Contributors** extend it — add a model adapter or a workflow without
   touching the other layers.

**Fully general. Zero downstream-game assumptions.** CookSprite must never
hard-code a specific game's camera angle, canvas size, direction naming, actor
pixel height, or content identity. Direction counts, canvas dimensions, frame
rates, and naming are user-supplied config, never baked constants.

**Simply First.** One default route per capability. One owner per concern
across the four layers. Delete an unselected route rather than keep it "just in
case." Missing data or a failed inference surfaces an explicit error — never a
silent fallback or a second hidden implementation.

## Product Lock

- CookSprite produces **sprites**: directional clips, sprite sheets, and — the
  signature unit — **sprite pairs** (a diffuse frame plus a same-size normal
  map for dynamic lighting).
- It is an **AI-generation tool**, not a hand-drawing pixel editor. The web
  frontend previews, selects frames, does pixel-perfect cleanup, and can
  regenerate a single frame. Detailed hand-pixeling stays in dedicated editors
  (Aseprite) via standard PNG sprite-sheet round-trips.
- Reference frontends for the human editor: spritecook.ai and pixellab.ai.
- The frontend includes a **three.js interactive preview**: load a sprite +
  its normal map, drag a dynamic light source, watch normal-mapped shading in
  real time.


## Architecture — Four ABI-Decoupled Layers

```text
Model + Inference  ──[ /infer HTTP API ]──►  atomic capability, local OR remote (docker)
Workflow           ──[ typed tool graph ]──►  one minimal self-contained task
Frontend           ──[ triggers workflows ]──►  Web GUI (humans) + CLI/skill (agents)
```

Each layer talks to the next only through a stable contract (its ABI). You can
swap a model, add a workflow, or replace the web UI without the other layers
knowing. ComfyUI is **not** a dependency — see the ComfyUI section.


## The Three Abstractions (the core mental model)

Two composition layers, same DAG shape. A **task** is a DAG of workflow-nodes;
a **workflow** is a DAG of tool-nodes. Neither nests into itself.

```text
Task  — a user-facing goal, e.g. "reference image → a full sprite animation
  │      pack". A DAG of workflow-nodes; the unit a frontend triggers. A simple
  │      goal (text→image) mounts ONE workflow; a big one mounts MANY.
  └── node — one slot in the task. Runs exactly one Workflow, chosen from
        │     `candidates` ([0] is the default; callers may pick another).
        │     Nodes wire one workflow's output into another's declared input.
        └── Workflow — one minimal end-to-end route; a FLAT graph of tools
              │        (never contains another workflow). May declare external
              │        `inputs` a task feeds via `$in.<name>`. Reusable across
              │        tasks and across nodes.
              └── Tool — the smallest unit that satisfies one minimal function,
                    with a typed input/output port (ComfyUI-like). Every tool
                    has a `kind`:
                    ├── kind="inference"     — calls /infer (text2img, img2img,
                    │     img2vid, upscale …). Names a model op that MANY
                    │     model_ids can serve behind /infer.
                    └── kind="deterministic" — a local, model-free step
                          (pixelize, crop, center-align, normal-estimate …).
```

Key decoupling: a **task** is independent of *which model* or *which route*
fulfills each step. A workflow is task-independent and reusable; the model
choice is a param on each inference tool.

**Candidate selection:** each task node lists one or more workflow candidates;
`candidates[0]` is the default. Humans and agents may pick another candidate by
node. No hidden auto-ranking.

**Typed I/O:** tools declare typed inputs/outputs (Image, ImageBatch,
SpriteSheet, FrameSeq, Mask, NormalMap, Palette, …) so they compose safely.


## Repository Shape & Branch State

```text
backend/    Python — FastAPI /infer server, model adapters, model-op routing
workflow/   Python — workflow schema, tool library, runner, ComfyUI export
cli/        Python — agent-facing CLI + skill
web/        TypeScript — human toolbox, sprite preview, three.js light preview
docs/       open-source-facing documentation (public)
.agent-os/  dev-state docs (NEVER in git; ignored)
```

- `.agent-os/` is branch/local development state, never in git, never in the
  public release tree.
- `docs/` is the open-source-facing documentation and IS committed.
- `AGENTS.md` is the one contract; `CLAUDE.md` is a symlink to it.
- No model weights, secrets, `.env`, generated outputs, or scratch in git.


## ComfyUI Relationship — Export Bridge Only

ComfyUI is the most mature open-source generation ecosystem, so many users live
there. CookSprite does **not** depend on it and does **not** build on its
`custom_nodes`. Instead:

- The backend is a self-owned lightweight inference API (`/infer`).
- A **translator** exports a CookSprite workflow → ComfyUI **API-format JSON**
  (`{node_id: {class_type, inputs}}`, links as `["node_id", output_index]`).
- This lets ComfyUI users run our workflows in their environment without us
  carrying ComfyUI's weight.

Rationale: ComfyUI's own architecture confirms inference-as-HTTP-API is right;
its API-format workflow is nearly 1:1 with our "workflow = minimal function"
concept, so a translator is cheap and keeps us light.


## Inference Contract (Model-Layer ABI)

Minimal REST, async job model (generation — especially video — is long):

```text
POST /infer { "op", "model_id", "inputs", "params" } → { "job_id" }
GET  /jobs/{job_id}            → status + progress
GET  /jobs/{job_id}/result     → { "outputs", "meta" }
```

- Engine: **vLLM-Omni** (covers FLUX.2 / WAN2.2 / LTX-2 / Qwen-Image / … in one
  engine). Orchestration: **Ray Serve** (model pool + VRAM multiplexing +
  future scale-out).
- One unified API regardless of machine; dev deployment on H20 GPU.
- Each `op` is atomic; one `op` ← many `model_id`s (caller/workflow picks).
- Adapters implement `(op, model_id) → result` on vLLM-Omni.
- Sprite multi-inputs (control / normal / mask / reference) are first-class
  `inputs`, not bolted on.


## v1 Vertical Slice (first end-to-end proof)

One prompt → single sprite → pixelize + normal-estimate → preview / light-edit,
touching every layer (Op + Tool + workflow + frontend + CLI). The frontend adds
a **three.js interactive preview**: load the sprite + normal map, drag a dynamic
light source, watch normal-mapped shading update live.

## Working Discipline

- Recovery order: read this file → `.agent-os/project-index.md` → active
  `todo.md` → newest `run-log.md` → the smallest owning schema/module/test.
- Make the smallest final-form change and verify it. No dead code or routes.
- First-party Markdown stays below 500 lines.
- No commit, push, or history rewrite without explicit user instruction.

