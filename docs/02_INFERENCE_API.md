# 02 · Inference API (Model-Layer ABI)

The backend exposes model capability as a minimal REST API. One unified API
format regardless of where it runs (dev deployment: H20 GPU).

## Engine & orchestration

- **Engine:** vLLM-Omni — one engine covering the target image models (FLUX.2,
  FLUX.1, SD-XL/3.5, Qwen-Image, …) and video models (WAN2.2, LTX-2,
  HunyuanVideo 1.5, …), with built-in online serving.
- **Orchestration:** Ray Serve manages the model pool and swaps heavy models
  in/out of VRAM on demand (a single GPU can't keep them all hot), and scales
  out later without changing this contract.

## Endpoint (async job model)

Generation — especially video — is a long task, so `/infer` is asynchronous:

```text
POST /infer
{
  "op":       "text2img",        // atomic operation
  "model_id": "<selectable>",    // one op ← many models
  "inputs":   { ... },           // op-specific data (images, masks, refs, text)
  "params":   { ... }            // op-specific knobs (steps, seed, size, …)
}
→ { "job_id": "..." }            // returns immediately

GET  /jobs/{job_id}              // → status + progress (poll or subscribe)
GET  /jobs/{job_id}/result       // → { "outputs": [...], "meta": {...} }
```

Progress may also be pushed (websocket/SSE) so the frontend can show a live
bar for multi-minute video jobs.

## Ops (atomic model capabilities)

Each `op` is one atomic thing a model can do. Initial set (extensible):

| op | inputs | outputs |
|---|---|---|
| `text2img` | prompt, params | image(s) |
| `img2img` | image, prompt, params | image(s) |
| `img2vid` | image, params | video / frame sequence |
| `frame_extract` | video, params | frame sequence |
| `normal_estimate` | diffuse image | normal map |
| `upscale` | image, params | image |

Sprite-specific multi-inputs (control / normal / mask / reference) are
first-class members of `inputs`, not add-ons.

## Adapters

A model adapter implements `(op, model_id) → result`. Adding a model = adding an
adapter that registers the ops it supports; the HTTP contract never changes.

## Local vs docker

Identical contract. Local = the server runs in-process on your machine; docker
= the same server behind a container port. Callers (workflows, CLI) do not care
which.

## Errors

Missing model, unsupported op, or bad inputs return an explicit error. There is
no silent fallback to another model or a stub result.
