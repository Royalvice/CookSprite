# 05 · ComfyUI Capability Map and Evidence Index

**Scope.** This is a standalone technical map of ComfyUI: what the platform
itself provides, what depends on a particular model or custom node, and what it
does not promise. It is not an integration design, product API, or workflow
authoring guide for this repository.

**Research snapshot:** 2026-08-11. ComfyUI changes quickly. Treat links to
official documentation as the current intent, but treat a pinned ComfyUI commit
plus a clean-environment run as the evidence for a production claim.

## 1. How to read this document

| Mark | Meaning |
| --- | --- |
| **Core** | Implemented and shipped by ComfyUI itself. It is still version-sensitive. |
| **Ecosystem** | Supplied by a specific custom-node package, not by ComfyUI Core. |
| **Model-dependent** | The graph can express the operation, but the model and its loader determine whether it works or is useful. |
| **Cloud-specific** | A Comfy Cloud API/service feature; do not infer it for a local server. |
| **Verified** | Tested in a named, pinned environment. This document makes no such claim unless an evidence record says so. |
| **Not guaranteed** | Must not be used as an architectural assumption without a local proof. |

The source hierarchy is deliberate:

1. Official documentation explains the intended public behavior.
2. The official repository at an exact commit explains the behavior actually
   shipped by that revision.
3. A clean install and an API execution test prove a selected configuration.
4. A third-party node README or model card only proves that its author makes a
   claim; it is not a ComfyUI Core guarantee.

## 2. What ComfyUI is

ComfyUI is a node-graph environment and execution server for generative-media
workflows. A workflow is a directed graph of node instances: each node accepts
typed values, does one operation, and may feed its outputs into downstream
nodes. The same graph can be edited visually or represented as JSON.

The normal architecture has two sides:

```text
JavaScript client                  Python server
canvas, widgets, graph editor  ->  node validation, data processing,
                                   model loading, diffusion and execution
```

The server uses `aiohttp`/`asyncio`; the web client sends a complete workflow
when it queues a run. Editing a canvas after submission does not mutate the
already queued execution. Real-time status travels over WebSocket messages.

This architecture enables a non-Comfy client to submit a workflow in API mode.
It does **not** mean that every ComfyUI visual feature or every third-party node
is usable without the official web client.

Primary references: [custom-node overview][node-overview], [server
overview][server-overview], and [workflow concept][workflow-concept].

## 3. Capability map

| Area | What ComfyUI provides | Boundary that must remain explicit |
| --- | --- | --- |
| Graph composition | **Core.** A node graph can express data flow, branching-like composition, loaders, samplers, transforms, and outputs. Workflows can be saved as JSON or embedded in generated-image metadata. | A graph is an executable configuration, not a stable domain-level API or a business workflow language. |
| Image diffusion | **Core + model-dependent.** Core includes loaders, conditioning and sampling machinery for its supported model families. | A checkpoint file alone is not proof that its architecture, loader, VAE, text encoder, or graph is supported. |
| Image operations | **Core and ecosystem.** Basic image/media plumbing exists; the ecosystem adds many transforms, masks, segmentation, control and utility operations. | The name of a node is not a capability guarantee. Record its package, revision, dependencies, device needs and output contract. |
| LoRA and other auxiliaries | **Core + model-dependent.** Workflows can load auxiliary weights such as LoRAs, VAEs and ControlNets. | The auxiliary weight must match the model family and loader path used by the graph; compatibility is not inferred safely from a filename. |
| Video, audio and 3D | **Core framework + model/node-dependent.** The project supports workflows that can produce many media types, and ships or documents support for selected families. | There is no generic “video/audio/3D works” guarantee: model support, custom nodes, VRAM, codecs, preprocessing and output conventions vary per workflow. |
| Custom operations | **Core extension mechanism.** A Python custom node can define inputs, outputs and an executable function. | A package may be server-only, UI-only, independently paired with UI code, or directly coupled to the client. Server-only nodes and the server portion of independently paired nodes are candidates for a headless worker. |
| Local API execution | **Core.** A non-Comfy client can submit an API-format graph, track execution and retrieve outputs. | The raw server API is low-level and versioned with ComfyUI; it does not become a product API merely by being HTTP. |
| Interactive graph UI | **Core frontend.** Canvas editing, widgets, templates and visual debugging. | UI extensions do not automatically create a server capability; direct client-server interaction is incompatible with API-only execution. |
| Queue and progress | **Core local server.** Prompt submission, an execution queue, history, interruption and WebSocket status/events exist. | This is not a complete multi-tenant scheduler, audit system, quota service or durable job platform. |
| Model / node discovery | **CLI, Manager and ecosystem tooling.** Installation, snapshots and download helpers exist. | Downloading is not admission control: security, model provenance, revision pinning and licence review remain the operator's responsibility. |

