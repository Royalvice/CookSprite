# 03 · Product workflow

The workbench follows one visible sequence so beginners do not need to learn
graphs:

1. **Create** — describe a character, weapon, prop, terrain, scene element, or
   VFX; optionally drop one reference image for image-to-image.
2. **Animate** — choose one action, one view, and one real direction; generate
   a `FrameSeq`, then curate it in the same page. Hover/focus to preview, click
   to place a green check, range-select, replace or append to the named final track, play,
   reorder, repeat, delete, set exact milliseconds and offsets, compare A/B,
   onion-skin/diff, redraw, and undo/redo.
3. **Normals** — generate normals for one image or every frame in the current
   sequence and inspect the real normal map or the pair under neutral, day, and
   night HDR light.
4. **Ship** — validate and emit `.cooksprite`; incomplete exports require an
   explicit acknowledgement and record their warnings.

The Animate stage also imports a SpriteSheet or GIF/video. Import chooses the
target action, view, and direction before extraction. SpriteSheet controls cover
auto/manual rows and columns, frame width/height, margin, spacing, and empty-cell
exclusion. Video controls expose sampling FPS and maximum frames.
Candidate lists virtualize after normal browser overflow and remain keyboard
operable.

Every imported/generated item is an Artifact and can be dragged to any
compatible slot with the same `{artifact_id, kind}` payload. External files can
be dropped or selected without a prior upload step. Originals stay in the
Library; replacement variants add lineage instead of overwriting bytes.

Transient preview and committed selection are separate states. Hovering never
changes the character/reference/normal input. After image generation, explicit
buttons carry the selected result into image-to-image, animation, or normals.
After curation, the final document track is materialized as a typed `FrameSeq`;
that final sequence, not the raw candidate sequence, is the default hand-off to
normal generation and later Actions. Per-project stage, form, input, selection,
and active-sequence workspace state is restored after a browser refresh.

The Gallery is not a feed. A project appears only after **Finish & Showcase**.
There are no accounts, recommendations, or telemetry in v0.1.

## Automation

CLI and agents do not imitate clicks. They discover the same Action registry,
create a Project, upload Artifacts, run Actions, wait for Run completion, edit
the ETag-protected SpriteDocument, and run `cspr project export`. See
`skills/cooksprite/SKILL.md` for the concise harness contract.
