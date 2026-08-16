---
name: cooksprite
description: Use the CookSprite API and cspr CLI to inspect registered sprite Actions, upload source media, run and wait for Actions, edit a versioned SpriteDocument, and export the canonical .cooksprite package.
---

# CookSprite Agent Harness

Use the stable Action surface. Do not call ComfyUI, author a Comfy graph, or
guess hidden prompt text. The Action registry is the source of truth.

## Discover

```bash
cspr actions --json
cspr action describe image.generate --json
```

Read `accepts`, `controls`, `models`, and `available` before running. A control
option may expose an `example` ArtifactRef; handle it exactly like any other
Artifact rather than extracting or passing its media URL.
Use the exact Action id and control ids returned by the API.
An Action id is the complete execution entry; do not look for or invent a
client-visible Task/Workflow selector.

## Project and upload

```bash
cspr project create --name "My sprite" --type character
cspr artifact upload character.png --project prj_x --kind Image
```

The application drag payload has exactly this shape:

```json
{"artifact_id":"art_x","kind":"Image"}
```

## Run and wait

```bash
cspr action run animation.generate \
  --project prj_x \
  --input character=art_x \
  --value action=walk \
  --value view=level \
  --value direction=s \
  --value model=rt_local:core-image-<checkpoint-hash> \
  --value count=8 \
  --wait
```

This is the exact public request shape:

```json
{
  "project": "prj_x",
  "inputs": {"character": "art_x"},
  "values": {"action": "walk", "view": "level", "direction": "s", "model": "rt_local:core-image-<checkpoint-hash>", "count": 8}
}
```

Never invent the model value shown above. Read the current `models` array from
the Action descriptor and pass one exact ID whose `modes` covers the requested
text/image inputs.

Animation, sheet slicing, and video sampling return one typed `FrameSeq`. Its
content is an ordered JSON manifest; child frames remain independent `Image`
artifacts. Expand it before selecting or editing frames:

```bash
cspr artifact sequence art_sequence_x
```

The response contains `sequence.action`, `sequence.view`,
`sequence.direction`, and ordered `frames`. Do not infer or overwrite a target
when any of those fields is absent; ask the user to specify it.

## Edit the SpriteDocument

Fetch the document and retain its `etag`. Change only semantic frame metadata
such as order, `duration_ms`, offsets, loop, pivot, views, clips, and artifact
references. PUT with the prior ETag. On HTTP 409, fetch again and reconcile;
never silently overwrite another editor.

```bash
cspr document get prj_x --out document.json
cspr document put prj_x document.json --etag <etag>
```

After the user has curated a track, materialize that exact document track as
the reusable hand-off `FrameSeq`. This is the sequence to pass to normals or
another Action; do not keep passing the raw generation candidates.

```bash
cspr project sequence prj_x --clip walk --view level --direction s
```

## Export

Run `sprite.export`. If validation reports missing directions or normals, fix
them. Set `allow_incomplete=true` only when the user explicitly accepts the
warnings. The only canonical delivery is the returned `.cooksprite` artifact.
