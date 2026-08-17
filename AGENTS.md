# CookSprite agent contract

CookSprite is a general open-source AI sprite tool. Its signature output is a
SpritePair: diffuse art plus a same-size normal map for dynamic-light preview.
Lowest user burden and one canonical implementation per capability win.

## System boundary

```text
Human Web ──────────────────────┐
Human / Agent → cspr CLI ───────┼→ CookSprite /api/v1 → ComfyUI → Artifacts
Contributor graph clients ─────┘
```

CookSprite API is the control plane. It validates typed requests, versions and
compiles graphs, schedules runs, tracks state/provenance, and persists declared
artifacts. It never runs AI inference or performs image, mask, audio, video, 3D,
or other media computation.

ComfyUI is the sole compute plane. Every authoritative media transformation,
including deterministic processing, executes as an installed ComfyUI node or a
sealed node subgraph. Web never talks to ComfyUI, authors graphs, or creates an
authoritative derived artifact. Browser-only interaction such as three.js
lighting preview is presentation state and is allowed.

`CS_LoadArtifact` and `CS_StoreArtifact` are the only artifact bridge. Artifacts
are typed, SHA-256 content-addressed blobs with SQLite metadata. Comfy upload,
output, and view folders are never public storage.

## Product and composition model

Stable Actions are the only ordinary product entry points. Internally they
compile through the following model:

```text
Action → Task → Workflow → Tool → cooksprite.* / comfy.* ComfyUI nodes
```

- An **Action** is stable user intent shared by Web, CLI, and agents.
- A **Task** is a versioned DAG of Workflow nodes with explicit candidates.
- A **Workflow** is a versioned, flat DAG of Tools; it never nests Workflows.
- A **Tool** is the smallest reusable typed capability. Its executable lowering
  is a ComfyUI node or sealed subgraph; it has no API-side compute fallback.
- A **Recipe** binds an Action to a validated Task/Workflow, models, and one
  immutable runtime snapshot. Runtime changes require a new revision.

Tool ports use CookSprite domain types such as `Image`, `Mask`, `FrameSeq`,
`SpriteSheet`, `NormalMap`, and `SpritePair`, represented by shared Python
schema classes. Raw Comfy values, filesystem paths, temporary URLs, and
node-specific dictionaries may exist only inside an adapter and must not cross
a Tool, API, CLI, or artifact boundary. Output ports, never names or file
extensions, determine artifact kinds. Only declared persistable outputs leave
the graph.

`cooksprite.*` Tools lower to our custom-node packages. `comfy.*` Tools are
dynamically discovered from a connected runtime. Discovery proves presence;
only a validated Recipe proves that an Action is available. There is no hidden
fallback route.

Every Task and Workflow must have a compiler-owned standalone lowering to a
standard ComfyUI workflow that runs successfully in a compatible ComfyUI
runtime without CookSprite orchestration. Standalone execution and execution
through CookSprite must be equivalent: given the same revision, immutable
runtime snapshot, model and node versions, input artifact bytes, prompts, seed,
sampler, scheduler, deterministic settings, and all other parameters, declared
outputs must have the same domain types and canonical SHA-256 content hashes.
Every implementation must prove this with a real-ComfyUI conformance test; a
hidden API-side transform, alternate graph, tolerance-based visual comparison,
or nondeterministic fallback does not satisfy the contract.

## Tool packages

Related Tools form one cohesive, versioned Tool package—for example alpha,
frames, normals, or export—not a grab bag. Prefer composition and narrow
protocols over deep inheritance or global switch statements. Each package owns:

- its typed Tool contracts and domain configuration;
- its server-side ComfyUI node implementations;
- its Workflow/Task definitions and model/node requirements;
- its compiler registration, provenance, license metadata, and tests.

Package implementations must be headless and require no ComfyUI browser
extension. Content under `.local/` is untracked reference material only: never
import it at runtime, package it, or copy it without an explicit license and
clean-room provenance review.

## One registry, all clients

One structured Action/Tool registry is the source of truth. Its projections
produce API discovery and validation, `cspr` commands/help, Vue controls and
types, and the CookSprite agent Skill reference. These surfaces must not keep
independent handwritten capability lists.

Adding a capability is incomplete until the same change provides:

1. typed Tool package contract and ComfyUI implementation;
2. Workflow/Task/Recipe lowering and stable Action exposure;
3. CLI list/describe/run/wait/cancel/result support without a browser;
4. generated or compiler-checked Web controls and Agent Skill documentation;
5. contract tests plus real ComfyUI execution returning typed artifacts.

Agents invoke `cspr`; they do not call internal Python classes or ComfyUI.
Humans may use either Web or `cspr`. Every principal product workflow must be
completable by CLI against `/api/v1`, without starting a frontend or browser.

## Distribution and installation

- The Python distribution `cooksprite` owns domain schemas, compiler, API,
  CLI, installer, and node-pack payload/metadata, and ships as a wheel/sdist.
- `web/` is a TypeScript + Vue npm workspace used to build and test the UI.
  End users must not need Node.js or npm: release builds ship its static output
  inside the Python/release distribution and the CookSprite server serves it.
- CookSprite custom nodes are independently versioned as standard ComfyUI node
  packages but are installed and verified by the CookSprite installer.
- One explicit install command is the target UX for CLI, API, built Web assets,
  an isolated pinned ComfyUI when needed, and compatible CookSprite node packs.
  Existing local or remote ComfyUI installations use the same API and may be
  registered without copying or modifying their existing models.
- Model downloads are never a startup side effect. An install command must show
  model identity, source, license, size, destination, and obtain explicit
  consent before downloading. Installation must be resumable and verifiable.

## Project rules

- Stable Actions minimize usage burden; contributor graph APIs are advanced
  integration surfaces, not normal product UX.
- No game-specific geometry, names, assets, or baked direction/canvas policy.
- No user accounts in v0.1; use trusted localhost or private networking.
- Do not commit secrets, models, generated outputs, `.env`, `.agent-os`, or
  `.local`.
- Preserve user changes. Do not commit, push, rewrite history, or publish
  packages without explicit permission.
