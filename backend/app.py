"""Inference backend FastAPI app — the model-layer ABI (async job model).

    POST /infer            {op, model_id, inputs, params} -> {job_id}
    GET  /jobs/{id}        -> status + progress
    GET  /jobs/{id}/result -> {outputs, meta}
    GET  /models           -> served (model_id, ops, adapter)
    GET  /healthz

One unified API regardless of machine. Dev uses the stub adapter; setting
COOKSPRITE_VLLM_URL routes supported models to vLLM-Omni on the H20.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .jobs import JobStore
from .ops import AdapterRouter, build_default_router


class InferRequest(BaseModel):
    op: str
    model_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


def create_app(router: AdapterRouter | None = None) -> FastAPI:
    app = FastAPI(title="CookSprite Inference Backend", version="0.1.0")
    app.state.router = router or build_default_router()
    app.state.jobs = JobStore()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/models")
    def models() -> dict[str, Any]:
        return {"models": app.state.router.models()}

    @app.post("/infer")
    def infer(req: InferRequest) -> dict[str, str]:
        router: AdapterRouter = app.state.router
        # Fail fast if unroutable, before creating a job.
        try:
            router.route(req.op, req.model_id)
        except LookupError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        def work(job) -> dict[str, Any]:
            job.message = f"running {req.op} on {req.model_id}"
            return router.run(req.op, req.model_id, req.inputs, req.params)

        job = app.state.jobs.submit(work)
        return {"job_id": job.id}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return {
            "job_id": job.id,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
            "error": job.error,
        }

    @app.get("/jobs/{job_id}/result")
    def job_result(job_id: str) -> dict[str, Any]:
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        if job.status == "error":
            raise HTTPException(status_code=500, detail=job.error)
        if job.status != "done":
            raise HTTPException(status_code=409, detail=f"job not done: {job.status}")
        return job.result or {"outputs": [], "meta": {}}

    return app


app = create_app()
