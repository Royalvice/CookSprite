# 04 · ComfyUI Export

Many users already run ComfyUI. CookSprite does **not** depend on it and is
**not** built on its `custom_nodes`. Instead, any CookSprite workflow can be
**exported** to a ComfyUI workflow so those users can run it in their own
environment.

## What gets produced

ComfyUI's **API-format** JSON — the lean, UI-metadata-free format:

```json
{
  "3": { "class_type": "KSampler", "inputs": { "seed": 42, "model": ["4", 0] } },
  "4": { "class_type": "CheckpointLoaderSimple", "inputs": { "ckpt_name": "..." } }
}
```

Each node is keyed by id, has a `class_type` and an `inputs` map; node-to-node
links are `["node_id", output_index]` arrays.

## Why this is cheap

CookSprite's own workflow is a typed tool graph. ComfyUI's API format is a
node graph with the same essential shape. So the exporter is a **structural
translation** (map our tools → ComfyUI node `class_type`s, our edges →
their link arrays), not a runtime integration.

## Boundaries

- Export is one-directional and best-effort: only tools with a known
  ComfyUI equivalent translate. Unmapped tools are reported, not silently
  dropped.
- The exported JSON runs in the user's ComfyUI (with the matching nodes/models
  installed); CookSprite does not launch or manage ComfyUI.
