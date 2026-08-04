"""Workflow HTTP server — the web frontend's backend (port 8000).

Endpoints (also mounted under /api so a Vite proxy works with or without a
path rewrite):

    GET  /tasks                   -> tasks + nodes/candidates + param schemas
    POST /run                     -> {run_id}   (async; starts a run thread)
    GET  /runs/{id}               -> status + progress + result-when-done
    GET  /runs/{id}/events        -> SSE stream of the status object
    GET  /runs/{id}/result        -> RunResult
    GET  /artifacts/{id}          -> PNG bytes

Runs execute tasks via the runner using a DirectClient over the backend
adapter router (in-process for v1; point COOKSPRITE_BACKEND_URL at a separate
backend to use HttpClient instead).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import REGISTRY
from .library import Library
from .runner import run_task
from .workspace import Workspace

@dataclass
class Run:
    id: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None


class RunRequest(BaseModel):
    task: str
    params: dict[str, Any] = Field(default_factory=dict)
    choices: dict[str, str] = Field(default_factory=dict)  # node id -> workflow id


def _build_client() -> Any:
    backend_url = os.environ.get("COOKSPRITE_BACKEND_URL")
    if backend_url:
        from .clients import HttpClient

        return HttpClient(backend_url)
    from backend.ops import build_default_router

    from .clients import DirectClient

    return DirectClient(build_default_router())


def _artifact_to_result_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a workspace manifest entry into a web RunResult artifact."""
    # URLs carry the /api prefix: the web calls through a /api proxy, and the
    # router is mounted at /api, so /api/artifacts/{id} always resolves.
    out: dict[str, Any] = {"id": entry["id"], "kind": entry["kind"], "meta": entry.get("meta", {})}
    if "diffuse" in entry:
        out["diffuse_url"] = f"/api/artifacts/{entry['diffuse']}"
    if "normal" in entry:
        out["normal_url"] = f"/api/artifacts/{entry['normal']}"
    if "image" in entry:
        out["url"] = f"/api/artifacts/{entry['image']}"
    for k in ("frames", "frame_w", "frame_h"):
        if k in entry:
            out[k] = entry[k]
    return out


def create_app(workspace_root: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="CookSprite Workflow Server", version="0.1.0")
    library = Library.load_builtin()
    ws_root = Path(workspace_root or os.environ.get("COOKSPRITE_WORKSPACE", "./cooksprite_workspace"))
    workspace = Workspace.init(ws_root)
    client = _build_client()
    runs: dict[str, Run] = {}
    lock = threading.Lock()

    api = APIRouter()

    @api.get("/tasks")
    def tasks() -> dict[str, Any]:
        out = []
        for task in library.tasks():
            schema = {
                name: {
                    "type": decl.get("type", "string"),
                    "default": decl.get("default"),
                    "label": decl.get("label", name),
                }
                for name, decl in task.params.items()
            }
            nodes = [
                {"id": n.id, "candidates": n.candidates, "inputs": n.inputs}
                for n in task.nodes
            ]
            out.append(
                {"id": task.id, "description": task.description,
                 "params_schema": schema, "nodes": nodes}
            )
        return {"tasks": out}

    @api.post("/run")
    def run(req: RunRequest) -> dict[str, str]:
        try:
            task = library.get_task(req.task)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        run_obj = Run(id=uuid.uuid4().hex)
        with lock:
            runs[run_obj.id] = run_obj

        def work() -> None:
            run_obj.status = "running"

            def on_progress(fraction: float, message: str) -> None:
                run_obj.progress = fraction
                run_obj.message = message

            try:
                artifact = run_task(
                    task,
                    library.workflow_map(),
                    REGISTRY,
                    client,
                    params=req.params,
                    choices=req.choices,
                    config_defaults=workspace.param_defaults(),
                    on_progress=on_progress,
                )
                entry = workspace.save_artifact(artifact)
                result = {"artifacts": [_artifact_to_result_entry(entry)]}
                workspace.record_run(
                    {"run_id": run_obj.id, "task": req.task,
                     "params": req.params, "choices": req.choices, "artifacts": [entry]}
                )
                run_obj.result = result
                run_obj.progress = 1.0
                run_obj.status = "done"
                run_obj.message = "completed"
            except Exception as exc:
                run_obj.status = "error"
                run_obj.error = f"{type(exc).__name__}: {exc}"
                run_obj.message = run_obj.error

        threading.Thread(target=work, daemon=True).start()
        return {"run_id": run_obj.id}

    def _status_obj(run_obj: Run) -> dict[str, Any]:
        body: dict[str, Any] = {
            "run_id": run_obj.id,
            "status": run_obj.status,
            "progress": run_obj.progress,
            "message": run_obj.message,
        }
        if run_obj.status == "done":
            body["result"] = run_obj.result
        return body

    @api.get("/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, Any]:
        run_obj = runs.get(run_id)
        if run_obj is None:
            raise HTTPException(status_code=404, detail="unknown run")
        return _status_obj(run_obj)

    @api.get("/runs/{run_id}/events")
    def run_events(run_id: str) -> StreamingResponse:
        run_obj = runs.get(run_id)
        if run_obj is None:
            raise HTTPException(status_code=404, detail="unknown run")

        def stream():
            while True:
                yield f"data: {json.dumps(_status_obj(run_obj))}\n\n"
                if run_obj.status in ("done", "error"):
                    break
                time.sleep(0.3)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @api.get("/runs/{run_id}/result")
    def run_result(run_id: str) -> dict[str, Any]:
        run_obj = runs.get(run_id)
        if run_obj is None:
            raise HTTPException(status_code=404, detail="unknown run")
        if run_obj.status == "error":
            raise HTTPException(status_code=500, detail=run_obj.error)
        if run_obj.status != "done":
            raise HTTPException(status_code=409, detail=f"run not done: {run_obj.status}")
        return run_obj.result or {"artifacts": []}

    @api.get("/artifacts/{artifact_id}")
    def artifact(artifact_id: str) -> Response:
        try:
            data = workspace.artifact_bytes(artifact_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="unknown artifact") from e
        return Response(content=data, media_type="image/png")

    # Mount at root and under /api so a Vite proxy works either way.
    app.include_router(api)
    app.include_router(api, prefix="/api")
    return app


app = create_app()

