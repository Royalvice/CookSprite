# CookSprite Web

Human-facing web toolbox for CookSprite, a general open-source AI sprite-generation
tool. This app does not draw sprites by hand — it triggers AI workflows on a
separate workflow server and previews the results, with a live diffuse + normal
map lighting preview.

## Requirements

- Node 18+ and npm
- The CookSprite **workflow server** running on `http://localhost:8000`.
  In dev, Vite proxies `/api/*` to that server (see `vite.config.ts`).

## Run

```bash
npm install
npm run dev      # start Vite dev server (default http://localhost:5173)
```

Open the printed URL. If the toolbox is empty and shows a connection banner,
the workflow server on `:8000` is not reachable yet.

```bash
npm run build    # type-check (tsc) + production build to dist/
npm run preview  # serve the production build
```

## What it does

1. On load, fetches `GET /api/capabilities` and renders the **Toolbox**.
2. Pick a capability (its default workflow is preselected), fill params
   (prompt, width, height, pixelize, normal), and click **Run**.
3. `POST /api/run` returns a `run_id`. The app subscribes to
   `GET /api/runs/{id}/events` (SSE) and shows a progress bar in **RunStatus**.
   If SSE errors, it falls back to polling `GET /api/runs/{id}` every 700ms.
4. When `status === "done"`, it fetches the result and renders:
   - **SpritePreview** — the diffuse sprite, with a pixel-perfect toggle
     (`image-rendering: pixelated` + integer scaling).
   - **FrameStrip** — a selectable frame strip when the sheet has `frames > 1`.
   - **LightPreview** — the signature feature: a three.js quad textured with the
     diffuse (albedo) and normal map. Drag the mouse to move the point light in
     the plane, scroll to change its height (z). Ambient slider and light-color
     picker included. This proves the diffuse+normal pair lights dynamically.
5. On `status === "error"`, the message is shown in RunStatus.

## Server contract

Base path `/api` (proxied to `:8000`):

- `GET /api/capabilities`
- `POST /api/run` → `{ run_id }`
- `GET /api/runs/{run_id}` → status/progress/message/result
- `GET /api/runs/{run_id}/events` → SSE, each `data:` line is the run state JSON
- `GET /api/runs/{run_id}/result` → `RunResult`
- `GET /api/artifacts/{id}` → PNG bytes

Types live in `src/api.ts`.