The project README is useful for its high-level claims (modular graph, queue,
changed-node re-execution and memory management). Use it only as orientation;
the model and node documentation for the exact graph decides the real support
level. See [official repository README][core-readme].

## 4. Workflows and execution

### 4.1 Two JSON representations, one execution graph

The visual workflow file contains graph/UI information for the editor. The
server accepts the lean **API format**: an object keyed by node ID, where each
node has a `class_type` and an `inputs` object. A value connected from another
node is represented by a pair such as `["4", 0]` (node ID, output index).

```json
{
  "3": {
    "class_type": "KSampler",
    "inputs": { "model": ["4", 0], "seed": 42 }
  }
}
```

The format describes *how to invoke installed node classes*. It does not carry
the code, Python dependencies, model files, licences, custom node revisions or
hardware requirements needed to make the graph executable.

### 4.2 Local server lifecycle

At a high level, a client submits an API-format prompt, receives a prompt ID,
then observes queue/execution events and retrieves output/history. The local
server documentation identifies routes such as `/prompt`, `/ws`, `/history`,
`/queue`, `/interrupt` and `/free`. Exact payloads and available routes must be
read from the documentation and source for the pinned release.

Useful WebSocket event categories include queue status, execution start, node
execution, node progress, node completion, success, error and interruption.
They are suitable for adapting status to another UI; they are not a substitute
for a durable external job record.

### 4.3 Local server versus Comfy Cloud

Comfy Cloud also accepts API-format workflows, but it has its own
authentication, storage, fair scheduling and subscription semantics. Some
Cloud fields are accepted for compatibility but ignored or handled differently.
Do not copy Cloud limits, endpoint behavior or security assumptions into a
self-hosted ComfyUI deployment. Read [Cloud API overview][cloud-overview] and
[Cloud API reference][cloud-api] separately from the [local routes][routes].

## 5. Custom nodes: the extension boundary

The official model distinguishes four useful categories:

| Category | Headless/API suitability |
| --- | --- |
| Server-side only | Normally suitable: Python defines node inputs, outputs and execution. |
| Client-side only | Not useful as an inference capability; it changes only the frontend. |
| Independent client and server | Often suitable if the server node does not require the added UI. Verify both independently. |
| Connected client and server | Not API-compatible when direct client-server communication is required. |

That last row is a hard boundary documented by ComfyUI, not a mere preference.
Before treating a node as a backend component, execute its minimal graph through
the API with no browser open.

Custom-node installation is executable-code installation: a node is cloned or
copied under `custom_nodes/`, and its Python dependencies are installed into
the ComfyUI environment. Some packages also supply install scripts. This is a
software supply-chain boundary, not just an asset import. The official
installation guide explicitly advises using trusted, understood packages.

Manager/CLI convenience is useful for a developer workstation. A repeatable
environment instead records at least:

- ComfyUI repository commit and Python/Torch/CUDA environment;
- custom-node repository URL, immutable revision, dependency lock and licence;
- model/LoRA source URL, exact filename, cryptographic hash and licence;
- workflow API JSON and all exposed parameter values;
- GPU/driver information and the test result.

References: [custom-node overview][node-overview], [custom-node installation][node-install],
[Manager overview][manager-overview], and [Manager publishing/lifecycle][manager-publish].

## 6. Models, LoRAs and filesystem state

Model weight files are normally outside the small ComfyUI application install.
They are placed under `models/` by category (for example checkpoints, LoRAs,
VAEs and ControlNets) and selected by matching loader nodes. ComfyUI can also
be configured with `extra_model_paths.yaml` to share a library across multiple
instances or programs.

Important consequences:

- A workflow is incomplete without its model manifest. A node dropdown value is
  a local filename, not a portable model identity.
- A model family can require several coupled files, and a custom-node project
  may use different directories or loader nodes than Core guidance.
- A LoRA is an auxiliary weight, not a universal style file. The compatible
  base model family and the effective strength/conditioning path must be
  specified and tested.
- ComfyUI Manager and `comfy-cli` can install nodes, manage snapshots and help
  download models, but they do not replace file hashes or legal/security review.

See [official model guidance][models], [Comfy CLI][cli], and [Manager
configuration][manager-config].

## 7. Performance, memory and determinism

ComfyUI's execution engine can cache unchanged portions of a graph between
runs. The core project also provides memory-management techniques, including
model offloading, to make large models viable on smaller GPUs. The exact
algorithm, device placement, attention implementation and available options
depend on the pinned release, PyTorch build, GPU and model family.

Therefore the following are **not guaranteed** by merely choosing ComfyUI:

