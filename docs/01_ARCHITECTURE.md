# 01 · Architecture

Four ABI-decoupled layers. Each talks to the next only through a stable
contract, so any layer can be swapped independently.

```text
Model + Inference  ──[ POST /infer ]──►  atomic op, local OR remote (docker)
Workflow           ──[ typed component graph ]──►  one minimal self-contained function
Frontend           ──[ triggers workflows ]──►  Web GUI (humans) + CLI/skill (agents)
```

## The three abstractions

```text
Capability  — a semantic intent, e.g. "generate an 8-direction character".
  └── Workflow(s) — named routes to that intent; one default, others opt-in.
        · route A: video model turntable → extract frames
        · route B: image model → one sheet → crop into N
        · route C: 1 reference image → batch-infer N frames
        └── Component — typed I/O unit, composed by developers/agents.
              ├── Op   — an inference atom; served by MANY model_ids via /infer.
              └── Tool — a deterministic, local, model-free step.
```

- A **Capability** is decoupled from *which model* and *which route* fulfills
  it. Same capability, many workflows; each workflow's Ops each selectable
  across many models.
- **Route selection:** each workflow is named, one is default per capability;
  callers may explicitly pick another. No hidden auto-ranking.
- **Topology is never shown to humans.** Developers and agents author component
  graphs; the web frontend only triggers them.

## Typed I/O

Components declare typed inputs/outputs so they compose safely:
`Image`, `ImageBatch`, `SpriteSheet`, `FrameSeq`, `Mask`, `NormalMap`,
`Palette`, and more as needed.

## Repository layout

```text
backend/    Python — /infer server, model adapters, Op registry
workflow/   Python — schema, Component/Tool library, runner, ComfyUI export
cli/        Python — agent CLI + skill
web/        TypeScript — human toolbox + three.js light preview
docs/       this documentation
```

## Why decoupled

You can replace the inference backend (local ↔ docker ↔ different models), add
a new workflow route, or rebuild the web UI, and the other layers do not
change. That is the whole point of the ABI boundaries.
