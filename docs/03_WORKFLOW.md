# 03 · Workflow

A **workflow** is one minimal, self-contained task: a graph of typed
tools that turns inputs into sprite output. It is the unit a frontend
triggers.

## Tools

A **tool** is the smallest unit that satisfies one minimal function. Every tool
declares typed inputs/outputs and has a `kind`:

- **`kind="inference"`** — calls a model op via `/infer`. May be served by
  several models; the workflow (or caller) picks a `model_id`, or takes the
  op's default. Examples: `text2img`, `img2img`.
- **`kind="deterministic"`** — local, model-free. Examples: `pixelize`,
  `normal_estimate`, `crop`, `center_align`, `pack_sheet`.

Tools declare typed ports (`Image`, `ImageBatch`, `FrameSeq`, `SpritePair`,
`SpriteSheet`, `Mask`, `NormalMap`, `Palette`) so connections are checked.
`ImageBatch` is an **unordered** set (e.g. batch candidates for one prompt);
`FrameSeq` is an **ordered** sequence (animation / turntable), and is what
`pack_sheet` consumes.

Port typing is exact-match today (a `"any"` port opts out). Subtyping and
union ports are a deliberate non-feature until a real cross-type tool needs
them — see `.agent-os/change-decisions.md` CD-026.

## Tasks

A **task** is a DAG of workflow-nodes. Each node has a `candidates` list of
workflows (`candidates[0]` is the default); callers select a non-default
candidate by name. Node outputs wire into downstream workflow declared
`inputs`. Example — `single_sprite`:

```
gen (generate_image) ──► spr (spritify)
```

`spr` is wired `inputs: {src: gen}`, feeding `gen`'s image output into
`spritify`'s declared `$in.src` input.

## Authoring

- **Authoring** (developers/agents): wire tools into a workflow graph, wire
  workflows into a task. Connections are explicit.
- **Using** (humans): the web toolbox never shows the graph. Pick a task,
  optionally pick a non-default workflow candidate per node, set exposed
  params, and run.

## Running

The workflow runner resolves the graph, calls `/infer` for inference tools,
runs deterministic tools locally, and returns typed outputs (e.g. a sprite pair
or a sheet). Failures surface explicitly per tool.

## ComfyUI export

Any workflow can be exported to ComfyUI **API-format JSON**. See
`04_COMFYUI_EXPORT.md`.
