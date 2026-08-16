# CookSprite UI design system

This file is the frontend visual source of truth. Page files under `pages/`, if
added later, may override only the named page.

## Intent

- Product: local-first creative Sprite workbench.
- Tone: pixel arcade cabinet outside, calm production tool inside.
- Dials: visual variance 8/10, motion 6/10, density 7/10.
- Rule: decoration must strengthen orientation or creative feedback. The canvas,
  frame timing, and asset lineage always outrank ornament.

## Type

- Pixel labels and compact system copy: local bilingual Fusion Pixel Font.
- Body and long helper text: system sans (`Avenir Next`, Avenir, sans-serif).
- Data, IDs, hashes, and timings: system monospace with tabular numbers.
- Body text is 14px desktop and at least 16px on mobile reading surfaces.

No network font is required. Fusion Pixel is distributed under its OFL file in
`web/public/fonts`.

## Semantic color themes

All components use semantic CSS variables; no page owns raw colors.

| Token | Neon Forge | Ember Cabinet | Mint Handheld |
|---|---:|---:|---:|
| `--bg` | `#0B1020` | `#170F10` | `#E7F3E5` |
| `--surface` | `#151B31` | `#29191A` | `#F7FFF4` |
| `--primary` | `#7C3AED` | `#F97316` | `#166534` |
| `--aux` | `#22D3EE` | `#22D3EE` | `#7C3AED` |
| `--confirm` | `#22C55E` | `#A3E635` | `#15803D` |
| `--danger` | `#EF4444` | `#F87171` | `#B91C1C` |
| `--text` | `#F8FAFC` | `#FFF7ED` | `#132019` |

Transparent art uses a fixed neutral dark checkerboard in all themes so visual
judgement does not change with the shell palette. Green is preview only;
transparency is canonical.

## Geometry and depth

- 4/8px spacing rhythm; primary bands use 16/24/32px.
- Square corners, one-pixel borders, and 3/5/8px hard offset shadows.
- No soft rounded SaaS cards and no decorative glass panels.
- One active primary action per stage; secondary actions use outline/text styles.
- Z-index scale: content 0, sticky header 50, mobile navigation 60, dialogs 90,
  notices 100, scanline overlay 9999.

## Interaction

- All essential controls are buttons, links, labels, or inputs with visible
  keyboard focus; no hover-only command.
- Minimum interactive target is 44×44px. Drag/drop always has a file picker and
  frame selection always has keyboard controls.
- Selected frames show both a green check and text. Errors include a recovery
  path. Async Actions disable repeat submission and expose Run state in Queue.
- Motion uses 150–300ms transform/opacity transitions. Pixel preview movement
  may use stepped timing. `prefers-reduced-motion` reduces all animation.

## Responsive behavior

- 1440px: stage rail + workbench + inspector.
- 1024px: inspector stays in its own narrow column and never overlays editing.
- 768px: compact editor remains usable; dense rows may scroll within their own
  explicit strip.
- Below 768px: browse, queue, preview, and download remain available; editing is
  replaced with a clear larger-screen message.
- 375px: one-column gallery/library, fixed four-item bottom navigation, no
  horizontal page scrolling, and safe bottom padding.

## Component rules

- Icons: Phosphor outline family only; no emoji as structural icons.
- Presets: visual preview + bilingual title/description; hover/focus previews,
  click commits.
- Artifact cards: fixed aspect ratio, declared dimensions, pixelated rendering
  for generated sprites, exact `{artifact_id, kind}` drag payload.
- Frame Studio: animation generation and curation share one page. Target action,
  view, and direction are chosen before generation/import and shown read-only
  while curating. Source order is preserved, candidates are virtualized, and
  the final track supports per-frame milliseconds/offsets, playback, loop, A/B,
  onion/diff, redraw, undo/redo, and explicit save feedback.
- Normal Lab: Three.js nearest-filter textures, actual normal map, movable point
  light with an on-canvas gizmo, lit/diffuse/normal views, neutral/day/night HDR
  environments, and explicit intensity/height/color/Y-flip.

## Delivery gates

- Build and typecheck pass.
- Chromium full flow plus Firefox/WebKit smoke pass.
- Screenshots checked at 1440, 1024, 768, and 375px in all three themes.
- Reduced-motion run has no console errors.
- Buttons have accessible names; focus ring and skip link are visible.
- Production dependency audit reports zero vulnerabilities.
