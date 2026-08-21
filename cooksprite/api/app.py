"""CookSprite /api/v1: stable Actions over a private ComfyUI runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.responses import Response as BinaryResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..action_graphs import bind_action_task, materialize_recipe_workflows
from ..bridge import ArtifactBridge, BridgeError
from ..comfy import ComfyClient
from ..comfy.discovery import LOOPBACK_HOSTS, discover_comfy_directory, validate_comfy_directory
from ..comfy.managed import install as install_managed_comfy
from ..comfy.managed import (
    install_node_pack,
    launch_with_preference,
    restart_with_preference,
    wait_until_ready,
)
from ..comfy.managed import launch as launch_managed_comfy
from ..comfy.models import ModelDownloadError, download_bundle_file
from ..compiler import CompileError, Compiler
from ..domain import (
    ActionDescriptor,
    ActionRunCreate,
    ArtifactPatch,
    ArtifactRef,
    DocumentView,
    FrameSequenceManifest,
    FrameSequenceTemporal,
    FrameSequenceView,
    GalleryItem,
    PixelGeometryPlanManifest,
    ProjectCreate,
    ProjectExportCreate,
    ProjectPatch,
    ProjectView,
    RunCreate,
    RunRuntimeState,
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
from ..prompting import COMPILER_VERSION
from ..recipe_assembler import sealed_tool_descriptor, with_dimension_slots
from ..recipes import (
    MODEL_BUNDLES,
    Recipe,
    discover_recipes,
    imported_recipe_is_compatible,
    manifest_from_assets,
    model_bundles,
    recipe_contract_is_valid,
    recipe_for_model,
    recipe_variants,
    recipes_from_runtime,
    runtime_manifest,
    supports,
)
from ..registry import ACTION_IDS, CookSpriteRegistry, RegistryError
from ..runtime_state import terminal_runtime_state
from ..store import DocumentConflict, Store, utcnow
from ..supervisor import RunSupervisor
from ..tool_packages import tool_packages

SEQUENCE_ACTIONS = {
    "animation.generate",
    "sheet.slice",
    "video.sample",
    "sprite.pixelize",
    "image.pixelize",
}
TEST_RUNTIME_VERSIONS = {"test", "demo-test", "cooksprite-test-runtime"}


def _runtime_id(label: str, base_url: str) -> str:
    """Create a stable short id so users never need to invent one."""

    host = urlsplit(base_url).hostname or "comfy"
    safe_host = "".join(char if char.isalnum() else "-" for char in host).strip("-")
    digest = hashlib.sha256(base_url.rstrip("/").encode()).hexdigest()[:8]
    return f"rt_{(safe_host or label or 'comfy')[:24]}_{digest}"


COOKSPRITE_NODE_CLASSES = {
    node_class for package in tool_packages.manifests for node_class in package.node_classes
}


class RuntimeCreate(BaseModel):
    id: str | None = None
    label: str = "ComfyUI"
    base_url: str
    location: Literal["local", "remote"] = "remote"
    transport: str = "http"
    callback_url: str | None = None
    directory: str | None = None


class RecipeCreate(BaseModel):
    id: str
    label: str
    family: str = "comfy.imported"
    actions: list[str]
    modes: list[str]
    workflow: dict[str, Any]
    slots: dict[str, str]
    slot_types: dict[str, str] = Field(default_factory=dict)
    output: list[Any]
    checkpoint: str | None = None
    output_name: str = "image"
    output_type: str = "Image"


class RuntimeDefaultBinding(BaseModel):
    model_id: str


class LocalSetupCreate(BaseModel):
    directory: str | None = None
    host: str = "127.0.0.1"
    port: int = 8188


class ComfyProbeCreate(BaseModel):
    base_url: str = "http://127.0.0.1:8188"


# Kept as an import-level compatibility alias for clients that used the old
# local-only name before probing was made location-neutral.
LocalProbeCreate = ComfyProbeCreate


class LocalStartCreate(BaseModel):
    base_url: str = "http://127.0.0.1:8188"
    directory: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)


class PublishCreate(BaseModel):
    cover_artifact_id: str | None = None


class ProjectDirectoryView(BaseModel):
    project_id: str
    path: str
    opened: bool = False
    error: str | None = None


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
                # ComfyUI's official background-removal model handle is an
                # internal graph value.  It never crosses the CookSprite
                # artifact boundary, so keep it as an opaque scalar slot.
                "BACKGROUND_REMOVAL": "Text",
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
                        # A decoded ComfyUI media output is already a typed
                        # artifact candidate.  The compiler adds the single
                        # CS_StoreArtifact sink; no pixel/cutout transform is
                        # implied by this flag.
                        "persistable": port_type(value) in {"Image", "Video", "Mask"},
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
        "method": None,
    }
    runtime_cache: dict[str, dict[str, Any]] = {}
    runtime_cache_lock = threading.RLock()
    model_downloads: dict[str, dict[str, Any]] = {}
    model_download_lock = threading.RLock()

    def runtime_assets(runtime: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not runtime:
            return []
        try:
            value = json.loads(runtime.get("assets") or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    def runtime_node_status(runtime: dict[str, Any] | None) -> tuple[bool, int]:
        if not runtime:
            return False, 0
        try:
            tools = json.loads(runtime.get("tools") or "[]")
        except (TypeError, json.JSONDecodeError):
            tools = []
        present = {
            str(item.get("title") or item.get("id", "")).removeprefix("comfy.")
            for item in tools
            if isinstance(item, dict)
        }
        installed = COOKSPRITE_NODE_CLASSES.issubset(present)
        return installed, len(COOKSPRITE_NODE_CLASSES.intersection(present))

    def bridge_for(runtime: dict[str, Any] | None) -> ArtifactBridge:
        manifest = manifest_from_assets(runtime_assets(runtime))
        callback_url = manifest.get("callback_url") or default_callback_url
        return ArtifactBridge(bridge_secret, callback_url)

    def callback_for(runtime: dict[str, Any] | None) -> str:
        manifest = manifest_from_assets(runtime_assets(runtime))
        return str(manifest.get("callback_url") or default_callback_url).rstrip("/")

    def _default_binding(
        action_id: str,
        recipes: list[Recipe],
        current: dict[str, Any] | None = None,
        *,
        prefer_flux9b: bool = False,
    ) -> dict[str, str] | None:
        """Keep one model default; select the compatible Workflow at run time."""

        compatible = [recipe for recipe in recipes if supports(recipe, action_id)]
        if not compatible:
            return None
        if prefer_flux9b:
            selected = next(
                (
                    recipe
                    for recipe in compatible
                    if recipe.id == "flux2-klein-9b-turbo-t2i"
                ),
                None,
            )
            if selected:
                return {"model_id": selected.checkpoint or selected.id}
            return None
        if isinstance(current, dict):
            workflow_id = str(current.get("workflow_id") or "")
            model_id = str(current.get("model_id") or "")
            if not model_id and workflow_id:
                legacy = next((recipe for recipe in compatible if recipe.id == workflow_id), None)
                model_id = str(legacy.checkpoint or legacy.id) if legacy else ""
            if model_id and any(
                str(recipe.checkpoint or recipe.id) == model_id for recipe in compatible
            ):
                return {"model_id": model_id}
            # An explicit model that disappeared is not silently replaced by
            # a different model. The user can choose another installed model.
            return None
        selected = compatible[0]
        return {"model_id": selected.checkpoint or selected.id}

    def runtime_defaults(
        runtime: dict[str, Any] | None, recipes: list[Recipe] | None = None
    ) -> dict[str, dict[str, str]]:
        if not runtime:
            return {}
        manifest = manifest_from_assets(runtime_assets(runtime))
        stored = manifest.get("defaults") if isinstance(manifest.get("defaults"), dict) else {}
        sources = (
            manifest.get("default_sources")
            if isinstance(manifest.get("default_sources"), dict)
            else {}
        )
        available = recipes if recipes is not None else recipes_from_runtime(runtime)
        result: dict[str, dict[str, str]] = {}
        for action_id in ACTION_IDS:
            has_flux = any(
                recipe.family == "comfy.flux2-klein"
                and action_id in recipe.actions
                for recipe in available
            )
            binding = _default_binding(
                action_id,
                available,
                stored.get(action_id)
                if not (action_id == "image.generate" and has_flux and sources.get(action_id) != "user")
                else None,
                prefer_flux9b=action_id == "image.generate" and has_flux and sources.get(action_id) != "user",
            )
            if binding:
                result[action_id] = binding
        return result

    def save_runtime_defaults(
        runtime: dict[str, Any],
        defaults: dict[str, dict[str, str]],
        *,
        explicit_action: str | None = None,
    ) -> None:
        manifest = manifest_from_assets(runtime_assets(runtime))
        default_sources = (
            dict(manifest.get("default_sources"))
            if isinstance(manifest.get("default_sources"), dict)
            else {}
        )
        if explicit_action:
            default_sources[explicit_action] = "user"
        manifest = {
            **manifest,
            "schema": manifest.get("schema", "cooksprite.runtime-assets/v1"),
            "defaults": defaults,
            "default_sources": default_sources,
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
            runtime.get("snapshot", ""),
            json.loads(runtime.get("tools") or "[]"),
            assets,
            runtime.get("location", "remote"),
            runtime.get("transport", "http"),
            runtime.get("directory"),
        )

    def runtime_model_options(recipes: list[Recipe]) -> list[dict[str, Any]]:
        """Project mode-specific Recipes into one user-selectable model each."""

        models: dict[str, dict[str, Any]] = {}
        for recipe in recipes:
            if not recipe.checkpoint:
                continue
            model_id = str(recipe.checkpoint)
            model = models.setdefault(
                model_id,
                {"id": model_id, "label": model_id, "actions": [], "modes": []},
            )
            model["actions"] = list(dict.fromkeys([*model["actions"], *recipe.actions]))
            model["modes"] = list(dict.fromkeys([*model["modes"], *recipe.modes]))
        return list(models.values())

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
        recipes_by_id: dict[str, Recipe] = {}
        for recipe in [*discovered, *imported]:
            # Fresh structural discovery wins over an older imported copy with
            # the same id, while unrelated user workflows remain intact.
            recipes_by_id.setdefault(recipe.id, recipe)
        recipes = list(recipes_by_id.values())
        manifest = runtime_manifest(report, recipes, callback_url=callback_for(runtime))
        existing_manifest = manifest_from_assets(runtime_assets(runtime))
        stored_defaults = (
            existing_manifest.get("defaults")
            if isinstance(existing_manifest.get("defaults"), dict)
            else {}
        )
        stored_sources = (
            dict(existing_manifest.get("default_sources"))
            if isinstance(existing_manifest.get("default_sources"), dict)
            else {}
        )
        manifest["defaults"] = {
            action_id: binding
            for action_id in ACTION_IDS
            if (
                binding := _default_binding(
                    action_id,
                    recipes,
                    stored_defaults.get(action_id)
                    if not (
                        action_id == "image.generate"
                        and any(
                            recipe.family == "comfy.flux2-klein"
                            and action_id in recipe.actions
                            for recipe in recipes
                        )
                        and stored_sources.get(action_id) != "user"
                    )
                    else None,
                    prefer_flux9b=action_id == "image.generate"
                    and any(
                        recipe.family == "comfy.flux2-klein"
                        and action_id in recipe.actions
                        for recipe in recipes
                    )
                    and stored_sources.get(action_id) != "user",
                )
            )
        }
        manifest["default_sources"] = stored_sources
        model_sources: dict[str, str] = {}
        for folder, names in (manifest.get("models") or {}).items():
            if not isinstance(names, list):
                continue
            for name in names:
                key = f"{folder}:{name}"
                model_sources[key] = "User existing"
        manifest["model_sources"] = model_sources
        assets: list[dict[str, Any]] = [
            manifest
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
            runtime.get("location", "remote"),
            runtime.get("transport", "http"),
            runtime.get("directory"),
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
        if runtime.get("location", "remote") == "local" and not runtime.get("directory"):
            directory = discover_comfy_directory(runtime["base_url"])
            if directory:
                store.put_runtime(
                    runtime["id"],
                    runtime["label"],
                    runtime["base_url"],
                    runtime.get("snapshot", ""),
                    json.loads(runtime.get("tools") or "[]"),
                    runtime_assets(runtime),
                    runtime.get("location", "remote"),
                    runtime.get("transport", "http"),
                    directory,
                )
                runtime = store.runtime(runtime_id) or runtime
        return runtime

    def runtime_tools(runtime: dict[str, Any]) -> list[ToolDescriptor]:
        dynamic = [
            ToolDescriptor.model_validate(item) for item in json.loads(runtime["tools"] or "[]")
        ]
        sealed = [
            descriptor
            for recipe in recipes_from_runtime(runtime)
            for variant in recipe_variants(recipe)
            if (
                descriptor := sealed_tool_descriptor(with_dimension_slots(variant))
            )
            is not None
        ]
        return registry.tools() + dynamic + sealed

    def capability_category(action_id: str) -> str:
        if action_id in {"image.generate", "image.views", "frame.redraw"}:
            return "image"
        if action_id in {"animation.generate", "video.sample"}:
            return "video"
        if action_id in {
            "normal.generate",
            "sprite.pixelize",
            "sheet.slice",
            "image.pixelize",
            "image.cutout",
            "project.export",
        }:
            return "tools"
        return "text"

    def runtime_capabilities(runtime: dict[str, Any]) -> dict[str, Any]:
        """Return a compact, semantic view over one ComfyUI capability snapshot."""

        manifest = manifest_from_assets(runtime_assets(runtime))
        categories = {
            "image": {"models": [], "workflows": [], "tools": []},
            "text": {"models": [], "workflows": [], "tools": []},
            "video": {"models": [], "workflows": [], "tools": []},
            "tools": {"models": [], "workflows": [], "tools": []},
        }
        model_folders = {"checkpoints", "diffusion_models", "unet", "loras"}
        video_folders = {"video_models", "unet_video", "text_encoders_video"}
        text_folders = {"text_encoders", "clip"}
        model_sources = manifest.get("model_sources") or {}
        for folder, names in (manifest.get("models") or {}).items():
            if not isinstance(names, list):
                continue
            category = (
                "video"
                if folder in video_folders
                else "text"
                if folder in text_folders
                else "image"
                if folder in model_folders
                else "tools"
            )
            for name in names:
                categories[category]["models"].append(
                    {
                        "id": f"{folder}:{name}",
                        "label": str(name),
                        "folder": str(folder),
                        "source": str(model_sources.get(f"{folder}:{name}", "User existing")),
                    }
                )
        for recipe in recipes_from_runtime(runtime):
            target_categories = {capability_category(action) for action in recipe.actions}
            for category in target_categories:
                categories[category]["workflows"].append(
                    {
                        "id": recipe.id,
                        "label": recipe.label,
                        "source": "CookSprite"
                        if recipe.family.startswith("cooksprite.")
                        else "User imported"
                        if recipe.source == "imported"
                        else "ComfyUI",
                        "actions": recipe.actions,
                        "modes": recipe.modes,
                    }
                )
        templates = manifest.get("workflow_templates") or {}
        for template_id, template in (templates.items() if isinstance(templates, dict) else []):
            serialized = json.dumps(template, ensure_ascii=False).lower()
            if any(token in serialized for token in ("video", "animated", "i2v", "t2v")):
                category = "video"
            elif any(token in serialized for token in ("prompt", "caption", "translate", "enhance")):
                category = "text"
            elif any(token in serialized for token in ("image", "checkpoint", "ksampler", "t2i", "i2i")):
                category = "image"
            else:
                category = "tools"
            label = template.get("name") if isinstance(template, dict) else None
            categories[category]["workflows"].append(
                {
                    "id": str(template_id),
                    "label": str(label or template_id),
                    "source": "ComfyUI",
                    "template": True,
                }
            )
        for tool in runtime_tools(runtime):
            tool_category = "tools"
            if tool.source == "comfy":
                output_types = {item.type for item in tool.outputs}
                tool_category = "video" if "Video" in output_types else "image" if "Image" in output_types else "tools"
            categories[tool_category]["tools"].append(
                {
                    "id": tool.id,
                    "label": tool.title,
                    "source": "CookSprite" if tool.source == "cooksprite" else "ComfyUI / third-party",
                    "inputs": [item.type for item in tool.inputs],
                    "outputs": [item.type for item in tool.outputs],
                }
            )
        return {
            "runtime_id": runtime["id"],
            "snapshot": runtime.get("snapshot"),
            "system": manifest.get("system") or {},
            "features": manifest.get("features") or {},
            "workflow_templates": manifest.get("workflow_templates") or {},
            "categories": categories,
        }

    def sealed_graphs(runtime: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            descriptor.id: normalized.dump()
            for recipe in recipes_from_runtime(runtime)
            for variant in recipe_variants(recipe)
            if variant.source in {"imported", "discovered"}
            and variant.workflow
            if (normalized := with_dimension_slots(variant))
            if (descriptor := sealed_tool_descriptor(normalized)) is not None
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
        try:
            runtime_state = RunRuntimeState.model_validate_json(row.get("runtime_state") or "{}")
        except (TypeError, ValueError):
            runtime_state = RunRuntimeState()
        # API-only jobs (for example project.export) do not emit ComfyUI
        # events. Their row still starts with the generic queued state, so
        # expose the database terminal status instead of making a finished
        # job look as if it is waiting for ComfyUI.
        terminal_phase = {
            "succeeded": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(row["status"])
        if terminal_phase and runtime_state.phase != terminal_phase:
            stored_error = json.loads(row["error"]) if row.get("error") else None
            runtime_state = RunRuntimeState.model_validate(
                terminal_runtime_state(
                    runtime_state.model_dump(mode="json"),
                    phase=terminal_phase,
                    message=row["message"],
                    error=stored_error,
                )
            )
        return RunView(
            id=row["id"],
            status=row["status"],
            progress=row["progress"],
            message=row["message"],
            action_id=row.get("action_id"),
            project_id=row.get("project_id"),
            runtime_id=row.get("runtime_id"),
            runtime_snapshot=json.loads(row.get("provenance") or "{}").get("runtime_snapshot"),
            artifacts=artifacts,
            runtime_state=runtime_state,
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
        temporal = (
            FrameSequenceTemporal(
                source="sampled_video", sample_fps=float(values.get("sample_fps", 12))
            )
            if action_id == "video.sample"
            else None
        )
        manifest = FrameSequenceManifest(
            action=target_action,
            view=view,
            direction=direction,
            frames=frames,
            temporal=temporal,
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
                "temporal": temporal.model_dump(mode="json") if temporal else None,
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
        store.set_run_artifacts(run_id, ordered, preserve_duplicates=True)

    def source_frame_ids(source_ids: list[str], *, limit: int | None = None) -> list[str]:
        frames: list[str] = []
        for source_id in source_ids:
            row = store.artifact(source_id)
            if row and row.get("kind") == "FrameSeq":
                frames.extend(frame_sequence_view(source_id).sequence.frames)
            else:
                frames.append(source_id)
        if limit is not None and len(frames) > limit:
            raise ValueError(f"Sprite chunks may contain at most {limit} frames")
        return frames

    def finalize_sprite_pixelize(
        run_id: str,
        source_artifact: str,
        sources: list[str],
        project_id: str,
    ) -> None:
        record = store.run(run_id)
        output_ids = json.loads(record.get("artifacts") or "[]") if record else []
        images = [artifact_id for artifact_id in output_ids if (store.artifact(artifact_id) or {}).get("kind") == "Image"]
        normals = [artifact_id for artifact_id in output_ids if (store.artifact(artifact_id) or {}).get("kind") == "NormalMap"]
        if len(images) != len(sources) or len(normals) != len(sources):
            raise RuntimeError("ComfyUI sprite outputs do not match the requested source frames")

        image_relations: dict[str, dict[str, list[str]]] = {}
        normal_relations: dict[str, dict[str, list[str]]] = {}
        for source_id, image_id, normal_id in zip(sources, images, normals, strict=True):
            image_relation = image_relations.setdefault(image_id, {"sources": [], "pairs": []})
            image_relation["sources"].append(source_id)
            image_relation["pairs"].append(normal_id)
            normal_relation = normal_relations.setdefault(normal_id, {"sources": [], "pairs": []})
            normal_relation["sources"].extend((image_id, source_id))
            normal_relation["pairs"].append(image_id)
        for artifact_id, relation in image_relations.items():
            row = store.artifact(artifact_id)
            meta = json.loads(row.get("meta") or "{}") if row else {}
            meta.update(
                source_artifacts=list(dict.fromkeys(relation["sources"])),
                paired_normals=list(dict.fromkeys(relation["pairs"])),
            )
            store.update_artifact_meta(artifact_id, meta)
        for artifact_id, relation in normal_relations.items():
            row = store.artifact(artifact_id)
            meta = json.loads(row.get("meta") or "{}") if row else {}
            meta.update(
                source_artifacts=list(dict.fromkeys(relation["sources"])),
                paired_diffuses=list(dict.fromkeys(relation["pairs"])),
            )
            store.update_artifact_meta(artifact_id, meta)

        source_row = store.artifact(source_artifact)
        if source_row and source_row.get("kind") == "FrameSeq":
            original = frame_sequence_view(source_artifact).sequence
            manifest = FrameSequenceManifest(
                action=original.action,
                view=original.view,
                direction=original.direction,
                frames=images,
            )
            sequence = store.put_artifact(
                manifest.model_dump_json(by_alias=True, exclude_none=False).encode(),
                "application/vnd.cooksprite.frame-sequence+json",
                "FrameSeq",
                {
                    "role": "pixel_frame_sequence",
                    "run_id": run_id,
                    "action_id": "sprite.pixelize",
                    "frame_count": len(images),
                    "cover_artifact": images[0],
                    "action": original.action,
                    "view": original.view,
                    "direction": original.direction,
                    "source_artifacts": [source_artifact],
                },
                project_id=project_id,
                title="Pixelized Sprite Sequence",
            )
            store.set_run_artifacts(
                run_id, [sequence.id, *normals], preserve_duplicates=True
            )
        else:
            store.set_run_artifacts(
                run_id, [images[0], normals[0]], preserve_duplicates=True
            )

    def pixel_plan_source_digest(frames: list[Any]) -> str:
        """Return the canonical digest shared by a Plan and its source order."""

        canonical = [
            {
                "artifact": str(frame.source_artifact),
                "sha256": str(frame.source_sha256),
                "canvas": [int(frame.canvas[0]), int(frame.canvas[1])],
            }
            for frame in frames
        ]
        return hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def validate_pixel_plan_integrity(plan: PixelGeometryPlanManifest) -> None:
        """Reject a stale or malformed geometry sidecar before graph execution."""

        if plan.source_order_sha256 != pixel_plan_source_digest(plan.frames):
            raise ValueError("PixelGeometryPlan source order digest does not match its frame list")
        if any(tuple(frame.canvas) != tuple(plan.canvas) for frame in plan.frames):
            raise ValueError("PixelGeometryPlan frames must share the declared canvas")

    def validate_pixel_plan_sequence(
        plan: PixelGeometryPlanManifest, source_view: FrameSequenceView
    ) -> None:
        """Validate a Plan against the exact source FrameSeq without decoding pixels."""

        validate_pixel_plan_integrity(plan)
        if len(plan.frames) != len(source_view.frames):
            raise ValueError("PixelGeometryPlan does not describe every source frame")
        for index, (source, frame) in enumerate(zip(source_view.frames, plan.frames, strict=True)):
            if frame.source_artifact != source.id or frame.source_sha256 != source.sha256:
                raise ValueError(
                    f"PixelGeometryPlan source frame {index + 1} does not match the FrameSeq"
                )
            raw_canvas = source.meta.get("canvas")
            if (
                isinstance(raw_canvas, (list, tuple))
                and len(raw_canvas) == 2
                and tuple(int(value) for value in raw_canvas) != tuple(frame.canvas)
            ):
                raise ValueError(
                    f"PixelGeometryPlan source frame {index + 1} canvas does not match the artifact"
                )

    def pixel_plan_frame_index(
        source_id: str,
        plan_id: str,
        requested_index: int | None = None,
    ) -> int:
        """Verify one human-selected source against an immutable geometry plan."""

        source = store.artifact(source_id)
        plan_row = store.artifact(plan_id)
        if not source or not plan_row or plan_row.get("kind") != "PixelGeometryPlan":
            raise ValueError("PixelGeometryPlan input is unavailable")
        try:
            plan = PixelGeometryPlanManifest.model_validate_json(store.artifact_bytes(plan_id))
        except Exception as exc:
            raise ValueError("PixelGeometryPlan artifact is invalid") from exc
        validate_pixel_plan_integrity(plan)
        def matches_source(index: int) -> bool:
            return (
                0 <= index < len(plan.frames)
                and plan.frames[index].source_artifact == source_id
                and plan.frames[index].source_sha256 == source.get("sha256")
            )

        if requested_index is not None and requested_index >= 0:
            if not matches_source(requested_index):
                raise ValueError(
                    "the selected sequence frame index does not match this PixelGeometryPlan source"
                )
            return requested_index
        matches = [index for index in range(len(plan.frames)) if matches_source(index)]
        if len(matches) == 1:
            return matches[0]
        source_meta = json.loads(source.get("meta") or "{}")
        latest_index = source_meta.get("latest_pixel_plan_frame_index")
        if isinstance(latest_index, int) and matches_source(latest_index):
            return latest_index
        if matches:
            raise ValueError(
                "the selected source occurs more than once in this PixelGeometryPlan; select its frame index"
            )
        raise ValueError("the selected source image is not an exact source frame of this PixelGeometryPlan")

    def finalize_pixelize_sequence(
        run_id: str,
        source_artifact: str,
        project_id: str,
    ) -> ArtifactRef:
        """Wrap streamed frame uploads and hide their internal geometry sidecar."""

        record = store.run(run_id)
        output_ids = json.loads(record.get("artifacts") or "[]") if record else []
        images = [
            artifact_id
            for artifact_id in output_ids
            if (store.artifact(artifact_id) or {}).get("kind") == "Image"
        ]
        plans = [
            artifact_id
            for artifact_id in output_ids
            if (store.artifact(artifact_id) or {}).get("kind") == "PixelGeometryPlan"
        ]
        source_view = frame_sequence_view(source_artifact)
        if len(images) != len(source_view.sequence.frames) or len(plans) != 1:
            raise RuntimeError("ComfyUI streamed pixel outputs do not match the source FrameSeq")
        plan_id = plans[0]
        plan = PixelGeometryPlanManifest.model_validate_json(store.artifact_bytes(plan_id))
        try:
            validate_pixel_plan_sequence(plan, source_view)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        expected_canvas = tuple(plan.target)
        for image_id in images:
            image_row = store.artifact(image_id)
            image_canvas = json.loads(image_row.get("meta") or "{}").get("canvas") if image_row else None
            if (
                isinstance(image_canvas, (list, tuple))
                and len(image_canvas) == 2
                and tuple(int(value) for value in image_canvas) != expected_canvas
            ):
                raise RuntimeError("PixelGeometryPlan target canvas does not match a streamed output")
        for index, (source_id, image_id) in enumerate(
            zip(source_view.sequence.frames, images, strict=True)
        ):
            source_row = store.artifact(source_id)
            image_row = store.artifact(image_id)
            if source_row:
                source_meta = json.loads(source_row.get("meta") or "{}")
                # Latest operational relation only: re-pixelizing the same
                # source replaces this pointer instead of growing SQLite.
                source_meta.update(
                    latest_pixel_plan_artifact=plan_id,
                    latest_pixel_plan_frame_index=index,
                    latest_pixel_frame_artifact=image_id,
                )
                store.update_artifact_meta(source_id, source_meta)
            if image_row:
                image_meta = json.loads(image_row.get("meta") or "{}")
                image_meta.update(
                    source_artifacts=[source_id],
                    pixel_plan_artifact=plan_id,
                )
                store.update_artifact_meta(image_id, image_meta)
        plan_row = store.artifact(plan_id)
        if plan_row:
            plan_meta = json.loads(plan_row.get("meta") or "{}")
            plan_meta.update(
                role="pixel_geometry_plan",
                system=True,
                run_id=run_id,
                action_id="image.pixelize",
                source_artifacts=[source_artifact],
                frame_count=len(images),
                pixel_frame_artifacts=images,
            )
            store.update_artifact_meta(plan_id, plan_meta)
        original = source_view.sequence
        manifest = FrameSequenceManifest(
            action=original.action,
            view=original.view,
            direction=original.direction,
            frames=images,
            temporal=original.temporal,
        )
        sequence = store.put_artifact(
            manifest.model_dump_json(by_alias=True, exclude_none=False).encode(),
            "application/vnd.cooksprite.frame-sequence+json",
            "FrameSeq",
            {
                "role": "pixel_frame_sequence",
                "run_id": run_id,
                "action_id": "image.pixelize",
                "frame_count": len(images),
                "cover_artifact": images[0],
                "pixel_plan_artifact": plan_id,
                "source_artifacts": [source_artifact],
                "temporal": original.temporal.model_dump(mode="json") if original.temporal else None,
            },
            project_id=project_id,
            title="Pixelized Frame Sequence",
        )
        store.set_run_artifacts(run_id, [sequence.id])
        return sequence

    def finalize_pixel_plan_normal(
        run_id: str,
        source_id: str,
        plan_id: str,
        frame_index: int,
    ) -> None:
        record = store.run(run_id)
        output_ids = json.loads(record.get("artifacts") or "[]") if record else []
        normals = [
            artifact_id
            for artifact_id in output_ids
            if (store.artifact(artifact_id) or {}).get("kind") == "NormalMap"
        ]
        if len(normals) != 1:
            raise RuntimeError("ComfyUI pixel-plan normal projection did not return one NormalMap")
        normal_id = normals[0]
        source_row = store.artifact(source_id)
        normal_row = store.artifact(normal_id)
        source_meta = json.loads(source_row.get("meta") or "{}") if source_row else {}
        if source_meta.get("latest_pixel_plan_artifact") != plan_id:
            raise RuntimeError("PixelGeometryPlan is no longer the current pixel relation for this source frame")
        plan_row = store.artifact(plan_id)
        plan_meta = json.loads(plan_row.get("meta") or "{}") if plan_row else {}
        frame_artifacts = plan_meta.get("pixel_frame_artifacts")
        if (
            not isinstance(frame_artifacts, list)
            or not 0 <= frame_index < len(frame_artifacts)
            or not isinstance(frame_artifacts[frame_index], str)
        ):
            raise RuntimeError("PixelGeometryPlan has no current diffuse for the selected frame index")
        diffuse_id = frame_artifacts[frame_index]
        if not diffuse_id or not store.artifact(diffuse_id):
            raise RuntimeError("selected source frame has no current pixel diffuse paired with this plan")
        if normal_row:
            normal_meta = json.loads(normal_row.get("meta") or "{}")
            normal_meta.update(
                source_artifacts=[source_id, diffuse_id],
                paired_diffuses=[diffuse_id],
                pixel_plan_artifact=plan_id,
                pixel_plan_frame_index=frame_index,
            )
            store.update_artifact_meta(normal_id, normal_meta)
        diffuse_row = store.artifact(diffuse_id)
        if diffuse_row:
            diffuse_meta = json.loads(diffuse_row.get("meta") or "{}")
            diffuse_meta.update(paired_normals=[normal_id])
            store.update_artifact_meta(diffuse_id, diffuse_meta)
        store.set_run_artifacts(run_id, [normal_id])

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
            "schema_version": 6,
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
        runtime_recipes = recipes_from_runtime(runtime)
        descriptor = registry.view(
            action_id, runtime, runtime_tool_ids(runtime), runtime_recipes
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
        control_ids = {control.id for control in registered.controls} | {"model", "runtime"}
        conflicts = sorted(control_ids.intersection(request.params))
        if conflicts:
            raise HTTPException(
                422,
                _detail(
                    "workflow_param_conflict",
                    f"workflow params cannot replace Action values: {conflicts}",
                ),
            )
        normalized_inputs = validate_artifact_inputs(registered, request.inputs)
        compile_inputs = {key: list(value) for key, value in normalized_inputs.items()}
        source_row = (
            store.artifact(normalized_inputs["source"][0])
            if normalized_inputs.get("source")
            else None
        )
        if action_id == "image.pixelize" and source_row and source_row.get("kind") == "FrameSeq":
            sequence = frame_sequence_view(normalized_inputs["source"][0]).sequence
            if len(sequence.frames) > 240:
                raise HTTPException(
                    422,
                    _detail("sequence_too_large", "long sequence pixelization accepts at most 240 frames"),
                )
            if values.get("temporal_mode") == "flow" and not (
                sequence.temporal
                and sequence.temporal.source == "sampled_video"
                and sequence.temporal.sample_fps >= 8.0
            ):
                raise HTTPException(
                    422,
                    _detail(
                        "flow_requires_continuous_video",
                        "continuous video flow requires a FrameSeq sampled from video at at least 8 FPS",
                    ),
                )
            compile_inputs["__source_kind"] = ["FrameSeq"]
        if action_id == "normal.generate" and normalized_inputs.get("pixel_plan"):
            if not source_row or source_row.get("kind") != "Image":
                raise HTTPException(
                    422,
                    _detail(
                        "pixel_plan_source_invalid",
                        "PixelGeometryPlan normal projection requires one selected original Image frame",
                    ),
                )
            try:
                requested_index = values.get("frame_index", -1)
                if isinstance(requested_index, bool) or int(requested_index) != requested_index:
                    raise ValueError("PixelGeometryPlan frame index must be an integer")
                values["frame_index"] = pixel_plan_frame_index(
                    normalized_inputs["source"][0],
                    normalized_inputs["pixel_plan"][0],
                    int(requested_index) if int(requested_index) >= 0 else None,
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, _detail("pixel_plan_source_invalid", str(exc))) from exc
        ordered_sources: list[str] = []
        if action_id in {"normal.generate", "sprite.pixelize"} and not normalized_inputs.get("pixel_plan"):
            try:
                ordered_sources = source_frame_ids(
                    normalized_inputs.get("source", []), limit=32
                )
            except ValueError as exc:
                raise HTTPException(
                    422, _detail("sprite_chunk_too_large", str(exc))
                ) from exc
        runtime = selected_runtime(values)
        runtime_recipes = recipes_from_runtime(runtime)
        descriptor = registry.view(
            action_id, runtime, runtime_tool_ids(runtime), runtime_recipes
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
            binding = runtime_defaults(runtime, runtime_recipes).get(action_id)
            has_flux_recipe = any(
                recipe.family == "comfy.flux2-klein" and action_id in recipe.actions
                for recipe in runtime_recipes
            )
            if action_id == "image.generate" and has_flux_recipe and not binding:
                raise HTTPException(
                    409,
                    _detail(
                        "default_model_unconfigured",
                        "FLUX.2 Klein is installed but no default model is configured; select a model explicitly",
                    ),
                )
            preferred = next(
                (
                    item.id
                    for item in descriptor.models
                    if binding and item.model_id == binding.get("model_id")
                ),
                "",
            )
            values["model"] = next(
                (item.id for item in descriptor.models if item.id == preferred),
                descriptor.models[0].id,
            )
        selected_model = str(values.get("model") or "")
        prefix, separator, model_id = selected_model.partition(":")
        if not separator or prefix != runtime["id"]:
            raise HTTPException(
                422,
                _detail(
                    "recipe_invalid",
                    "the selected model does not belong to the selected runtime",
                ),
            )
        selected_recipe = recipe_for_model(
            runtime_recipes, model_id, action_id, compile_inputs
        )
        if not selected_recipe:
            raise HTTPException(
                409,
                _detail(
                    "recipe_incompatible",
                    "the selected model does not support these text/image inputs",
                ),
            )
        # Re-materialize the small built-in adapter on each run.  Existing
        # runtime manifests may point at an older revision; unchanged graphs
        # reuse their revision while code-level contract changes become
        # available without asking the user to re-doctor ComfyUI.
        selected_recipe = materialize_recipe_workflows(
            store, runtime["id"], runtime["snapshot"], selected_recipe
        )
        project = ensure_action_project_type(project, action_id, values)
        run_id = f"run_{uuid.uuid4().hex}"
        payload = {
            "project": request.project,
            "inputs": normalized_inputs,
            "values": values,
            "params": request.params,
        }
        try:
            task_revision, workflow_revisions, task_inputs, prompt_metadata = bind_action_task(
                store,
                runtime["id"],
                runtime["snapshot"],
                selected_recipe,
                action_id,
                compile_inputs,
                values,
                request.params,
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
            "prompt_compiler": COMPILER_VERSION if values.get("prompt_compile", True) else None,
            "prompt_compiler_enabled": bool(values.get("prompt_compile", True)),
            "prompt": {
                "sha256": hashlib.sha256(
                    str(
                        task_inputs["prompt"].literal
                        if "prompt" in task_inputs and task_inputs["prompt"].literal is not None
                        else ""
                    ).encode()
                ).hexdigest(),
                "metadata": prompt_metadata,
            },
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
            if action_id == "sprite.pixelize":
                finalize_sprite_pixelize(
                    run_id,
                    normalized_inputs["source"][0],
                    ordered_sources,
                    request.project,
                )
            elif action_id == "image.pixelize" and source_row and source_row.get("kind") == "FrameSeq":
                finalize_pixelize_sequence(run_id, normalized_inputs["source"][0], request.project)
            elif action_id in SEQUENCE_ACTIONS and action_id != "image.pixelize":
                finalize_frame_sequence(run_id, action_id, request.project, values)
            elif action_id == "normal.generate":
                if normalized_inputs.get("pixel_plan"):
                    finalize_pixel_plan_normal(
                        run_id,
                        normalized_inputs["source"][0],
                        normalized_inputs["pixel_plan"][0],
                        int(values["frame_index"]),
                    )
                else:
                    order_normal_outputs(run_id, ordered_sources)

        execute_plan(run_id, runtime, compiled, finalize_action)
        return run_view(run_id)

    @app.get("/api/v1/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str) -> RunView:
        return run_view(run_id)

    @app.get("/api/v1/runs/{run_id}/events")
    def events(run_id: str) -> StreamingResponse:
        if not store.run(run_id):
            raise HTTPException(404, _detail("run_not_found", "unknown run"))

        def stream():
            last_updated = ""
            heartbeat_at = time.monotonic()
            while True:
                state = run_view(run_id).model_dump(mode="json")
                marker = state.get("updated_at") or state["runtime_state"].get("updated_at") or ""
                if marker != last_updated:
                    yield f"data: {json.dumps(state, ensure_ascii=False, separators=(',', ':'))}\n\n"
                    last_updated = marker
                if state["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                if time.monotonic() - heartbeat_at >= 10:
                    yield ": keep-alive\n\n"
                    heartbeat_at = time.monotonic()
                time.sleep(0.2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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

    @app.get("/api/v1/projects/{project_id}/directory", response_model=ProjectDirectoryView)
    def get_project_directory(project_id: str) -> ProjectDirectoryView:
        if not store.project(project_id):
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        return ProjectDirectoryView(project_id=project_id, path=str(store.project_directory(project_id)))

    @app.post("/api/v1/projects/{project_id}/directory/open", response_model=ProjectDirectoryView)
    def open_project_directory(project_id: str) -> ProjectDirectoryView:
        if not store.project(project_id):
            raise HTTPException(404, _detail("project_not_found", "unknown project"))
        directory = store.project_directory(project_id)
        system = platform.system()
        if system == "Darwin":
            command = ["open", str(directory)]
        elif system == "Windows":
            command = ["explorer", str(directory)]
        else:
            launcher = shutil.which("xdg-open")
            command = [launcher, str(directory)] if launcher else []
        if not command:
            return ProjectDirectoryView(
                project_id=project_id,
                path=str(directory),
                error="no graphical file browser launcher is available on the API host",
            )
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return ProjectDirectoryView(project_id=project_id, path=str(directory), error=str(exc))
        return ProjectDirectoryView(project_id=project_id, path=str(directory), opened=True)

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
        response_model_exclude_none=True,
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

        def package_runtime_state() -> dict[str, Any]:
            raw = (store.run(run_id) or {}).get("runtime_state") or "{}"
            try:
                return RunRuntimeState.model_validate_json(raw).model_dump(mode="json")
            except (TypeError, ValueError):
                return RunRuntimeState().model_dump(mode="json")

        def package() -> None:
            store.update_run(
                run_id,
                status="running",
                progress=0.2,
                message="validating package",
                runtime_state={
                    **RunRuntimeState().model_dump(mode="json"),
                    "event": "processing",
                    "phase": "processing",
                    "message": "validating package",
                },
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
                store.update_run(
                    run_id,
                    status="succeeded",
                    progress=1,
                    message="package ready",
                    runtime_state=terminal_runtime_state(
                        package_runtime_state(),
                        phase="completed",
                        message="package ready",
                    ),
                )
            except PackageError as exc:
                error = _detail(
                    "export_incomplete",
                    "fix the listed issues or explicitly allow an incomplete package",
                    issues=exc.issues,
                )
                store.update_run(
                    run_id,
                    status="failed",
                    message="package is incomplete",
                    error=json.dumps(error),
                    runtime_state=terminal_runtime_state(
                        package_runtime_state(),
                        phase="failed",
                        message="package is incomplete",
                        error=error,
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
        response_model_exclude_none=True,
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
        expand: str = "",
    ) -> BinaryResponse:
        run = store.run(run_id)
        row = store.artifact(artifact_id)
        if not run or not row:
            raise HTTPException(404, _detail("bridge_target_not_found", "unknown run or artifact"))
        runtime = runtime_or_404(run["runtime_id"])
        try:
            bridge_for(runtime).verify_download(
                artifact_id, run_id, expires, signature, expand=expand
            )
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

        top_level = artifact_ids(requested)
        allowed = set(top_level)
        for requested_id in top_level:
            requested_row = store.artifact(requested_id)
            if not requested_row or requested_row.get("kind") != "FrameSeq":
                continue
            try:
                allowed.update(
                    FrameSequenceManifest.model_validate_json(
                        store.artifact_bytes(requested_id)
                    ).frames
                )
            except ValueError:
                continue
        if artifact_id not in allowed:
            raise HTTPException(
                403,
                _detail("bridge_scope_violation", "artifact is not an input of this run"),
            )
        if expand:
            if expand not in {"frames", "sequence"}:
                raise HTTPException(422, _detail("bridge_expand_invalid", "unknown bridge expansion"))
            if row.get("kind") != "FrameSeq":
                return BinaryResponse(store.artifact_bytes(artifact_id), media_type=row["media_type"])
            sequence = frame_sequence_view(artifact_id)
            limit = 32 if expand == "frames" else 240
            if len(sequence.frames) > limit:
                raise HTTPException(
                    422,
                    _detail(
                        "sequence_too_large",
                        f"CookSprite {expand} bridge accepts at most {limit} frames",
                    ),
                )
            if expand == "frames":
                payload = {
                    "schema": "cooksprite.bridge-image-batch/v1",
                    "frames": [
                        {
                            "artifact": frame.id,
                            "url": bridge_for(runtime).download_url(frame.id, run_id),
                        }
                        for frame in sequence.frames
                    ],
                }
                media_type = "application/vnd.cooksprite.bridge-image-batch+json"
            else:
                raw_canvas = [
                    (json.loads(store.artifact(frame.id).get("meta") or "{}").get("canvas"))
                    for frame in sequence.frames
                    if store.artifact(frame.id)
                ]
                canvas = raw_canvas[0] if raw_canvas and all(item == raw_canvas[0] for item in raw_canvas) else None
                payload = {
                    "schema": "cooksprite.bridge-frame-sequence/v1",
                    "canvas": canvas,
                    "temporal": sequence.sequence.temporal.model_dump(mode="json")
                    if sequence.sequence.temporal
                    else None,
                    "frames": [
                        {
                            "artifact": frame.id,
                            "sha256": frame.sha256,
                            "canvas": json.loads(store.artifact(frame.id).get("meta") or "{}").get("canvas")
                            if store.artifact(frame.id)
                            else None,
                            "url": bridge_for(runtime).download_url(frame.id, run_id),
                        }
                        for frame in sequence.frames
                    ],
                }
                media_type = "application/vnd.cooksprite.bridge-frame-sequence+json"
            return BinaryResponse(
                json.dumps(payload, separators=(",", ":")).encode(),
                media_type=media_type,
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
        canvas_width: int | None = None,
        canvas_height: int | None = None,
    ) -> ArtifactRef:
        run = store.run(run_id)
        if not run:
            raise HTTPException(404, _detail("run_not_found", "unknown run"))
        runtime = runtime_or_404(run["runtime_id"])
        try:
            bridge_for(runtime).verify_upload(run_id, kind, source_artifact, expires, signature)
        except BridgeError as exc:
            raise HTTPException(403, _detail("bridge_signature_invalid", str(exc))) from exc
        if kind not in {"Image", "NormalMap", "PixelGeometryPlan"}:
            raise HTTPException(
                422,
                _detail("artifact_type_invalid", "ComfyUI may persist only declared typed bridge outputs"),
            )
        data = await request.body()
        if not data:
            raise HTTPException(422, _detail("empty_artifact", "artifact request body is empty"))
        media_type = "image/png"
        if kind == "PixelGeometryPlan":
            try:
                plan = PixelGeometryPlanManifest.model_validate_json(data)
                validate_pixel_plan_integrity(plan)
            except Exception as exc:
                raise HTTPException(
                    422, _detail("pixel_plan_invalid", "PixelGeometryPlan payload is invalid")
                ) from exc
            data = plan.model_dump_json(by_alias=True, exclude_none=False).encode()
            media_type = "application/vnd.cooksprite.pixel-geometry-plan+json"
        source_artifacts: list[str] = []
        if source_artifact:
            source_row = store.artifact(source_artifact)
            if source_row and source_row.get("kind") == "FrameSeq" and output_index is not None:
                try:
                    frames = FrameSequenceManifest.model_validate_json(
                        store.artifact_bytes(source_artifact)
                    ).frames
                    if 0 <= output_index < len(frames):
                        source_artifacts = [frames[output_index]]
                except ValueError:
                    source_artifacts = []
            if not source_artifacts:
                source_artifacts = [source_artifact]
        else:
            run_request = json.loads(run.get("request") or "{}")
            for supplied in run_request.get("inputs", {}).values():
                source_artifacts.extend(supplied if isinstance(supplied, list) else [supplied])
        meta = {
            "run_id": run_id,
            "action_id": run.get("action_id"),
            "source_artifacts": source_artifacts,
            "output_index": output_index,
        }
        if canvas_width is not None and canvas_height is not None and canvas_width > 0 and canvas_height > 0:
            meta["canvas"] = [int(canvas_width), int(canvas_height)]
        if kind == "PixelGeometryPlan":
            meta["system"] = True
        artifact = store.put_artifact(
            data,
            media_type,
            kind,
            meta,
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

    def _local_start_target(request: LocalStartCreate) -> tuple[str, str, int, str]:
        parsed = urlsplit(request.base_url.strip())
        hostname = (parsed.hostname or "").lower()
        if hostname not in LOOPBACK_HOSTS:
            raise HTTPException(
                422,
                _detail(
                    "local_start_requires_loopback",
                    "ComfyUI can be started here only for a loopback address",
                ),
            )
        if parsed.scheme not in {"", "http"}:
            raise HTTPException(
                422,
                _detail("local_start_scheme_invalid", "local ComfyUI startup requires http"),
            )
        host = request.host or parsed.hostname or "127.0.0.1"
        port = int(request.port or parsed.port or 8188)
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        base_url = f"http://{display_host}:{port}"

        directory = validate_comfy_directory(request.directory) if request.directory else None
        if request.directory and not directory:
            raise HTTPException(
                422,
                _detail("local_comfy_directory_invalid", "the supplied ComfyUI directory is not a valid checkout"),
            )
        if not directory:
            for runtime in store.runtimes():
                if (
                    runtime.get("location", "remote") == "local"
                    and str(runtime.get("base_url", "")).rstrip("/") == base_url.rstrip("/")
                ):
                    directory = validate_comfy_directory(runtime.get("directory"))
                    if directory:
                        break
        directory = discover_comfy_directory(base_url, directory)
        if not directory and port == 8188:
            directory = validate_comfy_directory(default_managed_root)
        if not directory:
            raise HTTPException(
                409,
                _detail(
                    "local_comfy_directory_not_found",
                    "no local ComfyUI checkout was found; connect it once with its directory or install the managed runtime",
                ),
            )
        return base_url, host, port, directory

    @app.post("/api/v1/local/start", status_code=202)
    def start_local_comfy(request: LocalStartCreate) -> dict[str, Any]:
        base_url, host, port, directory = _local_start_target(request)
        with setup_lock:
            if setup_state["status"] in {"installing", "starting", "validating"}:
                raise HTTPException(
                    409, _detail("setup_in_progress", "local ComfyUI setup is already running")
                )
            setup_state.update(
                status="starting",
                progress=0.05,
                message="starting local ComfyUI",
                error=None,
                directory=directory,
                method=None,
            )

        def start_worker() -> None:
            method = "already_running"
            try:
                try:
                    app.state.comfy_factory(base_url).ping()
                except Exception:  # noqa: BLE001 - the next step is the explicit local start.
                    launch = launch_with_preference(directory, host=host, port=port)
                    method = launch.method
                    with setup_lock:
                        setup_state.update(
                            status="starting",
                            progress=0.35,
                            message=f"ComfyUI started with {method}; waiting for API",
                            method=method,
                        )
                    report = wait_until_ready(base_url, timeout=300)
                else:
                    report = app.state.comfy_factory(base_url).doctor()

                existing = next(
                    (
                        runtime
                        for runtime in store.runtimes()
                        if runtime.get("location", "remote") == "local"
                        and str(runtime.get("base_url", "")).rstrip("/") == base_url.rstrip("/")
                    ),
                    None,
                )
                runtime_id = existing["id"] if existing else _runtime_id("Local ComfyUI", base_url)
                store.put_runtime(
                    runtime_id,
                    existing["label"] if existing else "Local ComfyUI",
                    base_url,
                    existing.get("snapshot", "") if existing else "",
                    json.loads(existing.get("tools") or "[]") if existing else [],
                    runtime_assets(existing) if existing else [],
                    "local",
                    "local-process",
                    directory,
                )
                runtime = store.runtime(runtime_id)
                if not runtime:
                    raise RuntimeError("local runtime registration failed")
                with setup_lock:
                    setup_state.update(
                        status="validating",
                        progress=0.85,
                        message="validating local ComfyUI capabilities",
                        method=method,
                    )
                snapshot, _, recipes = persist_runtime_report(runtime, report)
                with setup_lock:
                    setup_state.update(
                        status="ready",
                        progress=1.0,
                        message=f"local ComfyUI is ready with {len(recipes)} compatible recipe(s)",
                        error=None,
                        method=method,
                        snapshot=snapshot,
                        runtime_id=runtime_id,
                    )
            except Exception as exc:  # noqa: BLE001 - normalized UI lifecycle boundary.
                with setup_lock:
                    setup_state.update(
                        status="failed",
                        progress=1.0,
                        message="local ComfyUI startup failed",
                        error=str(exc),
                        method=method,
                    )

        threading.Thread(target=start_worker, daemon=True, name="cooksprite-local-comfy-start").start()
        return local_setup_status()

    @app.post("/api/v1/comfyui/probe")
    @app.post("/api/v1/local/probe", include_in_schema=False)
    def probe_comfyui(request: ComfyProbeCreate | None = None) -> dict[str, Any]:
        """Probe the explicitly supplied ComfyUI URL, local or remote.

        Runtime location is a connection choice, not something inferred by
        this probe. Local installation/start controls are exposed only when
        CookSprite can identify a local checkout for the same URL.
        """

        request = request or ComfyProbeCreate()
        base_url = request.base_url.strip().rstrip("/")
        configured = [
            runtime
            for runtime in store.runtimes()
            if str(runtime.get("base_url", "")).rstrip("/") == base_url
        ]
        known_runtime = configured[0] if configured else None
        known_directory = known_runtime.get("directory") if known_runtime else None
        managed_installed = (default_managed_root / "install.json").is_file() and (
            default_managed_root / "ComfyUI" / "main.py"
        ).is_file()
        default_local_url = f"http://127.0.0.1:{LocalSetupCreate.model_fields['port'].default}"
        directory = discover_comfy_directory(base_url, known_directory)
        managed = bool(
            directory
            and (
                directory == str(default_managed_root / "ComfyUI")
                or (known_runtime and known_runtime.get("location", "remote") == "local")
            )
        ) or base_url == default_local_url
        try:
            # Keep the button responsive when the endpoint is offline. The
            # full doctor call fans out to several ComfyUI endpoints and is
            # only useful after this cheap liveness check.
            client = app.state.comfy_factory(base_url)
            ping = getattr(client, "ping", None)
            if callable(ping):
                ping()
            report = app.state.comfy_factory(base_url).doctor()
            system = (report.get("system_stats") or {}).get("system") or {}
            present_nodes = {
                name
                for name in (report.get("object_info") or {})
                if name in COOKSPRITE_NODE_CLASSES
            }
            candidate = {
                "base_url": base_url,
                "status": "found",
                "version": system.get("comfyui_version"),
                "device": system.get("device"),
                "models": sum(
                    len(items)
                    for items in (report.get("models") or {}).values()
                    if isinstance(items, list)
                ),
                "workflows": len(report.get("workflow_templates") or {}),
                "nodes": len(report.get("object_info") or {}),
                "cooksprite_nodes": len(present_nodes),
                "nodes_installed": COOKSPRITE_NODE_CLASSES.issubset(present_nodes),
                "directory_found": bool(directory),
                "directory": directory,
                "managed": managed,
            }
        except Exception as exc:  # noqa: BLE001 - probe returns a user-readable state.
            candidate = {
                "base_url": base_url,
                "status": "unreachable",
                "error": str(exc),
                "directory_found": bool(directory),
                "directory": directory,
                "managed": managed,
            }
        status = candidate["status"]
        if status == "unreachable":
            if managed_installed and managed:
                status = "unreachable"
            elif managed:
                status = "missing"
        return {
            "status": status,
            "managed_installed": managed_installed,
            "candidates": [candidate],
        }

    # Contributor/debug surface. Ordinary Web/CLI/Skill users do not need it.
    @app.get("/api/v1/setup/local")
    def local_setup_status() -> dict[str, Any]:
        with setup_lock:
            state = dict(setup_state)
        if state["status"] in {"installed", "ready"} and state.get("directory") == str(default_managed_root):
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
                    "local",
                    "local-process",
                    str(root),
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
        runtime_id = request.id or next(
            (
                row["id"]
                for row in store.runtimes()
                if row.get("base_url") == request.base_url
                and row.get("location", "remote") == request.location
            ),
            _runtime_id(request.label, request.base_url),
        )
        existing = store.runtime(runtime_id)
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
        directory = request.directory or (existing.get("directory") if same_endpoint and existing else None)
        if request.location == "local":
            directory = discover_comfy_directory(request.base_url, directory)
        else:
            directory = None
        store.put_runtime(
            runtime_id,
            request.label,
            request.base_url,
            existing.get("snapshot", "") if same_endpoint and existing else "",
            json.loads(existing.get("tools", "[]")) if same_endpoint and existing else [],
            existing_assets,
            request.location,
            request.transport,
            directory,
        )
        invalidate_runtime(runtime_id)
        return {
            "id": runtime_id,
            "label": request.label,
            "base_url": request.base_url,
            "location": request.location,
            "transport": request.transport,
            "directory": directory,
            "snapshot": existing.get("snapshot") if same_endpoint and existing else None,
        }

    @app.delete("/api/v1/runtimes/{runtime_id}")
    def delete_runtime(runtime_id: str) -> dict[str, Any]:
        runtime = runtime_or_404(runtime_id)
        try:
            active_runtime_id = store.delete_runtime(runtime_id)
        except RuntimeError as exc:
            raise HTTPException(409, _detail("runtime_in_use", str(exc))) from exc
        invalidate_runtime(runtime_id)
        return {
            "runtime_id": runtime_id,
            "deleted": True,
            "active_runtime_id": active_runtime_id,
            "message": f"runtime {runtime['label']} removed; no ComfyUI process was stopped",
        }

    @app.get("/api/v1/runtimes")
    def runtimes() -> list[dict[str, Any]]:
        active_runtime_id = store.active_runtime_id()
        if not active_runtime_id:
            fallback = store.active_runtime()
            active_runtime_id = fallback["id"] if fallback else None
        result = []
        for row in store.runtimes():
            row = runtime_or_404(row["id"])
            state = probe_runtime(row)
            nodes_installed, cooksprite_nodes = runtime_node_status(row)
            result.append(
                {
                    **{
                        key: row[key]
                        for key in ("id", "label", "base_url", "snapshot", "location", "transport")
                    },
                    "status": state["status"],
                    "error": state["error"],
                    "checked_at": state["checked_at"],
                    "callback_url": callback_for(row),
                    "recipes": [recipe.dump() for recipe in recipes_from_runtime(row)],
                    "active": row["id"] == active_runtime_id,
                    "nodes_installed": nodes_installed,
                    "cooksprite_nodes": cooksprite_nodes,
                    "node_install_available": bool(row.get("directory")),
                }
            )
        return result

    @app.post("/api/v1/runtimes/{runtime_id}/select")
    def select_runtime(runtime_id: str) -> dict[str, Any]:
        runtime = runtime_or_404(runtime_id)
        store.set_active_runtime(runtime_id)
        invalidate_runtime()
        state = probe_runtime(runtime, force=True)
        return {
            "runtime_id": runtime_id,
            "status": state["status"],
            "error": state["error"],
            "active": True,
        }

    @app.get("/api/v1/runtimes/{runtime_id}/capabilities")
    def runtime_capabilities_view(runtime_id: str) -> dict[str, Any]:
        return runtime_capabilities(runtime_or_404(runtime_id))

    @app.get("/api/v1/runtimes/{runtime_id}/defaults")
    def runtime_defaults_view(runtime_id: str) -> dict[str, Any]:
        runtime = runtime_or_404(runtime_id)
        recipes = recipes_from_runtime(runtime)
        manifest = manifest_from_assets(runtime_assets(runtime))
        # Recompute from the persisted model inventory as well as the stored
        # projection so older runtimes show bundle readiness before their next
        # doctor refresh.
        bundles = model_bundles(manifest)
        return {
            "runtime_id": runtime_id,
            "defaults": runtime_defaults(runtime, recipes),
            "model_bundles": bundles,
            "models": runtime_model_options(recipes),
            "recipes": [
                {
                    "id": recipe.id,
                    "label": recipe.label,
                    "actions": recipe.actions,
                    "modes": recipe.modes,
                    "model_id": recipe.checkpoint or recipe.id,
                }
                for recipe in recipes
            ],
        }

    @app.put("/api/v1/runtimes/{runtime_id}/defaults/{action_id}")
    def set_runtime_default(
        runtime_id: str, action_id: str, body: RuntimeDefaultBinding
    ) -> dict[str, Any]:
        runtime = runtime_or_404(runtime_id)
        if action_id not in ACTION_IDS:
            raise HTTPException(404, _detail("action_not_found", "unknown Action"))
        recipes = recipes_from_runtime(runtime)
        compatible = [
            recipe
            for recipe in recipes
            if supports(recipe, action_id)
            and str(recipe.checkpoint or recipe.id) == body.model_id
        ]
        if not compatible:
            raise HTTPException(
                422,
                _detail(
                    "default_model_incompatible",
                    "the selected model does not support this Action on this runtime",
                ),
            )
        defaults = runtime_defaults(runtime, recipes)
        defaults[action_id] = {"model_id": body.model_id}
        save_runtime_defaults(runtime, defaults, explicit_action=action_id)
        invalidate_runtime(runtime_id)
        return {"runtime_id": runtime_id, "action_id": action_id, "default": defaults[action_id]}

    def _model_download_view(job: dict[str, Any]) -> dict[str, Any]:
        with model_download_lock:
            return dict(job)

    def _update_model_download(download_id: str, **changes: Any) -> None:
        with model_download_lock:
            job = model_downloads.get(download_id)
            if job:
                job.update(changes)

    def _run_model_download(
        download_id: str,
        runtime: dict[str, Any],
        bundle_id: str,
    ) -> None:
        bundle = MODEL_BUNDLES[bundle_id]
        files = list(bundle["files"])
        total_files = max(1, len(files))
        _update_model_download(
            download_id,
            status="downloading",
            message="downloading model bundle",
            progress=0.0,
        )

        def report_file(index: int, event: dict[str, Any]) -> None:
            local = float(event.get("progress") or 0.0)
            _update_model_download(
                download_id,
                current_file=event.get("current_file"),
                bytes_done=int(event.get("bytes_done") or 0),
                bytes_total=int(event.get("bytes_total") or 0),
                progress=min(0.99, (index + local) / total_files),
                message=str(event.get("message") or "downloading model"),
            )

        try:
            for index, file in enumerate(files):
                download_bundle_file(
                    runtime,
                    file,
                    progress=lambda event, index=index: report_file(index, event),
                )
            _update_model_download(
                download_id,
                status="verifying",
                progress=0.99,
                current_file=None,
                message="verifying model bundle with ComfyUI",
            )
            report = app.state.comfy_factory(runtime["base_url"]).doctor()
            persist_runtime_report(runtime, report)
            available = next(
                (
                    item
                    for item in model_bundles(report)
                    if item["id"] == bundle_id
                ),
                None,
            )
            if not available or not available["ready"]:
                missing = [file["name"] for file in (available or {}).get("files", []) if not file.get("present")]
                raise ModelDownloadError(
                    "model bundle verification failed"
                    + (f": missing {', '.join(missing)}" if missing else ""),
                    code="model_bundle_incomplete",
                )
            _update_model_download(
                download_id,
                status="succeeded",
                progress=1.0,
                current_file=None,
                bytes_done=0,
                bytes_total=0,
                message="model bundle is ready",
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - report every downloader failure to Web.
            code = getattr(exc, "code", "model_download_failed")
            _update_model_download(
                download_id,
                status="failed",
                message="model bundle download failed",
                error={"code": str(code), "message": str(exc)},
            )

    @app.post("/api/v1/runtimes/{runtime_id}/model-bundles/{bundle_id}/download", status_code=202)
    def download_model_bundle(runtime_id: str, bundle_id: str) -> dict[str, Any]:
        runtime = runtime_or_404(runtime_id)
        if bundle_id not in MODEL_BUNDLES:
            raise HTTPException(404, _detail("model_bundle_not_found", "unknown model bundle"))
        with model_download_lock:
            existing = next(
                (
                    item
                    for item in model_downloads.values()
                    if item["runtime_id"] == runtime_id
                    and item["bundle_id"] == bundle_id
                    and item["status"] in {"queued", "downloading", "verifying"}
                ),
                None,
            )
            if existing:
                return _model_download_view(existing)
            download_id = f"model_download_{uuid.uuid4().hex}"
            job = {
                "id": download_id,
                "runtime_id": runtime_id,
                "bundle_id": bundle_id,
                "status": "queued",
                "current_file": None,
                "bytes_done": 0,
                "bytes_total": 0,
                "progress": 0.0,
                "message": "queued",
                "error": None,
            }
            model_downloads[download_id] = job
        threading.Thread(
            target=_run_model_download,
            args=(download_id, runtime, bundle_id),
            name=f"cooksprite-model-{bundle_id}",
            daemon=True,
        ).start()
        return _model_download_view(job)

    @app.get("/api/v1/runtimes/{runtime_id}/model-downloads/{download_id}")
    def model_download_status(runtime_id: str, download_id: str) -> dict[str, Any]:
        with model_download_lock:
            job = model_downloads.get(download_id)
        if not job or job["runtime_id"] != runtime_id:
            raise HTTPException(404, _detail("model_download_not_found", "unknown model download"))
        return _model_download_view(job)

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
        devices = report.get("system_stats", {}).get("devices") or []
        model_counts = {
            name: len(items)
            for name, items in (report.get("models") or {}).items()
            if isinstance(items, list) and items
        }
        return {
            "runtime_id": runtime_id,
            "snapshot": snapshot,
            "tool_count": len(dynamic),
            "recipe_count": len(recipes),
            "recipes": [recipe.dump() for recipe in recipes],
            "system": {
                key: system.get(key)
                for key in (
                    "comfyui_version",
                    "python_version",
                    "pytorch_version",
                    "deploy_environment",
                )
                if system.get(key) is not None
            },
            "device": devices[0] if devices else None,
            "models": model_counts,
        }

    @app.post("/api/v1/runtimes/{runtime_id}/nodes/install")
    def install_runtime_nodes(runtime_id: str) -> dict[str, Any]:
        """Install CookSprite nodes only when the ComfyUI checkout is local."""

        runtime = runtime_or_404(runtime_id)
        directory = validate_comfy_directory(runtime.get("directory"))
        if not directory:
            return {
                "runtime_id": runtime_id,
                "status": "manual_required",
                "message": "Install CookSprite nodes on the remote ComfyUI host, then reconnect.",
                "command": "cspr comfy install-nodes <ComfyUI directory>",
                "restart_required": False,
            }
        target = install_node_pack(directory, install_dependencies=False)
        return {
            "runtime_id": runtime_id,
            "status": "installed",
            "directory": directory,
            "node_directory": str(target),
            "message": "CookSprite nodes installed; restart ComfyUI to load them.",
            "restart_required": True,
        }

    @app.post("/api/v1/runtimes/{runtime_id}/restart", status_code=202)
    def restart_runtime(runtime_id: str) -> dict[str, Any]:
        """Restart a local ComfyUI process so newly installed nodes are loaded."""

        runtime = runtime_or_404(runtime_id)
        if runtime.get("location", "remote") != "local":
            return {
                "runtime_id": runtime_id,
                "status": "manual_required",
                "message": "Remote ComfyUI cannot be restarted from this CookSprite host.",
                "restart_required": True,
            }
        base_url, host, port, directory = _local_start_target(
            LocalStartCreate(base_url=runtime["base_url"], directory=runtime.get("directory"))
        )
        active_runs = store.active_run_count(runtime_id)
        if active_runs:
            raise HTTPException(
                409,
                _detail(
                    "runtime_in_use",
                    f"cannot restart ComfyUI while {active_runs} run(s) are active",
                ),
            )
        with setup_lock:
            if setup_state["status"] in {"installing", "starting", "validating"}:
                raise HTTPException(
                    409, _detail("setup_in_progress", "local ComfyUI lifecycle operation is already running")
                )
            setup_state.update(
                status="starting",
                progress=0.05,
                message="restarting local ComfyUI",
                error=None,
                directory=directory,
                method=None,
                runtime_id=runtime_id,
            )

        def restart_worker() -> None:
            try:
                launch = restart_with_preference(directory, host=host, port=port)
                with setup_lock:
                    setup_state.update(
                        status="starting",
                        progress=0.35,
                        message=f"ComfyUI restarted with {launch.method}; waiting for API",
                        method=launch.method,
                    )
                report = wait_until_ready(base_url, timeout=300)
                refreshed = store.runtime(runtime_id)
                if not refreshed:
                    raise RuntimeError("runtime was removed while ComfyUI was restarting")
                with setup_lock:
                    setup_state.update(
                        status="validating",
                        progress=0.85,
                        message="validating restarted ComfyUI capabilities",
                        method=launch.method,
                    )
                snapshot, _, recipes = persist_runtime_report(refreshed, report)
                with setup_lock:
                    setup_state.update(
                        status="ready",
                        progress=1.0,
                        message=f"ComfyUI restarted with {len(recipes)} compatible recipe(s)",
                        error=None,
                        method=launch.method,
                        snapshot=snapshot,
                        runtime_id=runtime_id,
                    )
            except Exception as exc:  # noqa: BLE001 - normalized UI lifecycle boundary.
                with setup_lock:
                    setup_state.update(
                        status="failed",
                        progress=1.0,
                        message="ComfyUI restart failed",
                        error=str(exc),
                    )

        threading.Thread(target=restart_worker, daemon=True, name="cooksprite-comfy-restart").start()
        return local_setup_status()

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
        candidate = Recipe(**body.model_dump(), source="imported")
        if not recipe_contract_is_valid(candidate):
            raise HTTPException(
                422,
                _detail(
                    "recipe_invalid",
                    "recipe slots/output do not satisfy a supported CookSprite contract",
                ),
            )
        recipe = materialize_recipe_workflows(
            store,
            runtime["id"],
            runtime["snapshot"],
            candidate,
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
            runtime.get("location", "remote"),
            runtime.get("transport", "http"),
            runtime.get("directory"),
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
        if action_id == "sprite.pixelize":
            sources = (request.get("inputs") or {}).get("source") or []
            source_ids = sources if isinstance(sources, list) else [sources]
            expanded = source_frame_ids(source_ids, limit=32)
            return lambda: finalize_sprite_pixelize(
                row["id"],
                source_ids[0],
                expanded,
                row.get("project_id") or request.get("project"),
            )
        if action_id == "image.pixelize":
            sources = (request.get("inputs") or {}).get("source") or []
            source_id = sources[0] if isinstance(sources, list) and sources else sources
            source = store.artifact(source_id) if source_id else None
            if source and source.get("kind") == "FrameSeq":
                return lambda: finalize_pixelize_sequence(
                    row["id"], source_id, row.get("project_id") or request.get("project")
                )
            return None
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
            plans = (request.get("inputs") or {}).get("pixel_plan") or []
            plan_id = plans[0] if isinstance(plans, list) and plans else plans
            if plan_id and source_ids:
                return lambda: finalize_pixel_plan_normal(
                    row["id"], source_ids[0], plan_id, int((request.get("values") or {}).get("frame_index", 0))
                )
            expanded = source_frame_ids(source_ids, limit=32)
            return lambda: order_normal_outputs(row["id"], expanded)
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
