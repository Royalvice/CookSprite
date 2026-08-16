# 01 · Architecture

## Boundary

```text
Human              Automation                Contributor
Vue UI              cspr / Skill              graph APIs
   └──────────────────────┬────────────────────────┘
                          ▼
                 CookSprite /api/v1
       Action registry · validation · compiler · runs
       projects · SpriteDocument · artifact metadata
                          │ private prompt
                          ▼
                       ComfyUI
              inference and all media operations
                          │ CS_StoreArtifact
                          ▼
        SQLite metadata + data/artifacts/<sha256>
```

CookSprite is a control plane, not an inference server. The API may validate,
version, compile, schedule, package existing bytes, and track provenance. It
must not run a model or perform an image/video operation. ComfyUI is the only
execution runtime. `CS_LoadArtifact` and `CS_StoreArtifact` are the sole bridge;
Comfy upload/view folders are not public storage.

## Product layer

Most clients use stable Actions. One YAML registry is compiled into:

- API discovery and validation;
- Vue controls, typed option examples, input slots, availability, and model choices;
- `cspr action describe/run`;
- the CookSprite agent Skill.

Option examples are normal `ArtifactRef` values, not media URLs or hidden prompt
packs. Every business image is rendered from an Artifact and uses the same
`{artifact_id, kind}` drag payload. The browser sends Action IDs, artifact IDs,
and user-visible values; it cannot author or submit a Comfy graph.

## Contributor layer

Advanced integrations can register runtimes, inspect Tools, and author typed
Workflow/Task definitions. A Workflow is a flat Tool DAG. A Task is a Workflow
DAG with explicit candidates. Every revision is immutable and bound to one
doctor snapshot. A runtime change requires a newly validated revision; there is
no hidden fallback.

The canonical lowering chain is `Action → Task revision → Workflow revision →
Tool → ComfyUI node`. These are authoring layers, not separate executors. Each
ends in the same private `ExecutionPlan` (`graph` plus declared artifact sinks),
then uses one submit/wait/cancel/error path. Tool output ports determine
artifact kinds; names such as `normal` are never used to guess a type. A Tool is
not directly runnable because a versioned Workflow must declare which typed
outputs may leave ComfyUI.

Related built-in Tools live in static, versioned Tool packages. One aggregate
registry owns the Action list, package manifests, node lowerings, dependency
checks, CLI/Web constants, and generated Agent reference. There is no dynamic
plugin marketplace or API-side media fallback.

## Runtime adaptation

An online ComfyUI and a compatible Action are separate facts. Doctor snapshots
node schemas and model folders; a compact `Recipe` binds an Action to one
checkpoint or imported API-format workflow and declares only:

- supported Action IDs;
- accepted modes such as `t2i`, `i2i`, `i2i-sequence`, or `video-to-frames`;
- checkpoint identity or workflow graph;
- semantic input slots and one typed output.

This keeps arbitrary user ComfyUI installations useful without guessing what a
node/model combination means. Runtime liveness plus at least one verified Recipe
is required before the product says “ready.” Imported recipes are tied to the
doctor snapshot and cannot silently survive incompatible node changes.

The same topology works locally or remotely. For remote compute, CookSprite API
is deployed beside ComfyUI, and signed artifact bridge traffic stays between
those services. Web, CLI, and agents see the same Actions and never receive
Comfy URLs, filesystem paths, workflow JSON, or prompt IDs.

## State

- `Project`: name, static/character/tileset type, publication state.
- `SpriteDocument`: semantic editable state with ETag concurrency: pivot,
  canvas, clips, views, direction tracks, frame order, timing, offsets, normals.
- `Artifact`: immutable SHA-256 blob plus kind, media type, lineage, favorites,
  trash state, and project links.
- `Run`: Action/graph request, public status, normalized error, artifact outputs,
  immutable definition/package/runtime provenance, and private runtime identifiers.

A `RunSupervisor` owns worker lifetime. It maps ComfyUI WebSocket sampler steps
to public Run progress, falls back to history polling through incompatible
proxies, resumes already-submitted prompts after restart, and gives an explicit
retryable failure to work interrupted before submission.

The local Gallery is deliberately manual: only explicitly published projects
appear. v0.1 assumes trusted localhost/private networking and has no users.