- a specific VRAM ceiling, throughput, batch size or latency;
- concurrent execution or fair isolation between users;
- deterministic pixels across GPU architectures, PyTorch/CUDA releases,
  attention kernels and model revisions;
- efficient execution for a particular video, audio or third-party model;
- recovery after an out-of-memory error without a caller's retry policy.

Benchmark a full pinned graph, not an isolated sampler screenshot. Record at
least resolution, frames, steps, batch size, seed, GPU, peak allocated/reserved
memory, wall time, models, nodes and output hashes or comparison images.

## 8. What ComfyUI does not provide as a Core promise

1. **A product-domain API.** Its API describes nodes and ports, not an
   application's concepts, permissions, validation rules or product errors.
2. **Universal model compatibility.** Support requires the correct loader,
   graph, auxiliary weights and sometimes a third-party extension.
3. **Universal custom-node compatibility.** UI-coupled nodes cannot be assumed
   to work in API mode, and Python dependencies can conflict.
4. **Tenant isolation or public-service security.** Authentication,
   authorization, quotas, input policy, rate limits, artifact retention and
   audit trails require an explicitly designed surrounding service.
5. **Licence compatibility or legal clearance.** ComfyUI itself is GPL-3.0;
   every custom node, model, LoRA, code dependency and remote API may carry
   independent terms. This is an engineering inventory, not legal advice.
6. **Reproducibility by workflow JSON alone.** JSON omits the full execution
   environment and can invoke a different class behavior after an upgrade.
7. **Quality, identity consistency, safety or semantic correctness of generated
   media.** Those are properties of models, inputs, prompts and evaluation.

## 9. A minimal evaluation protocol for any capability

Use this checklist before saying that a ComfyUI capability is available:

1. Classify it as Core, ecosystem, model-dependent or Cloud-specific.
2. Locate the official docs, exact source repository and licence.
3. Pin the ComfyUI revision, node revision, dependencies and every weight hash.
4. Build a smallest API-format graph and execute it with no frontend involved.
5. Assert the output type, dimensions/frames/audio metadata and failure modes.
6. Measure resources and repeated-run behavior on the intended hardware.
7. Upgrade one dependency only, rerun the same graph and compare results.
8. Mark the result **Verified** only with the environment and evidence link;
   otherwise retain **Not guaranteed**.

## 10. Primary reading index

| Topic | Primary source | Why read it |
| --- | --- | --- |
| Documentation map | [Official docs index][docs-index] | Start here; it lists current official documentation. |
| Workflow model | [Workflow concept][workflow-concept] | Graph terminology, saving and templates. |
| API workflow JSON and jobs | [Cloud overview][cloud-overview] | API-format structure and asynchronous job model; distinguish it from local semantics. |
| Local server communication | [Server overview][server-overview] and [routes][routes] | Local HTTP/WebSocket execution behavior and extension routes. |
| Custom-node categories | [Custom-node overview][node-overview] | Server/client boundary and API incompatibility of directly coupled nodes. |
| Custom-node installation | [Install guide][node-install] | Code/dependency lifecycle and security warning. |
| Models and shared paths | [Models guide][models] | Model categories, loaders, extra model paths and limitations. |
| CLI and snapshots | [Comfy CLI][cli] | Local install/launch/update/node/model management. |
| Manager lifecycle | [Manager overview][manager-overview] and [publishing guide][manager-publish] | Node/model management and install script/dependency behavior. |
| Implementation and licence | [Core repository][core-repo], [server source][server-source], [licence][licence] | Pin an actual release and inspect behavior/licensing before relying on it. |

[docs-index]: https://docs.comfy.org/llms.txt
[workflow-concept]: https://docs.comfy.org/development/core-concepts/workflow
[node-overview]: https://docs.comfy.org/custom-nodes/overview
[server-overview]: https://docs.comfy.org/development/comfyui-server/comms_overview
[routes]: https://docs.comfy.org/development/comfyui-server/comms_routes
[cloud-overview]: https://docs.comfy.org/development/cloud/overview
[cloud-api]: https://docs.comfy.org/development/cloud/api-reference
[node-install]: https://docs.comfy.org/installation/install_custom_node
[models]: https://docs.comfy.org/development/core-concepts/models
[cli]: https://docs.comfy.org/comfy-cli/getting-started
[manager-overview]: https://docs.comfy.org/manager/overview
[manager-config]: https://docs.comfy.org/manager/configuration
[manager-publish]: https://docs.comfy.org/custom-nodes/backend/manager
[core-readme]: https://github.com/Comfy-Org/ComfyUI#readme
[core-repo]: https://github.com/Comfy-Org/ComfyUI
[server-source]: https://github.com/Comfy-Org/ComfyUI/blob/master/server.py
[licence]: https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE
