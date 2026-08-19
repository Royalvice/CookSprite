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

When introducing a new model, first find and validate the smallest official
inference example and its official ComfyUI/API workflow. If no official
workflow is available, use a well-maintained, high-star open-source pipeline
as the starting point. Do not write a workflow from scratch by default.
Preserve the upstream graph and version/provenance information, then add only
the CookSprite adapter, parameter mapping, Recipe registration, and required
typed post-processing.

Recipe assembly is API-owned and generic. Runtime-discovered/imported ComfyUI
graphs declare semantic slots, slot types, and one typed output; the shared
`RecipeAssembler` turns any such Recipe into the same Prompt Tool → sealed
workflow → CookSprite post-process DAG. Do not add a model-specific branch to
the API for a new raw workflow. Stable Action controls stay in the registry;
workflow-specific scalar knobs travel through the reserved `params` map and
must match a declared Workflow input. An undeclared or wrongly typed parameter
is an explicit error. Only a new computation requires a Tool package and
custom-node implementation.

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

Prefer official Comfy-Org packaged models, built-in nodes, and official
workflow/blueprint templates whenever they satisfy the typed Action contract.
Use a CookSprite-specific implementation only when official support is absent
or insufficient, and record the selected source and version in provenance.

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

## Dependency and environment boundary

CookSprite owns exactly two Python environments:

- the repository `.venv`, containing only CookSprite API, CLI, compiler, and
  development dependencies from `pyproject.toml` and `uv.lock`;
- the managed ComfyUI runtime `.venv`, containing only the pinned ComfyUI
  runtime, PyTorch/accelerator packages, and dependencies declared by the
  registered CookSprite Tool Packages.

These environments are deliberately disjoint. API/CLI code must not import
Torch, ComfyUI, rembg, ONNX runtimes, or media-compute libraries. ComfyUI
nodes must not depend on the CookSprite API environment. Models and caches are
data, never Python dependencies.

The ComfyUI environment is resolved from `cooksprite/comfy/requirements.in`
and installed from its generated `requirements.lock`. Tool Package manifests
are the only source for CookSprite node dependencies; the node requirements
file and ComfyUI lock are generated, not hand-maintained. A new or changed
custom node is incomplete until `cspr dev sync`, `cspr comfy lock`, and a
locked `cspr comfy sync <runtime>` succeed. `uv` is required to refresh locks;
an existing lock may be installed with the release fallback when `uv` is not
available.

Do not add a dependency to both environments for convenience. Do not install
unlocked packages with bare `pip` into a managed environment. External or
remote ComfyUI installations remain user-owned; CookSprite may inspect them,
but only an explicit node installation may modify their environment.

## Project rules

- Stable Actions minimize usage burden; contributor graph APIs are advanced
  integration surfaces, not normal product UX.
- No game-specific geometry, names, assets, or baked direction/canvas policy.
- No user accounts in v0.1; use trusted localhost or private networking.
- Do not commit secrets, models, generated outputs, `.env`, `.agent-os`, or
  `.local`.
- SQLite is a current-state metadata store by default: write the latest state
  in place, keep only what is needed for current operation, and do not append
  unbounded snapshots, events, or history. Any required history must be an
  explicitly bounded, separately designed feature with a retention policy.
- Preserve user changes. Do not commit, push, rewrite history, or publish
  packages without explicit permission.
