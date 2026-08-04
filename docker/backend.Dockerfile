# CookSprite inference backend.
#
# The default image runs the FastAPI /infer server with the deterministic stub
# adapter (CPU, no GPU needed) — useful for CI and local integration.
#
# For the real H20 deployment, install the `serve` extra and point
# COOKSPRITE_VLLM_URL at a running vLLM-Omni endpoint; Ray Serve fronts the
# model pool. That layer is deployment-specific and lives on the GPU host.
FROM python:3.11-slim

WORKDIR /app

# System deps for Pillow/numpy wheels are already covered by slim + wheels.
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY workflow ./workflow

RUN pip install --no-cache-dir . && pip install --no-cache-dir "uvicorn[standard]"

ENV COOKSPRITE_HOST=0.0.0.0 \
    COOKSPRITE_PORT=8188

EXPOSE 8188

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8188"]
