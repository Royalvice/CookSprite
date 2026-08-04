# 03 · Workflow

A **workflow** is one minimal, self-contained function: a graph of typed
components that turns inputs into sprite output. It is the unit a frontend
triggers.

## Components

Two kinds, both with typed inputs/outputs:

- **Op** — wraps an inference atom (`/infer`). May be served by several models;
  the workflow (or caller) picks a `model_id`, or takes the op's default.
- **Tool** — deterministic, local, model-free. Examples: `pixelize`,
  `pixel_perfect`, `crop`, `center_align`, `pack_sheet`.

Components declare typed ports (`Image`, `ImageBatch`, `SpriteSheet`,
`FrameSeq`, `Mask`, `NormalMap`, `Palette`, …) so connections are checked.

## Authoring vs using

- **Authoring** (developers/agents): wire components into a graph. Connections
  are explicit and exported by default.
- **Using** (humans): the web toolbox never shows the graph. You pick a
  capability, optionally pick a non-default workflow by name, set exposed
  params, and run.

## Capabilities and routes

A **capability** is the intent (e.g. `single_sprite`, `character_8dir`). It maps
to one or more named workflows; exactly one is the default. Example, for
`character_8dir`:

- `turntable_video` — img2vid turntable → frame_extract → pixelize → pack
- `sheet_crop` — text2img one sheet → crop into 8 → pixel_perfect
- `batch_ref` — reference img → batch text2img 8 → normal_estimate → pack

Callers select by name; otherwise the default runs.

## Running

The workflow runner resolves the graph, calls `/infer` for Ops, runs Tools
locally, and returns typed outputs (e.g. a sprite pair or a sheet). Failures
surface explicitly per component.

## ComfyUI export

Any workflow can be exported to ComfyUI **API-format JSON**. See
`04_COMFYUI_EXPORT.md`.
