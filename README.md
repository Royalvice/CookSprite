<div align="center">

# 🍳✨ CookSprite

**Cook up game-ready sprites with AI.** 🎮👾

_Directional clips · sprite sheets · **sprite pairs** (diffuse + normal map) for real dynamic lighting._

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![three.js](https://img.shields.io/badge/three.js-000000?logo=three.js&logoColor=white)](https://threejs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)](./docker)
[![ComfyUI export](https://img.shields.io/badge/ComfyUI-export%20bridge-6E44FF)](./docs/04_COMFYUI_EXPORT.md)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#-contributing)
[![Status: v1 slice](https://img.shields.io/badge/status-v1%20slice-orange.svg)](#-status)

</div>

---

CookSprite is **AI-generation-first**, not a hand-drawing pixel editor. 🎨 Generate, preview, pixel-perfect, and regenerate individual frames — then round-trip to editors like Aseprite via standard PNG sprite sheets when you want to hand-tune. 🖌️

## 🧩 Design in one picture

```text
🧠 Model + Inference  ──[ /infer HTTP API ]──►  atomic op · local OR docker
⚙️  Workflow           ──[ typed component graph ]──►  one minimal function
🖥️  Frontend           ──[ triggers workflows ]──►  Web toolbox (you) + CLI/skill (agents)
```

Four **ABI-decoupled** layers. 🔌 Swap a model, add a workflow, or replace the UI without touching the rest. ComfyUI is supported as an **export target** (workflow → ComfyUI API JSON), not a dependency.

## 💡 Concepts

- 🎯 **Capability** — what you want (e.g. "an 8-direction character").
- 🛣️ **Workflow** — a named route to a capability; one default, others opt-in. You never wire nodes by hand.
- ⚛️ **Op** — an inference atom (`text2img`, `normal-estimate`, …), served by any of several models.
- 🔧 **Tool** — a deterministic, model-free step (pixelize, crop, center, pack).

## 🚀 Quick start

```bash
pip install -e .

# See what CookSprite can do
cspr list

# Cook a sprite (diffuse + normal pair) 🍳
cspr run single_sprite --prompt "a small copper robot" --out ./out

# Export any workflow to ComfyUI API JSON 🔁
cspr export single_sprite --out workflow.json
```

Prefer the web toolbox? 🖥️ `cd web && npm install && npm run dev` — includes a **three.js draggable-light preview** so you can see your normal maps light up in real time. 💡

## 📊 Status

**v1 vertical slice — green.** ✅ Single-prompt → single-sprite pipeline runs end-to-end GPU-free (deterministic stub adapter), with a production vLLM-Omni inference path wired for real model weights. See [`docs/`](./docs) for the full design.

## 🤝 Contributing

PRs welcome! New workflows are just declarative YAML; new Tools/Ops are small typed Python functions. Start with [`docs/03_WORKFLOW.md`](./docs/03_WORKFLOW.md).

## 📜 License

[Apache-2.0](./LICENSE). Cook freely. 🧑‍🍳
