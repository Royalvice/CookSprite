# CookSprite

CookSprite is a local-first open-source AI sprite studio. Every Git clone has
the same capabilities: it may run Web, API, CLI, or a managed ComfyUI Runtime.
Projects and Artifacts live with the selected CookSprite API data directory;
inference runs in the ComfyUI Runtime selected by that API.

## Install

```bash
git clone https://github.com/Royalvice/CookSprite.git
cd CookSprite
uv sync --extra dev
```

Source clones synchronize only through their shared Git remote:

```bash
git pull --ff-only
git push
```

## Start CookSprite

```bash
uv run cspr service start --data-dir ~/.cooksprite/data
```

For a ComfyUI Runtime on another computer, expose an API callback URL reachable
from that Runtime:

```bash
uv run cspr service start \
  --data-dir ~/.cooksprite/data \
  --public-api-url https://api.example.test/api/v1
```

`cspr service start` starts the product API and packaged Web UI in the background. It never installs,
starts, stops, or updates ComfyUI.

## Connect ComfyUI

```bash
uv run cspr comfy connect import \
  --label ComfyUI \
  --url http://127.0.0.1:8188 \
  --location local

uv run cspr comfy inspect doctor --runtime <runtime-id>
uv run cspr comfy connect select --runtime <runtime-id>
```

For a remote Runtime, use `--location remote` and provide `--callback-url`.

CookSprite can optionally create and manage a dedicated ComfyUI Runtime:

```bash
uv run cspr comfy worker init --runtime-dir ../worker-runtime --device auto
uv run cspr comfy worker install --runtime-dir ../worker-runtime
uv run cspr comfy worker start --runtime-dir ../worker-runtime
uv run cspr comfy worker doctor --runtime-dir ../worker-runtime --json
```

The default device policy is shared. Optional CUDA exclusivity is explicit:

```bash
uv run cspr comfy worker init \
  --runtime-dir ../worker-runtime \
  --device cuda:0 \
  --exclusive
```

## Use the CLI

```bash
uv run cspr action
uv run cspr project create --name "Forest mage" --type character
uv run cspr artifact upload hero.png --project <project-id>
uv run cspr action run image.generate \
  --project <project-id> \
  --value prompt="tiny forest mage" \
  --wait
uv run cspr project export <project-id> --wait
```

## Development

```bash
uv run cspr dev package sync
uv run cspr dev package lock
uv run cspr dev check
ruff check .
pytest -q
cd web && npm ci && npm run build && npm test
```

See [architecture](docs/01_ARCHITECTURE.md),
[API](docs/02_INFERENCE_API.md),
[managed worker](docs/07_MANAGED_WORKER.md), and
[dependencies](docs/06_DEPENDENCY_ENVIRONMENT.md).
