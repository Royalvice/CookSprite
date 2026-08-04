# 00 · Vision

CookSprite is a general, open-source tool for producing 2D **sprites** with AI.

## What it is for

Making sprites is slow. CookSprite turns a prompt (and optional references) into
usable sprite output — with the signature unit being a **sprite pair**: a
diffuse frame plus a same-size normal map, so the result lights dynamically
instead of looking flat.

It is **AI-generation-first**. It is not, and will not become, a hand-drawing
pixel editor — mature tools already do that. The human frontend previews,
selects frames, does pixel-perfect cleanup, and regenerates single frames;
detailed hand-pixeling round-trips to a dedicated editor via PNG sheets.

## Who uses it

- **Agents** — call it fastest, through a CLI + skill. This is the primary
  audience; there is one obvious way to do each thing.
- **Humans** — a web toolbox: pick a capability, run, preview, light-edit.
  Never author node graphs.
- **Contributors** — add a model adapter or a workflow without touching the
  other layers.

## Design values

- **Lowest usage burden wins.** Every choice is judged by mental load.
- **Fully general.** No downstream project's assumptions are baked in — canvas
  size, direction count, frame rate, and naming are all config.
- **Simply First.** One default route per capability, explicit errors, no
  silent fallbacks or hidden second implementations.

## Non-goals

- Not a ComfyUI replacement — we export to ComfyUI, we don't compete with its
  node ecosystem.
- Not a general image editor or a hand-drawing tool.
- Not tied to any specific game or engine.
