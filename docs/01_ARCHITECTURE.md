# 01 · Architecture

Four ABI-decoupled layers. Each talks to the next only through a stable
contract, so any layer can be swapped independently.

```text
Model + Inference  ──[ POST /infer ]──►  atomic op, local OR remote (docker)
Workflow           ──[ typed tool graph ]──►  one minimal self-contained task
Frontend           ──[ triggers workflows ]──►  Web GUI (humans) + CLI/skill (agents)
```

## The three abstractions

```text
Task      — a DAG of workflow-nodes. Each node runs one workflow chosen from
              a `candidates` list ([0] is the default); node outputs wire into
              downstream workflow declared inputs.
  └── Workflow  — a flat DAG of tool-nodes with typed I/O. Declares external
        `inputs` (artifact ports); reusable across many tasks.
        └── Tool — the smallest unit, with typed I/O and a `kind`:
              ├── kind="inference"     — calls a model op via /infer; served by
              │     MANY model_ids.
              └── kind="deterministic" — a local, model-free step.
```

- A **Task** composes workflows: each node slot has a default candidate and
  optional alternates that callers can select by name.
- A **Workflow** is task-independent and reusable. It never nests another
  workflow; nesting is the task's job.
- **Topology is never shown to humans.** Developers and agents author graphs;
  the web frontend only triggers tasks.

## Typed I/O

Tools declare typed inputs/outputs so they compose safely:
`Image`, `ImageBatch`, `SpriteSheet`, `FrameSeq`, `Mask`, `NormalMap`,
`Palette`, and more as needed.

## Repository layout

```text
backend/    Python — /infer server, model adapters, model-op routing
workflow/   Python — schema, tool library, runner, ComfyUI export
cli/        Python — agent CLI + skill
web/        TypeScript — human toolbox + three.js light preview
docs/       this documentation
```

## Why decoupled

You can replace the inference backend (local ↔ docker ↔ different models), add
a new workflow route, or rebuild the web UI, and the other layers do not
change. That is the whole point of the ABI boundaries.
