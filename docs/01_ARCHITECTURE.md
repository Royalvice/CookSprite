# 01 · Architecture

## Deployment boundary

CookSprite assigns responsibility to processes, never host identity. Every
clone has the same code and capabilities.

```text
Web / CLI / agent
          │ stable Action / Project / Artifact API
          ▼
CookSprite API
  Web · SQLite · Project documents · Artifact Blob Store · exports
          │ private Action → Task → Workflow → Tool lowering
          │ signed CS_LoadArtifact / CS_StoreArtifact URLs
          ▼
selected ComfyUI Runtime
  Custom Nodes · models · accelerator
```

The API/data-directory location determines Project and Artifact ownership. The
selected ComfyUI Runtime determines inference location. Both may run on one
host or separate hosts without changing product contracts.

ComfyUI `output/`, `temp/`, model cache, and execution cache are local
implementation data, not product storage. Source synchronization is also a
separate concern: ordinary clones use only the shared Git remote with
`git push` and `git pull --ff-only`. Model weights and generated media are data,
not Git contents.

## Product API boundary

The public `/api/v1` surface is intentionally small:

- stable Action discovery and Action Runs;
- Projects, SpriteDocuments, Artifacts, exports, gallery, Runs, and queue;
- explicit Runtime probe/import/doctor/select/defaults/capabilities;
- controlled Recipe registration for an already doctored Runtime;
- the scoped Artifact Bridge used only by custom nodes.

The API validates typed requests, assembles private graphs, schedules Runs,
tracks normalized progress/provenance, and persists declared Artifacts. It does
not run media computation, manage a connected Runtime host process, install a
node pack, download a model, inspect a remote filesystem, or infer a remote
callback URL.

Public clients cannot create Tools, Workflows, Tasks, or generic Runs. Those
are private compilation structures:

```text
Action → Task revision → Workflow revision → Tool → ComfyUI node/subgraph
```

Their revisions remain in SQLite only as needed to lower an Action and record
Run provenance. A client selects stable user intent, typed input Artifacts, and
declared values; it never receives a raw graph, Comfy path, prompt ID, or
temporary file URL.

## Runtime adaptation

A reachable ComfyUI and an Action-capable Runtime are separate facts. Doctor
reads its live node schema, system information, and model inventory. A Recipe
then binds stable Actions to a compatible model/workflow and one immutable
Runtime snapshot. A new runtime snapshot requires new internal definitions;
there is no hidden fallback to a different model or graph.

For any worker-managed Runtime, doctor additionally requires
`GET /cooksprite/runtime-info`. The identity contains only the source branch,
source revision, node-pack version, dependency-lock SHA-256, and Comfy URL. The
active API records the first observed identity. If any later doctor observes a
change, it returns `worker_runtime_incompatible` until that API explicitly
re-registers the Runtime after an approved Git/worker synchronization.

Every remote Runtime registration must include an explicit API `callback_url`
reachable from that ComfyUI. Loopback/default callback inheritance is forbidden
for remote compute. A worker-managed Runtime may be local or remote.

## State and durability

```text
<API data directory>/
├── cooksprite.sqlite3              metadata and current Project/Run state
└── artifacts/<sha256>              canonical immutable media bytes
```

- `Project`: name, type, publication state, and document reference.
- `SpriteDocument`: mutable semantic state with ETag concurrency.
- `Artifact`: immutable content-addressed blob plus kind, media type, lineage,
  favorites/trash state, and project references.
- `Run`: one Action request, normalized state/error, declared output
  references, and immutable runtime/package/definition provenance.

Project directories are not mirrors of blobs. An explicit export creates the
canonical `.cooksprite` package; it does not make a second user-visible media
store. Artifact uploads stream into an API-owned same-filesystem staging file,
compute SHA-256 incrementally, fsync, and atomically promote to
`artifacts/<sha256>`. Downloads use file streaming rather than reading the
whole Artifact into API memory. Export packaging likewise streams blobs in
bounded chunks into a staged ZIP before atomically promoting that package.

## Managed Runtime lifecycle

`cspr comfy worker init/install/sync/start/stop/restart/status/doctor` is the managed
ComfyUI lifecycle contract. A worker owns one non-Git Runtime sibling of its
source clone. It only stops PIDs recorded by that Runtime and only when the
command line proves ownership. `install` and `sync` reject dirty source, a
changed pinned origin, local-only `HEAD`, a running listener, and
non-fast-forward Git state; they synchronize the locked environment and
atomically swap the node pack while stopped. Device selection is backend-neutral
and shared by default. Optional exclusivity is implemented by registered
resource inspectors; the current CUDA inspector uses process ownership rather
than utilization as its signal.

The same acceptance path applies to local and remote Runtimes:

```text
API stores input Artifact
→ API creates Action Run and signed bridge URLs
→ selected ComfyUI loads declared bytes
→ ComfyUI graph executes
→ ComfyUI stores declared output
→ API Blob Store persists it and marks the Run succeeded
```
