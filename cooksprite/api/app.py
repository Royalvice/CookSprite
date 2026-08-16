"""CookSprite /api/v1: stable Actions over a private ComfyUI runtime."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.responses import Response as BinaryResponse
from pydantic import BaseModel

from .. import __version__
from ..action_graphs import bind_action_task, materialize_recipe_workflows, sealed_tool_descriptor
from ..bridge import ArtifactBridge, BridgeError
from ..comfy import ComfyClient
from ..comfy.managed import DEFAULT_MODEL, wait_until_ready
from ..comfy.managed import install as install_managed_comfy
from ..comfy.managed import launch as launch_managed_comfy
from ..compiler import CompileError, Compiler
from ..domain import (
    ActionDescriptor,
    ActionRunCreate,
    ArtifactPatch,
    ArtifactRef,
    DocumentView,
    FrameSequenceManifest,
    FrameSequenceView,
    GalleryItem,
    ProjectCreate,
    ProjectExportCreate,
    ProjectPatch,
    ProjectView,
    RunCreate,
    RunView,
    SpriteDocument,
    TaskDefinition,
    TaskRevision,
    ToolDescriptor,
    TrackSequenceCreate,
    WorkflowDefinition,
    WorkflowRevision,
)
from ..example_catalog import register_action_examples
from ..execution import ExecutionPlan
from ..package import PackageError, build_package
from ..recipes import (
    Recipe,
    discover_recipes,
    imported_recipe_is_compatible,
    manifest_from_assets,
    recipe_for,
    recipes_from_runtime,
    runtime_manifest,
    supports,
)
from ..registry import ACTION_IDS, CookSpriteRegistry, RegistryError
from ..store import DocumentConflict, Store, utcnow
from ..supervisor import RunSupervisor
from ..tool_packages import tool_packages

SEQUENCE_ACTIONS = {"animation.generate", "sheet.slice", "video.sample"}
TEST_RUNTIME_VERSIONS = {"test", "demo-test", "cooksprite-test-runtime"}


class RuntimeCreate(BaseModel):
    id: str
    label: str
    base_url: str
    callback_url: str | None = None


class RecipeCreate(BaseModel):
    id: str
    label: str
    family: str = "comfy.imported"
    actions: list[str]
    modes: list[str]
    workflow: dict[str, Any]
    slots: dict[str, str]
    output: list[Any]
    checkpoint: str | None = None


class LocalSetupCreate(BaseModel):
    directory: str | None = None
    with_models: bool = True
    host: str = "127.0.0.1"
    port: int = 8188


class PublishCreate(BaseModel):
    cover_artifact_id: str | None = None


def _snapshot(info: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(info, sort_keys=True, default=str).encode()).hexdigest()


def _detail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


def _dynamic_tools(raw: list[tuple[str, dict[str, Any]]]) -> list[ToolDescriptor]:
    result = []
    for name, spec in raw:
        required = spec.get("input", {}).get("required", {}) if isinstance(spec, dict) else {}
        outputs = spec.get("output", []) if isinstance(spec, dict) else []

        def port_type(value: Any) -> str:
            raw_type = str(value[0] if isinstance(value, list) and value else value).upper()
            return {
                "IMAGE": "Image",
                "MASK": "Mask",
                "STRING": "Text",
                "INT": "Number",
                "FLOAT": "Number",
                "BOOLEAN": "Boolean",
                "MODEL": "MODEL",
                "CLIP": "CLIP",
                "VAE": "VAE",
                "LATENT": "LATENT",
                "CONDITIONING": "CONDITIONING",
            }.get(raw_type, "Text")

        result.append(
            ToolDescriptor(
                id=f"comfy.{name}",
                source="comfy",
                title=str(name),
                inputs=[
                    {
                        "name": key,
                        "type": port_type(value),
                        "required": True,
                    }
                    for key, value in required.items()
                ],
                outputs=[
                    {
                        "name": f"output_{index}",
                        "type": port_type(value),
                        "required": True,
                        "persistable": False,
                    }
                    for index, value in enumerate(outputs)
                ],
            )
        )
    return result


def create_app(
    data_dir: str | Path = "data",
    comfy_factory: type[ComfyClient] = ComfyClient,
    registry_path: str | Path | None = None,
    allow_test_runtime: bool | None = None,
) -> FastAPI:
    app = FastAPI(
        title="CookSprite API",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )
    data_path = Path(data_dir).expanduser().resolve()
    store = Store(data_path)
    registry = CookSpriteRegistry(registry_path)
    registry.set_examples(register_action_examples(store))
    app.state.store = store
    app.state.registry = registry
    app.state.comfy_factory = comfy_factory
    app.state.allow_test_runtime = (
        os.environ.get("COOKSPRITE_ALLOW_TEST_RUNTIME") == "1"
        if allow_test_runtime is None
        else allow_test_runtime
    )
    default_callback_url = os.environ.get(
        "COOKSPRITE_PUBLIC_API_URL", "http://127.0.0.1:8000/api/v1"
    ).rstrip("/")
    bridge_secret = ArtifactBridge.from_data_dir(data_path, default_callback_url).secret
    setup_lock = threading.RLock()
    default_managed_root = (Path.home() / ".cooksprite" / "runtime").resolve()
    managed_install_present = (default_managed_root / "install.json").is_file() and (
        default_managed_root / "ComfyUI" / "main.py"
    ).is_file()
    setup_state: dict[str, Any] = {
        "status": "installed" if managed_install_present else "idle",
        "progress": 1.0 if managed_install_present else 0.0,
        "message": "managed ComfyUI is installed" if managed_install_present else "",
        "error": None,
        "directory": str(default_managed_root) if managed_install_present else None,
    }
    runtime_cache: dict[str, dict[str, Any]] = {}
    runtime_cache_lock = threading.RLock()

    def runtime_assets(runtime: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not runtime:
            return []
        try:
            value = json.loads(runtime.get("assets") or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    def bridge_for(runtime: dict[str, Any] | None) -> ArtifactBridge:
        manifest = manifest_from_assets(runtime_assets(runtime))
        callback_url = manifest.get("callback_url") or default_callback_url
        return ArtifactBridge(bridge_secret, callback_url)

    def callback_for(runtime: dict[str, Any] | None) -> str:
        manifest = manifest_from_assets(runtime_assets(runtime))
        return str(manifest.get("callback_url") or default_callback_url).rstrip("/")

    def persist_runtime_report(
        runtime: dict[str, Any],
        report: dict[str, Any],
        *,
        is_test_runtime: bool = False,
    ) -> tuple[str, list[ToolDescriptor], list[Recipe]]:
        dynamic = _dynamic_tools(list((report.get("object_info") or {}).items()))
        snapshot = _snapshot(report)
        discovered = [
            materialize_recipe_workflows(store, runtime["id"], snapshot, recipe)
            for recipe in discover_recipes(report)
        ]
        imported = [
            materialize_recipe_workflows(store, runtime["id"], snapshot, recipe)
            for recipe in recipes_from_runtime(runtime)
            if imported_recipe_is_compatible(recipe, report)
        ]
        recipes = [*discovered, *imported]
        assets: list[dict[str, Any]] = [
            runtime_manifest(report, recipes, callback_url=callback_for(runtime))
        ]
        if is_test_runtime:
            assets.append({"cooksprite_test_runtime": True})
        store.put_runtime(
            runtime["id"],
            runtime["label"],
            runtime["base_url"],
            snapshot,
            [item.model_dump(mode="json") for item in dynamic],
            assets,
        )
        invalidate_runtime(runtime["id"])
        return snapshot, dynamic, recipes

    def is_stored_test_runtime(runtime: dict[str, Any]) -> bool:
        try:
            assets = json.loads(runtime.get("assets") or "[]")
        except (TypeError, json.JSONDecodeError):
            return False
        return any(
            isinstance(item, dict) and item.get("cooksprite_test_runtime") is True
            for item in assets
        )

    def invalidate_runtime(runtime_id: str | None = None) -> None:
        with runtime_cache_lock:
            if runtime_id:
                runtime_cache.pop(runtime_id, None)
            else:
                runtime_cache.clear()

    supervisor = RunSupervisor(store, app.state.comfy_factory, invalidate_runtime)
    app.state.supervisor = supervisor
    app.router.add_event_handler("shutdown", supervisor.close)

    def probe_runtime(runtime: dict[str, Any] | None, *, force: bool = False) -> dict[str, Any]:
        checked_at = utcnow()
        if not runtime:
            return {
                "status": "unconfigured",
                "runtime_id": None,
                "checked_at": checked_at,
                "error": None,
            }
        if not runtime.get("snapshot"):
            return {
                "status": "offline",
                "runtime_id": runtime["id"],
                "checked_at": checked_at,
                "error": "Runtime has not passed capability validation",
            }
        if is_stored_test_runtime(runtime) and not app.state.allow_test_runtime:
            return {
                "status": "offline",
                "runtime_id": runtime["id"],
                "checked_at": checked_at,
                "error": "Fake Runtime is disabled outside an explicit test process",
            }
        with runtime_cache_lock:
            cached = runtime_cache.get(runtime["id"])
            if cached and not force and time.monotonic() - cached["monotonic"] < 1:
                return {key: cached[key] for key in ("status", "runtime_id", "checked_at", "error")}
        try:
            client = app.state.comfy_factory(runtime["base_url"])
            ping = getattr(client, "ping", None)
            if callable(ping):
                ping()
            else:
                client.queue()
            if not recipes_from_runtime(runtime):
                result = {
                    "status": "offline",
                    "runtime_id": runtime["id"],
                    "checked_at": checked_at,
                    "error": "ComfyUI is online, but no compatible model/workflow recipe is installed",
                    "monotonic": time.monotonic(),
                }
            else:
                result = {
                    "status": "ready",
                    "runtime_id": runtime["id"],
                    "checked_at": checked_at,
                    "error": None,
                    "monotonic": time.monotonic(),
                }
        except Exception as exc:  # noqa: BLE001 - normalize the runtime boundary.
            result = {
                "status": "offline",
                "runtime_id": runtime["id"],
                "checked_at": checked_at,
                "error": str(exc) or exc.__class__.__name__,
                "monotonic": time.monotonic(),
            }
        with runtime_cache_lock:
            runtime_cache[runtime["id"]] = result
        return {key: result[key] for key in ("status", "runtime_id", "checked_at", "error")}

    def live_runtime(runtime_id: str | None = None) -> dict[str, Any] | None:
        runtime = store.active_runtime(runtime_id)
        return runtime if probe_runtime(runtime)["status"] == "ready" else None

    def runtime_or_404(runtime_id: str) -> dict[str, Any]:
        runtime = store.runtime(runtime_id)
        if not runtime:
            raise HTTPException(
                404,
                _detail("runtime_not_found", f"unknown runtime {runtime_id}"),
            )
        return runtime

    def runtime_tools(runtime: dict[str, Any]) -> list[ToolDescriptor]:
        dynamic = [
            ToolDescriptor.model_validate(item) for item in json.loads(runtime["tools"] or "[]")
        ]
        sealed = [
            descriptor
            for recipe in recipes_from_runtime(runtime)
            if (descriptor := sealed_tool_descriptor(recipe)) is not None
        ]
        return registry.tools() + dynamic + sealed

    def sealed_graphs(runtime: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            f"comfy.sealed.{recipe.id}": recipe.dump()
            for recipe in recipes_from_runtime(runtime)
            if recipe.source == "imported" and recipe.workflow
        }

    def runtime_tool_ids(runtime: dict[str, Any] | None) -> set[str]:
        if not runtime:
            return set()
        return {
            item["id"]
            for item in json.loads(runtime.get("tools") or "[]")
            if isinstance(item, dict) and item.get("id")
        }

    def assert_runtime(runtime_id: str, snapshot: str) -> dict[str, Any]:
        runtime = runtime_or_404(runtime_id)
        if not runtime["snapshot"] or runtime["snapshot"] != snapshot:
            raise HTTPException(
                409,
                _detail(
                    "runtime_snapshot_incompatible",
                    "definition needs revalidation on this runtime",
                ),
            )
        state = probe_runtime(runtime)
        if state["status"] != "ready":
            raise HTTPException(
                409,
                _detail("runtime_offline", state["error"] or "ComfyUI is offline"),
            )
        return runtime

    def workflow_from(row: dict[str, Any]) -> WorkflowRevision:
        return WorkflowRevision.model_validate(
            {
                **json.loads(row["body"]),
                "revision": row["revision"],
                "runtime_snapshot": row["snapshot"],
            }
        )

    def task_from(row: dict[str, Any]) -> TaskRevision:
        return TaskRevision.model_validate(
            {
                **json.loads(row["body"]),
                "revision": row["revision"],
                "runtime_snapshot": row["snapshot"],
            }
        )

    def run_view(run_id: str) -> RunView:
        row = store.run(run_id)
        if not row:
            raise HTTPException(404, _detail("run_not_found", "unknown run"))
        artifacts = []
        for artifact_id in json.loads(row["artifacts"] or "[]"):
            artifact = store.artifact(artifact_id)
            if artifact:
                artifacts.append(store.artifact_ref(artifact, row.get("project_id")))
        return RunView(
            id=row["id"],
            status=row["status"],
            progress=row["progress"],
            message=row["message"],
            action_id=row.get("action_id"),
            project_id=row.get("project_id"),
            artifacts=artifacts,
            error=json.loads(row["error"]) if row["error"] else None,
            provenance=json.loads(row.get("provenance") or "{}"),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )

    def execute_plan(
        run_id: str,
        runtime: dict[str, Any],
        plan: ExecutionPlan,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        """Submit every Action, Workflow, and Task through one runtime path."""
        supervisor.submit_plan(run_id, runtime, plan, on_complete)

    def frame_sequence_view(artifact_id: str) -> FrameSequenceView:
        row = store.artifact(artifact_id)
        if not row:
            raise HTTPException(404, _detail("artifact_not_found", "unknown artifact"))
        if row["kind"] != "FrameSeq":
            raise HTTPException(
                422,
                _detail("artifact_type_mismatch", "artifact is not a FrameSeq"),
            )
        try:
            manifest = FrameSequenceManifest.model_validate_json(store.artifact_bytes(artifact_id))
        except Exception as exc:
            raise HTTPException(
                422,
                _detail("invalid_frame_sequence", "FrameSeq manifest is invalid"),
            ) from exc
        meta = json.loads(row.get("meta") or "{}")
        sequence_run = store.run(meta.get("run_id")) if meta.get("run_id") else None
        project_id = sequence_run.get("project_id") if sequence_run else None
        frames: list[ArtifactRef] = []
        for frame_id in manifest.frames:
            frame = store.artifact(frame_id)
            if not frame or frame["kind"] != "Image":
                raise HTTPException(
                    422,
                    _detail(
                        "invalid_frame_sequence",
                        f"FrameSeq references a missing Image: {frame_id}",
                    ),
                )
            frames.append(store.artifact_ref(frame, project_id))
        return FrameSequenceView(
            artifact=store.artifact_ref(row, project_id),
            sequence=manifest,
            frames=frames,
        )

    def target_value(values: dict[str, Any], key: str) -> str | None:
        raw = values.get(key)
        if raw is None and key == "direction":
            raw = values.get("directions")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], str):
            return raw[0]
        return None

    def finalize_frame_sequence(
        run_id: str,
        action_id: str,
        project_id: str,
        values: dict[str, Any],
    ) -> ArtifactRef:
        record = store.run(run_id)
        frame_ids = json.loads(record.get("artifacts") or "[]") if record else []
        frames = [
            frame_id
            for frame_id in frame_ids
            if (store.artifact(frame_id) or {}).get("kind") == "Image"
        ]
        if not frames:
            raise RuntimeError("ComfyUI completed without producing sequence frames")
        target_action = target_value(values, "action")
        view = target_value(values, "view")
        direction = target_value(values, "direction")
        manifest = FrameSequenceManifest(
            action=target_action,
            view=view,
            direction=direction,
            frames=frames,
        )
        payload = manifest.model_dump_json(by_alias=True, exclude_none=False).encode()
        sequence = store.put_artifact(
            payload,
            "application/vnd.cooksprite.frame-sequence+json",
            "FrameSeq",
            {
                "role": "frame_sequence",
                "run_id": run_id,
                "action_id": action_id,
                "frame_count": len(frames),
                "cover_artifact": frames[0],
                "action": target_action,
                "view": view,
                "direction": direction,
                "needs_target": not all((target_action, view, direction)),
                "source_artifacts": frames,
            },
            project_id=project_id,
            title=f"{(target_action or 'Imported').upper()} · {(view or '?').upper()} · {(direction or '?').upper()}",
        )
        store.set_run_artifacts(run_id, [sequence.id])
        return sequence

    def order_normal_outputs(run_id: str, source_ids: list[str]) -> None:
        record = store.run(run_id)
        output_ids = json.loads(record.get("artifacts") or "[]") if record else []
        by_source: dict[str, list[str]] = {}
        for artifact_id in output_ids:
            row = store.artifact(artifact_id)
            if not row or row.get("kind") != "NormalMap":
                continue
            meta = json.loads(row.get("meta") or "{}")
            sources = meta.get("source_artifacts") or []
            if sources:
                by_source.setdefault(str(sources[0]), []).append(artifact_id)
        ordered = [
            by_source[source_id].pop(0) for source_id in source_ids if by_source.get(source_id)
        ]
        if len(ordered) != len(source_ids):
            raise RuntimeError("ComfyUI normal outputs do not match the requested source frames")
        store.set_run_artifacts(run_id, ordered)

    def selected_runtime(values: dict[str, Any]) -> dict[str, Any] | None:
        runtime_id = values.get("runtime")
        if not runtime_id and isinstance(values.get("model"), str):
            runtime_id = values["model"].split(":", 1)[0]
        return live_runtime(runtime_id)

    def ensure_action_project_type(
        project: ProjectView, action_id: str, values: dict[str, Any]
    ) -> ProjectView:
        """Apply Action project semantics once for Web, CLI, and agents."""

        target_type: str | None = None
        if action_id == "animation.generate" and project.type != "character":
            target_type = "character"
        elif (
            action_id == "image.generate"
            and values.get("category") == "terrain"
            and project.type == "static"
        ):
            target_type = "tileset"
        if not target_type:
            return project
        current = store.document(project.id)
        if not current:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        body = dict(current["document"])
        body["type"] = target_type
        document = SpriteDocument.model_validate(body)
        document.history.append({"operation": f"action_convert_to_{target_type}", "at": utcnow()})
        try:
            store.put_document(
                project.id,
                document.model_dump(mode="json", by_alias=True),
                current["etag"],
            )
        except DocumentConflict as exc:
            raise HTTPException(
                409,
                _detail(
                    "document_conflict",
                    "the project changed while preparing the Action",
                ),
            ) from exc
        changed = store.patch_project(project.id, {"type": target_type})
        if not changed:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        return changed

    def validate_artifact_inputs(
        action: ActionDescriptor, inputs: dict[str, str | list[str]]
    ) -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {}
        for slot, supplied in inputs.items():
            artifact_ids = supplied if isinstance(supplied, list) else [supplied]
            normalized[slot] = artifact_ids
            declared = action.accepts[slot].type
            expected = declared if isinstance(declared, list) else [declared]
            for artifact_id in artifact_ids:
                artifact = store.artifact(artifact_id)
                if not artifact or artifact.get("trashed"):
                    raise HTTPException(
                        422,
                        _detail(
                            "artifact_not_available",
                            f"input artifact {artifact_id} is missing or in trash",
                            slot=slot,
                        ),
                    )
                if artifact["kind"] not in expected:
                    raise HTTPException(
                        422,
                        _detail(
                            "artifact_type_mismatch",
                            f"{slot} accepts {' | '.join(expected)}, got {artifact['kind']}",
                            slot=slot,
                            artifact=artifact_id,
                        ),
                    )
        return normalized

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        configured = store.runtimes()
        runtime = store.active_runtime() or (configured[0] if configured else None)
        state = probe_runtime(runtime)
        runtime_recipe_list = recipes_from_runtime(runtime) if state["status"] == "ready" else []
        action_states = {
            item.id: {
                "available": item.available,
                "models": len(item.models),
                "reason": item.unavailable_reason,
            }
            for item in registry.list(
                runtime if state["status"] == "ready" else None,
                runtime_tool_ids(runtime),
                runtime_recipe_list,
            )
        }
        return {
            "service": "cooksprite",
            "executor": "comfyui",
            "runtime": state["status"],
            "runtime_id": state["runtime_id"],
            "checked_at": state["checked_at"],
            "error": state["error"],
            "actions": action_states,
            "schema_version": 4,
        }

    # Stable human/CLI/agent entry surface.
    @app.get(
        "/api/v1/actions",
        response_model=list[ActionDescriptor],
        response_model_exclude_none=True,
    )
    def actions() -> list[ActionDescriptor]:
        runtime = live_runtime()
        return registry.list(runtime, runtime_tool_ids(runtime), recipes_from_runtime(runtime))

    @app.get(
        "/api/v1/actions/{action_id}",
        response_model=ActionDescriptor,
        response_model_exclude_none=True,
    )
    def action(action_id: str) -> ActionDescriptor:
        runtime = live_runtime()
        descriptor = registry.view(
            action_id, runtime, runtime_tool_ids(runtime), recipes_from_runtime(runtime)
        )
        if not descriptor:
            raise HTTPException(404, _detail("action_not_found", "unknown Action"))
        return descriptor

    @app.post("/api/v1/actions/{action_id}/runs", response_model=RunView, status_code=202)
    def start_action_run(action_id: str, request: ActionRunCreate) -> RunView:
        project = store.project(request.project)
        if not project:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        registered = registry.get(action_id)
        if not registered:
            raise HTTPException(404, _detail("action_not_found", "unknown Action"))
        values = {**registry.defaults(registered), **request.values}
        try:
            registry.validate_request(registered, request.inputs, values)
        except RegistryError as exc:
            raise HTTPException(422, _detail("action_request_invalid", str(exc))) from exc
        normalized_inputs = validate_artifact_inputs(registered, request.inputs)
        if action_id == "normal.generate":
            expanded: list[str] = []
            for artifact_id in normalized_inputs.get("source", []):
                row = store.artifact(artifact_id)
                if row and row["kind"] == "FrameSeq":
                    expanded.extend(frame_sequence_view(artifact_id).sequence.frames)
                else:
                    expanded.append(artifact_id)
            normalized_inputs["source"] = expanded
        runtime = selected_runtime(values)
        descriptor = registry.view(
            action_id, runtime, runtime_tool_ids(runtime), recipes_from_runtime(runtime)
        )
        if not descriptor or not descriptor.available:
            raise HTTPException(
                409,
                _detail(
                    descriptor.unavailable_reason if descriptor else "runtime_not_ready",
                    "the selected Action is not available on a healthy runtime",
                ),
            )
        if not values.get("model") and descriptor.models:
            values["model"] = descriptor.models[0].id
        selected_model = str(values.get("model") or "")
        prefix, separator, recipe_id = selected_model.partition(":")
        if not separator or prefix != runtime["id"]:
            raise HTTPException(
                422,
                _detail(
                    "recipe_invalid",
                    "the selected model does not belong to the selected runtime",
                ),
            )
        selected_recipe = recipe_for(runtime, recipe_id)
        if not selected_recipe or not supports(selected_recipe, action_id, normalized_inputs):
            raise HTTPException(
                409,
                _detail(
                    "recipe_incompatible",
                    "the selected model/workflow does not support these text/image inputs",
                ),
            )
        project = ensure_action_project_type(project, action_id, values)
        run_id = f"run_{uuid.uuid4().hex}"
        payload = {
            "project": request.project,
            "inputs": normalized_inputs,
            "values": values,
        }
        try:
            task_revision, workflow_revisions, task_inputs = bind_action_task(
                store,
                runtime["id"],
                runtime["snapshot"],
                selected_recipe,
                action_id,
                normalized_inputs,
                values,
            )
            compiled = Compiler(
                runtime_tools(runtime),
                bridge_for(runtime),
                run_id,
                sealed_graphs=sealed_graphs(runtime),
            ).compile_task(
                task_revision,
                workflow_revisions,
                task_inputs,
                {},
            )
        except (CompileError, ValueError) as exc:
            raise HTTPException(422, _detail("graph_invalid", str(exc))) from exc
        provenance = {
            "action": action_id,
            "recipe": selected_recipe.id,
            "task": {"id": task_revision.id, "revision": task_revision.revision},
            "workflows": [
                {"id": workflow.id, "revision": workflow.revision}
                for workflow in workflow_revisions.values()
            ],
            "packages": tool_packages.versions(),
            "runtime_snapshot": runtime["snapshot"],
        }
        store.create_run(
            run_id,
            runtime["id"],
            action_id=action_id,
            project_id=request.project,
            request=payload,
            provenance=provenance,
        )

        def finalize_action() -> None:
            if action_id in SEQUENCE_ACTIONS:
                finalize_frame_sequence(run_id, action_id, request.project, values)
            elif action_id == "normal.generate":
                order_normal_outputs(run_id, normalized_inputs.get("source", []))

        execute_plan(run_id, runtime, compiled, finalize_action)
        return run_view(run_id)

    @app.get("/api/v1/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str) -> RunView:
        return run_view(run_id)

    @app.get("/api/v1/runs/{run_id}/events")
    def events(run_id: str) -> StreamingResponse:
        def stream():
            while True:
                state = run_view(run_id).model_dump(mode="json")
                yield f"data: {json.dumps(state)}\n\n"
                if state["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.35)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunView)
    def cancel_run(run_id: str) -> RunView:
        row = store.run(run_id)
        if not row:
            raise HTTPException(404, _detail("run_not_found", "unknown run"))
        if row["status"] in {"succeeded", "failed", "cancelled"}:
            return run_view(run_id)
        store.update_run(run_id, status="cancel_requested", message="cancelling in ComfyUI")
        if not row.get("prompt_id"):
            store.update_run(run_id, status="cancelled", progress=1, message="cancelled")
            return run_view(run_id)
        runtime = runtime_or_404(row["runtime_id"])
        try:
            app.state.comfy_factory(runtime["base_url"]).cancel(row["prompt_id"])
        except Exception as exc:  # noqa: BLE001 - cancellation is best effort.
            store.update_run(
                run_id,
                message="cancel requested; waiting for ComfyUI",
                error=json.dumps(_detail("comfy_cancel_failed", str(exc))),
            )
        return run_view(run_id)

    @app.post("/api/v1/runs/{run_id}/retry", response_model=RunView, status_code=202)
    def retry_run(run_id: str) -> RunView:
        row = store.run(run_id)
        if not row:
            raise HTTPException(404, _detail("run_not_found", "unknown run"))
        if not row.get("action_id"):
            raise HTTPException(
                422,
                _detail("retry_not_supported", "only Action runs use the stable retry API"),
            )
        return start_action_run(
            row["action_id"], ActionRunCreate.model_validate(json.loads(row["request"]))
        )

    @app.get("/api/v1/queue")
    def queue() -> dict[str, Any]:
        rows = store.runs()
        views = [run_view(row["id"]) for row in rows]
        runtime = live_runtime()
        runtime_queue: dict[str, Any] | None = None
        if runtime:
            try:
                runtime_queue = app.state.comfy_factory(runtime["base_url"]).queue()
            except Exception as exc:  # noqa: BLE001 - queue health must not break the UI.
                runtime_queue = {"unavailable": True, "message": str(exc)}
        return {
            "running": [item for item in views if item.status in {"running", "cancel_requested"}],
            "pending": [item for item in views if item.status == "queued"],
            "history": [
                item for item in views if item.status in {"succeeded", "failed", "cancelled"}
            ],
            "runtime": runtime_queue,
        }

    # Projects and the optimistic-concurrency SpriteDocument.
    @app.post("/api/v1/projects", response_model=ProjectView, status_code=201)
    def create_project(request: ProjectCreate) -> ProjectView:
        return store.create_project(request.name, request.type)

    @app.get("/api/v1/projects", response_model=list[ProjectView])
    def projects() -> list[ProjectView]:
        return store.projects()

    @app.get("/api/v1/projects/{project_id}", response_model=ProjectView)
    def get_project(project_id: str) -> ProjectView:
        project = store.project(project_id)
        if not project:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        return project

    @app.patch("/api/v1/projects/{project_id}", response_model=ProjectView)
    def patch_project(project_id: str, request: ProjectPatch) -> ProjectView:
        project = store.patch_project(project_id, request.model_dump(exclude_none=True))
        if not project:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        return project

    @app.get("/api/v1/projects/{project_id}/document", response_model=DocumentView)
    def get_document(project_id: str, response: Response) -> DocumentView:
        document = store.document(project_id)
        if not document:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        response.headers["ETag"] = f'"{document["etag"]}"'
        return DocumentView.model_validate(document)

    @app.put("/api/v1/projects/{project_id}/document", response_model=DocumentView)
    def put_document(
        project_id: str,
        document: SpriteDocument,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> DocumentView:
        try:
            updated = store.put_document(
                project_id, document.model_dump(mode="json", by_alias=True), if_match
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, _detail("project_not_found", "unknown project")) from exc
        except DocumentConflict as exc:
            current = store.document(project_id)
            raise HTTPException(
                409,
                _detail(
                    "document_conflict",
                    str(exc),
                    current_revision=current["revision"] if current else None,
                    current_etag=current["etag"] if current else None,
                ),
            ) from exc
        response.headers["ETag"] = f'"{updated["etag"]}"'
        return DocumentView.model_validate(updated)

    @app.get("/api/v1/projects/{project_id}/artifacts", response_model=list[ArtifactRef])
    def project_artifacts(project_id: str) -> list[ArtifactRef]:
        if not store.project(project_id):
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        return store.project_artifacts(project_id)

    @app.post(
        "/api/v1/projects/{project_id}/sequences",
        response_model=FrameSequenceView,
        status_code=201,
    )
    def materialize_track_sequence(
        project_id: str, request: TrackSequenceCreate
    ) -> FrameSequenceView:
        """Turn the current curated track into the typed hand-off artifact.

        This operation persists only a small ordered manifest. It performs no
        image processing and therefore stays on the CookSprite control plane.
        """

        current = store.document(project_id)
        if not current:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        document = SpriteDocument.model_validate(current["document"])
        clip = (
            next(
                (item for item in document.character.clips if item.action == request.action),
                None,
            )
            if document.character
            else None
        )
        view = (
            next((item for item in clip.views if item.id == request.view), None) if clip else None
        )
        track = (
            next((item for item in view.tracks if item.direction == request.direction), None)
            if view
            else None
        )
        frame_ids = [item.artifact for item in track.frames] if track else []
        if not frame_ids:
            raise HTTPException(
                422,
                _detail(
                    "track_empty",
                    "the selected animation track has no curated frames",
                ),
            )
        missing = [
            artifact_id
            for artifact_id in frame_ids
            if (store.artifact(artifact_id) or {}).get("kind") != "Image"
        ]
        if missing:
            raise HTTPException(
                422,
                _detail(
                    "track_artifact_invalid",
                    "the animation track references missing or non-Image artifacts",
                    artifacts=missing,
                ),
            )
        manifest = FrameSequenceManifest(
            action=request.action,
            view=request.view,
            direction=request.direction,
            frames=frame_ids,
        )
        payload = manifest.model_dump_json(by_alias=True, exclude_none=False).encode()
        artifact = store.put_artifact(
            payload,
            "application/vnd.cooksprite.frame-sequence+json",
            "FrameSeq",
            {
                "role": "curated_sequence",
                "action_id": "document.track.materialize",
                "frame_count": len(frame_ids),
                "cover_artifact": frame_ids[0],
                "action": request.action,
                "view": request.view,
                "direction": request.direction,
                "source_artifacts": frame_ids,
                "document_revision": current["revision"],
            },
            project_id=project_id,
            title=(
                f"{request.action.upper()} · {request.view.upper()} · "
                f"{request.direction.upper()} · FINAL"
            ),
        )
        return frame_sequence_view(artifact.id)

    @app.post("/api/v1/projects/{project_id}/publish", response_model=ProjectView)
    def publish(project_id: str, request: PublishCreate) -> ProjectView:
        if request.cover_artifact_id and not store.artifact(request.cover_artifact_id):
            raise HTTPException(422, _detail("artifact_not_found", "unknown cover"))
        project = store.publish_project(project_id, request.cover_artifact_id)
        if not project:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        return project

    @app.post(
        "/api/v1/projects/{project_id}/exports",
        response_model=RunView,
        status_code=202,
    )
    def export_project(project_id: str, request: ProjectExportCreate) -> RunView:
        project = store.project(project_id)
        if not project:
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        run_id = f"run_{uuid.uuid4().hex}"
        payload = {"project": project_id, "allow_incomplete": request.allow_incomplete}
        store.create_run(
            run_id,
            None,
            action_id="project.export",
            project_id=project_id,
            request=payload,
            provenance={"operation": "project.export", "packages": tool_packages.versions()},
        )

        def package() -> None:
            store.update_run(
                run_id,
                status="running",
                progress=0.2,
                message="validating package",
            )
            try:
                result = build_package(
                    store,
                    project_id,
                    allow_incomplete=request.allow_incomplete,
                )
                artifact = store.put_artifact(
                    result.data,
                    "application/vnd.cooksprite+zip",
                    "CookSpritePack",
                    {"manifest": result.manifest, "run_id": run_id},
                    project_id=project_id,
                    title=f"{project.name}.cooksprite",
                )
                store.attach_run_artifact(run_id, artifact.id)
                store.update_run(run_id, status="succeeded", progress=1, message="package ready")
            except PackageError as exc:
                store.update_run(
                    run_id,
                    status="failed",
                    message="package is incomplete",
                    error=json.dumps(
                        _detail(
                            "export_incomplete",
                            "fix the listed issues or explicitly allow an incomplete package",
                            issues=exc.issues,
                        )
                    ),
                )

        supervisor.submit_job(run_id, package)
        return run_view(run_id)

    @app.get("/api/v1/gallery", response_model=list[GalleryItem])
    def gallery() -> list[GalleryItem]:
        return [GalleryItem.model_validate(item) for item in store.gallery()]

    # Artifact library. Upload uses the raw request body to stay CLI/agent friendly.
    @app.post("/api/v1/artifacts", response_model=ArtifactRef, status_code=201)
    async def upload_artifact(
        request: Request,
        media_type: str = "image/png",
        kind: str = "Image",
        project_id: str | None = None,
        title: str = "",
    ) -> ArtifactRef:
        if project_id and not store.project(project_id):
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        data = await request.body()
        if not data:
            raise HTTPException(422, _detail("empty_artifact", "artifact request body is empty"))
        return store.put_artifact(data, media_type, kind, project_id=project_id, title=title)

    @app.get("/api/v1/artifacts", response_model=list[ArtifactRef])
    def artifacts(
        project_id: str | None = None,
        kind: str | None = None,
        trashed: bool = False,
        search: str = "",
        include_system: bool = False,
    ) -> list[ArtifactRef]:
        items = store.artifacts(project_id, kind, trashed, search)
        return items if include_system else [item for item in items if not item.meta.get("system")]

    @app.get("/api/v1/artifacts/{artifact_id}", response_model=ArtifactRef)
    def get_artifact(artifact_id: str) -> ArtifactRef:
        row = store.artifact(artifact_id)
        if not row:
            raise HTTPException(404, _detail("artifact_not_found", "unknown artifact"))
        return store.artifact_ref(row)

    @app.get(
        "/api/v1/artifacts/{artifact_id}/sequence",
        response_model=FrameSequenceView,
    )
    def get_frame_sequence(artifact_id: str) -> FrameSequenceView:
        return frame_sequence_view(artifact_id)

    @app.patch("/api/v1/artifacts/{artifact_id}", response_model=ArtifactRef)
    def patch_artifact(artifact_id: str, request: ArtifactPatch) -> ArtifactRef:
        artifact = store.patch_artifact(artifact_id, request.model_dump(exclude_none=True))
        if not artifact:
            raise HTTPException(404, _detail("artifact_not_found", "unknown artifact"))
        return artifact

    @app.get("/api/v1/artifacts/{artifact_id}/content")
    def artifact_content(artifact_id: str) -> BinaryResponse:
        row = store.artifact(artifact_id)
        if not row:
            raise HTTPException(404, _detail("artifact_not_found", "unknown artifact"))
        headers = {
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{row["sha256"]}"',
        }
        if row["kind"] == "CookSpritePack":
            headers["Content-Disposition"] = (
                f'attachment; filename="{row.get("title") or artifact_id}.cooksprite"'
            )
        return BinaryResponse(
            store.artifact_bytes(artifact_id),
            media_type=row["media_type"],
            headers=headers,
        )

    @app.post("/api/v1/artifacts/{artifact_id}/trash", response_model=ArtifactRef)
    def trash_artifact(artifact_id: str) -> ArtifactRef:
        artifact = store.set_artifact_trashed(artifact_id, True)
        if not artifact:
            raise HTTPException(404, _detail("artifact_not_found", "unknown artifact"))
        return artifact

    @app.post("/api/v1/artifacts/{artifact_id}/restore", response_model=ArtifactRef)
    def restore_artifact(artifact_id: str) -> ArtifactRef:
        artifact = store.set_artifact_trashed(artifact_id, False)
        if not artifact:
            raise HTTPException(404, _detail("artifact_not_found", "unknown artifact"))
        return artifact

    @app.get("/api/v1/bridge/artifacts/{artifact_id}")
    def bridge_download(
        artifact_id: str,
        run_id: str,
        expires: int,
        signature: str,
    ) -> BinaryResponse:
        run = store.run(run_id)
        row = store.artifact(artifact_id)
        if not run or not row:
            raise HTTPException(404, _detail("bridge_target_not_found", "unknown run or artifact"))
        runtime = runtime_or_404(run["runtime_id"])
        try:
            bridge_for(runtime).verify_download(artifact_id, run_id, expires, signature)
        except BridgeError as exc:
            raise HTTPException(403, _detail("bridge_signature_invalid", str(exc))) from exc
        requested = json.loads(run.get("request") or "{}").get("inputs", {})

        def artifact_ids(value: Any) -> set[str]:
            if isinstance(value, dict):
                direct = value.get("artifact")
                return ({str(direct)} if direct else set()).union(
                    *(artifact_ids(item) for item in value.values())
                )
            if isinstance(value, list):
                return set().union(*(artifact_ids(item) for item in value))
            if isinstance(value, str) and value.startswith("art_"):
                return {value}
            return set()

        allowed = artifact_ids(requested)
        if artifact_id not in allowed:
            raise HTTPException(
                403,
                _detail("bridge_scope_violation", "artifact is not an input of this run"),
            )
        return BinaryResponse(store.artifact_bytes(artifact_id), media_type=row["media_type"])

    @app.post("/api/v1/bridge/runs/{run_id}/artifacts", response_model=ArtifactRef)
    async def bridge_upload(
        run_id: str,
        request: Request,
        kind: str = "Image",
        source_artifact: str = "",
        expires: int = 0,
        signature: str = "",
        output_index: int | None = None,
    ) -> ArtifactRef:
        run = store.run(run_id)
        if not run:
            raise HTTPException(404, _detail("run_not_found", "unknown run"))
        runtime = runtime_or_404(run["runtime_id"])
        try:
            bridge_for(runtime).verify_upload(run_id, kind, source_artifact, expires, signature)
        except BridgeError as exc:
            raise HTTPException(403, _detail("bridge_signature_invalid", str(exc))) from exc
        if kind not in {"Image", "NormalMap"}:
            raise HTTPException(
                422,
                _detail("artifact_type_invalid", "ComfyUI may persist only typed image outputs"),
            )
        data = await request.body()
        if not data:
            raise HTTPException(422, _detail("empty_artifact", "artifact request body is empty"))
        source_artifacts: list[str] = []
        if source_artifact:
            source_artifacts = [source_artifact]
        else:
            run_request = json.loads(run.get("request") or "{}")
            for supplied in run_request.get("inputs", {}).values():
                source_artifacts.extend(supplied if isinstance(supplied, list) else [supplied])
        artifact = store.put_artifact(
            data,
            "image/png",
            kind,
            {
                "run_id": run_id,
                "action_id": run.get("action_id"),
                "source_artifacts": source_artifacts,
                "output_index": output_index,
            },
            project_id=run.get("project_id"),
        )
        store.attach_run_artifact(
            run_id,
            artifact.id,
            allow_duplicate=run.get("action_id") in SEQUENCE_ACTIONS,
        )
        return artifact

    @app.post("/api/v1/internal/artifacts", response_model=ArtifactRef, include_in_schema=False)
    async def legacy_internal_artifact(
        request: Request,
        run_id: str | None = None,
        media_type: str = "image/png",
        kind: str = "Image",
        source_artifact: str | None = None,
        output_index: int | None = None,
    ) -> ArtifactRef:
        if not app.state.allow_test_runtime:
            raise HTTPException(404, _detail("route_not_found", "legacy bridge is disabled"))
        run = store.run(run_id) if run_id else None
        source_artifacts: list[str] = []
        if source_artifact:
            source_artifacts = [source_artifact]
        elif run:
            for supplied in json.loads(run.get("request") or "{}").get("inputs", {}).values():
                source_artifacts.extend(supplied if isinstance(supplied, list) else [supplied])
        artifact = store.put_artifact(
            await request.body(),
            media_type,
            kind,
            {
                "run_id": run_id,
                "action_id": run.get("action_id") if run else None,
                "source_artifacts": source_artifacts,
                "output_index": output_index,
            },
            project_id=run.get("project_id") if run else None,
        )
        if run_id and run:
            store.attach_run_artifact(run_id, artifact.id, allow_duplicate=True)
        return artifact

    @app.post("/api/v1/artifacts/gc")
    def gc() -> dict[str, int]:
        return {"removed_blobs": store.gc()}

    # Contributor/debug surface. Ordinary Web/CLI/Skill users do not need it.
    @app.get("/api/v1/setup/local")
    def local_setup_status() -> dict[str, Any]:
        with setup_lock:
            state = dict(setup_state)
        if state["status"] in {"installed", "ready"}:
            runtime = store.runtime("local-managed")
            if runtime and probe_runtime(runtime)["status"] == "ready":
                state.update(
                    status="ready",
                    progress=1.0,
                    message="managed ComfyUI is installed and online",
                    error=None,
                )
            else:
                state.update(
                    status="installed",
                    progress=1.0,
                    message="managed ComfyUI is installed; start or reconnect it to run Actions",
                )
        return {
            **state,
            "default_directory": str(default_managed_root),
            "model": DEFAULT_MODEL,
        }

    @app.post("/api/v1/setup/local", status_code=202)
    def local_setup(request: LocalSetupCreate) -> dict[str, Any]:
        with setup_lock:
            if setup_state["status"] in {"installing", "starting", "validating"}:
                raise HTTPException(
                    409, _detail("setup_in_progress", "local setup is already running")
                )
            root = Path(request.directory or default_managed_root).expanduser().resolve()
            setup_state.update(
                status="installing",
                progress=0.0,
                message="preparing isolated ComfyUI",
                error=None,
                directory=str(root),
            )

        def progress(message: str, value: float) -> None:
            with setup_lock:
                setup_state.update(status="installing", message=message, progress=value)

        def setup_worker() -> None:
            runtime_id = "local-managed"
            base_url = f"http://{request.host}:{request.port}"
            try:
                install_managed_comfy(
                    root,
                    with_models=request.with_models,
                    progress=progress,
                )
                with setup_lock:
                    setup_state.update(status="starting", progress=0.92, message="starting ComfyUI")
                try:
                    app.state.comfy_factory(base_url).ping()
                except Exception:  # noqa: BLE001 - any failed heartbeat means start managed runtime.
                    launch_managed_comfy(root, host=request.host, port=request.port)
                report = wait_until_ready(base_url, timeout=300)
                with setup_lock:
                    setup_state.update(
                        status="validating", progress=0.97, message="validating models and nodes"
                    )
                store.put_runtime(
                    runtime_id,
                    "Local ComfyUI",
                    base_url,
                    "",
                    [],
                    [
                        {
                            "schema": "cooksprite.runtime-assets/v1",
                            "callback_url": default_callback_url,
                            "recipes": [],
                            "models": {},
                        }
                    ],
                )
                runtime = runtime_or_404(runtime_id)
                _, _, recipes = persist_runtime_report(runtime, report)
                if not recipes:
                    raise RuntimeError(
                        "ComfyUI started, but no compatible CookSprite recipe was found"
                    )
                with setup_lock:
                    setup_state.update(
                        status="ready",
                        progress=1.0,
                        message=f"ready with {len(recipes)} compatible recipe(s)",
                        error=None,
                    )
            except Exception as exc:  # noqa: BLE001 - normalized setup boundary.
                with setup_lock:
                    setup_state.update(status="failed", message="setup failed", error=str(exc))

        threading.Thread(target=setup_worker, daemon=True).start()
        return local_setup_status()

    @app.post("/api/v1/runtimes")
    def create_runtime(request: RuntimeCreate) -> dict[str, Any]:
        # Re-registering the same endpoint is a normal reconnect operation from
        # the UI. Keep its validated snapshot until the new doctor succeeds so
        # a failed reconnect cannot silently take a previously-ready runtime
        # offline. A changed URL intentionally invalidates the old snapshot.
        existing = store.runtime(request.id)
        same_endpoint = bool(existing and existing.get("base_url") == request.base_url)
        existing_assets = runtime_assets(existing) if same_endpoint else []
        existing_manifest = manifest_from_assets(existing_assets)
        if request.callback_url or existing_manifest:
            manifest = {
                **existing_manifest,
                "schema": existing_manifest.get("schema", "cooksprite.runtime-assets/v1"),
                "callback_url": (request.callback_url or callback_for(existing)).rstrip("/"),
            }
            existing_assets = [
                item
                for item in existing_assets
                if not (isinstance(item, dict) and item.get("schema") == manifest["schema"])
            ]
            existing_assets.insert(0, manifest)
        store.put_runtime(
            request.id,
            request.label,
            request.base_url,
            existing.get("snapshot", "") if same_endpoint and existing else "",
            json.loads(existing.get("tools", "[]")) if same_endpoint and existing else [],
            existing_assets,
        )
        invalidate_runtime(request.id)
        return {
            "id": request.id,
            "label": request.label,
            "base_url": request.base_url,
            "snapshot": existing.get("snapshot") if same_endpoint and existing else None,
        }

    @app.get("/api/v1/runtimes")
    def runtimes() -> list[dict[str, Any]]:
        result = []
        for row in store.runtimes():
            state = probe_runtime(row)
            result.append(
                {
                    **{key: row[key] for key in ("id", "label", "base_url", "snapshot")},
                    "status": state["status"],
                    "error": state["error"],
                    "checked_at": state["checked_at"],
                    "callback_url": callback_for(row),
                    "recipes": [recipe.dump() for recipe in recipes_from_runtime(row)],
                }
            )
        return result

    @app.post("/api/v1/runtimes/{runtime_id}/doctor")
    def doctor(runtime_id: str) -> dict[str, Any]:
        runtime = runtime_or_404(runtime_id)
        try:
            report = app.state.comfy_factory(runtime["base_url"]).doctor()
        except Exception as exc:
            invalidate_runtime(runtime_id)
            raise HTTPException(502, _detail("comfy_unavailable", str(exc))) from exc
        system = report.get("system_stats", {}).get("system", {})
        is_test_runtime = (
            system.get("device") == "protocol-stub"
            or system.get("comfyui_version") in TEST_RUNTIME_VERSIONS
        )
        if is_test_runtime and not app.state.allow_test_runtime:
            invalidate_runtime(runtime_id)
            raise HTTPException(
                422,
                _detail(
                    "test_runtime_not_allowed",
                    "Fake Runtime is allowed only in an explicit test process",
                ),
            )
        snapshot, dynamic, recipes = persist_runtime_report(
            runtime, report, is_test_runtime=is_test_runtime
        )
        return {
            "runtime_id": runtime_id,
            "snapshot": snapshot,
            "tools": [item.model_dump() for item in dynamic],
            "recipes": [recipe.dump() for recipe in recipes],
            "system": report.get("system_stats", {}),
            "models": report.get("models", {}),
        }

    @app.post("/api/v1/runtimes/{runtime_id}/recipes", status_code=201)
    def import_recipe(runtime_id: str, body: RecipeCreate) -> dict[str, Any]:
        runtime = runtime_or_404(runtime_id)
        if not runtime.get("snapshot"):
            raise HTTPException(409, _detail("runtime_not_doctored", "validate ComfyUI first"))
        if not body.id or ":" in body.id:
            raise HTTPException(
                422, _detail("recipe_invalid", "recipe id cannot be empty or contain ':'")
            )
        known_actions = set(ACTION_IDS)
        if not body.actions or not set(body.actions).issubset(known_actions):
            raise HTTPException(422, _detail("recipe_invalid", "recipe has unsupported Actions"))
        if not body.output or len(body.output) != 2:
            raise HTTPException(
                422, _detail("recipe_invalid", "recipe output must be [node_id, index]")
            )
        workflow_nodes = {
            str(node_id): node for node_id, node in body.workflow.items() if isinstance(node, dict)
        }
        if str(body.output[0]) not in workflow_nodes:
            raise HTTPException(422, _detail("recipe_invalid", "recipe output node is missing"))
        known_nodes = {item.removeprefix("comfy.") for item in runtime_tool_ids(runtime)}
        missing = sorted(
            {
                str(node.get("class_type"))
                for node in workflow_nodes.values()
                if str(node.get("class_type")) not in known_nodes
            }
        )
        if missing:
            raise HTTPException(
                422,
                _detail(
                    "recipe_nodes_missing",
                    "workflow uses nodes absent from this runtime",
                    nodes=missing,
                ),
            )
        recipe = materialize_recipe_workflows(
            store,
            runtime["id"],
            runtime["snapshot"],
            Recipe(**body.model_dump(), source="imported"),
        )
        recipes = [item for item in recipes_from_runtime(runtime) if item.id != recipe.id]
        recipes.append(recipe)
        manifest = manifest_from_assets(runtime_assets(runtime))
        manifest = {
            **manifest,
            "schema": "cooksprite.runtime-assets/v1",
            "recipes": [item.dump() for item in recipes],
            "callback_url": callback_for(runtime),
        }
        assets = [
            item
            for item in runtime_assets(runtime)
            if not (isinstance(item, dict) and item.get("schema") == manifest["schema"])
        ]
        assets.insert(0, manifest)
        store.put_runtime(
            runtime["id"],
            runtime["label"],
            runtime["base_url"],
            runtime["snapshot"],
            json.loads(runtime.get("tools") or "[]"),
            assets,
        )
        invalidate_runtime(runtime_id)
        return recipe.dump()

    @app.get("/api/v1/runtimes/{runtime_id}/tools")
    def tools_for_runtime(runtime_id: str) -> list[dict[str, Any]]:
        return [item.model_dump() for item in runtime_tools(runtime_or_404(runtime_id))]

    @app.get("/api/v1/tools")
    def tools() -> list[dict[str, Any]]:
        return [item.model_dump() for item in registry.tools()]

    @app.get("/api/v1/tool-packages")
    def tool_package_manifests() -> list[dict[str, Any]]:
        return registry.package_manifests()

    @app.post("/api/v1/workflows", response_model=WorkflowRevision, status_code=201)
    def create_workflow(body: WorkflowDefinition) -> WorkflowRevision:
        runtime = runtime_or_404(body.runtime_id)
        if not runtime["snapshot"]:
            raise HTTPException(
                409,
                _detail("runtime_not_doctored", "run doctor before defining graphs"),
            )
        descriptors = {item.id: item for item in runtime_tools(runtime)}
        unknown = [node.tool for node in body.nodes if node.tool not in descriptors]
        if unknown:
            raise HTTPException(
                422,
                _detail(
                    "unknown_tool",
                    "tool is not registered for runtime",
                    tools=unknown,
                ),
            )
        by_node = {node.id: node for node in body.nodes}
        for name, reference in body.outputs.items():
            node = by_node.get(reference.node or "")
            descriptor = descriptors.get(node.tool) if node else None
            port = next(
                (
                    item
                    for item in (descriptor.outputs if descriptor else [])
                    if item.name == reference.output
                ),
                None,
            )
            if not port or not port.persistable:
                raise HTTPException(
                    422,
                    _detail(
                        "nonpersistable_output",
                        "only declared persistable outputs may leave a workflow",
                        output=name,
                    ),
                )
        revision = store.save_definition(
            "workflow",
            body.id,
            body.runtime_id,
            runtime["snapshot"],
            body.model_dump(mode="json"),
        )
        return WorkflowRevision(
            **body.model_dump(),
            revision=revision,
            runtime_snapshot=runtime["snapshot"],
        )

    @app.get("/api/v1/workflows")
    def workflows() -> list[dict[str, Any]]:
        return [workflow_from(row).model_dump() for row in store.definitions("workflow")]

    @app.get("/api/v1/workflows/{workflow_id}/{revision}", response_model=WorkflowRevision)
    def workflow(workflow_id: str, revision: int) -> WorkflowRevision:
        row = store.definition("workflow", workflow_id, revision)
        if not row:
            raise HTTPException(404, _detail("workflow_not_found", "unknown workflow revision"))
        return workflow_from(row)

    @app.post("/api/v1/tasks", response_model=TaskRevision, status_code=201)
    def create_task(body: TaskDefinition) -> TaskRevision:
        runtime = runtime_or_404(body.runtime_id)
        if not runtime["snapshot"]:
            raise HTTPException(
                409,
                _detail("runtime_not_doctored", "run doctor before defining graphs"),
            )
        for node in body.nodes:
            for candidate in node.candidates:
                row = store.definition("workflow", node.workflow_id, candidate)
                if not row:
                    raise HTTPException(
                        422,
                        _detail(
                            "workflow_candidate_missing",
                            f"{node.workflow_id}@{candidate} is absent",
                            node=node.id,
                        ),
                    )
                if row["runtime_id"] != body.runtime_id or row["snapshot"] != runtime["snapshot"]:
                    raise HTTPException(
                        409,
                        _detail(
                            "runtime_snapshot_incompatible",
                            "candidate is bound to another runtime snapshot",
                            node=node.id,
                        ),
                    )
        revision = store.save_definition(
            "task",
            body.id,
            body.runtime_id,
            runtime["snapshot"],
            body.model_dump(mode="json"),
        )
        return TaskRevision(
            **body.model_dump(),
            revision=revision,
            runtime_snapshot=runtime["snapshot"],
        )

    @app.get("/api/v1/tasks")
    def tasks() -> list[dict[str, Any]]:
        return [task_from(row).model_dump() for row in store.definitions("task")]

    @app.get("/api/v1/tasks/{task_id}/{revision}", response_model=TaskRevision)
    def task(task_id: str, revision: int) -> TaskRevision:
        row = store.definition("task", task_id, revision)
        if not row:
            raise HTTPException(404, _detail("task_not_found", "unknown task revision"))
        return task_from(row)

    @app.post("/api/v1/runs", response_model=RunView, status_code=202)
    def start_contributor_run(request: RunCreate) -> RunView:
        runtime = runtime_or_404(request.runtime_id)
        run_id = f"run_{uuid.uuid4().hex}"
        try:
            if request.target.kind == "workflow":
                row = store.definition("workflow", request.target.id, request.target.revision)
                if not row:
                    raise CompileError("workflow revision not found")
                workflow_revision = workflow_from(row)
                assert_runtime(request.runtime_id, workflow_revision.runtime_snapshot)
                compiled = Compiler(
                    runtime_tools(runtime), bridge_for(runtime), run_id
                ).compile_workflow(workflow_revision, request.inputs)
            elif request.target.kind == "task":
                row = store.definition("task", request.target.id, request.target.revision)
                if not row:
                    raise CompileError("task revision not found")
                task_revision = task_from(row)
                assert_runtime(request.runtime_id, task_revision.runtime_snapshot)
                workflow_revisions = {}
                for node in task_revision.nodes:
                    for revision in node.candidates:
                        workflow_revisions[(node.workflow_id, revision)] = workflow_from(
                            store.definition("workflow", node.workflow_id, revision)
                        )
                compiled = Compiler(
                    runtime_tools(runtime), bridge_for(runtime), run_id
                ).compile_task(
                    task_revision,
                    workflow_revisions,
                    request.inputs,
                    request.candidate_selection,
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, _detail("graph_invalid", str(exc))) from exc
        store.create_run(run_id, request.runtime_id, request=request.model_dump(mode="json"))
        execute_plan(run_id, runtime, compiled)
        return run_view(run_id)

    def recovered_finalizer(row: dict[str, Any]) -> Callable[[], None] | None:
        action_id = row.get("action_id")
        request = json.loads(row.get("request") or "{}")
        if action_id in SEQUENCE_ACTIONS:
            return lambda: finalize_frame_sequence(
                row["id"],
                action_id,
                row.get("project_id") or request.get("project"),
                request.get("values") or {},
            )
        if action_id == "normal.generate":
            sources = (request.get("inputs") or {}).get("source") or []
            source_ids = sources if isinstance(sources, list) else [sources]
            return lambda: order_normal_outputs(row["id"], source_ids)
        return None

    for interrupted in store.runs(("queued", "running", "cancel_requested")):
        runtime = store.runtime(interrupted.get("runtime_id"))
        if interrupted.get("prompt_id") and runtime:
            supervisor.resume_prompt(
                interrupted["id"],
                runtime,
                interrupted["prompt_id"],
                recovered_finalizer(interrupted),
            )
        else:
            store.update_run(
                interrupted["id"],
                status="failed",
                message="CookSprite restarted before this Run reached ComfyUI",
                error=json.dumps(
                    _detail(
                        "run_interrupted",
                        "retry this Run from its Action or project operation",
                    )
                ),
            )

    packaged_web = Path(__file__).resolve().parents[1] / "static"
    source_web = Path(__file__).resolve().parents[2] / "web" / "dist"
    web_root = packaged_web if (packaged_web / "index.html").is_file() else source_web
    if (web_root / "index.html").is_file():

        @app.get("/{spa_path:path}", include_in_schema=False)
        def web_app(spa_path: str) -> FileResponse:
            if spa_path.startswith("api/"):
                raise HTTPException(404, _detail("not_found", "unknown API endpoint"))
            candidate = (web_root / spa_path).resolve()
            if candidate.is_relative_to(web_root.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(web_root / "index.html")

    return app


app = create_app(os.environ.get("COOKSPRITE_DATA_DIR", "data"))
