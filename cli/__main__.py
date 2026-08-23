"""Thin HTTP CLI used directly by people and by the CookSprite agent Skill."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from cooksprite.client import CookSpriteClient
from cooksprite.comfy.managed import (
    check_dependencies,
    lock_dependencies,
)
from cooksprite.config import resolve_data_dir, save_data_dir
from cooksprite.data_migration import DataMigrationError, migrate_data_dir, verify_data_dir
from cooksprite.dev import (
    check_generated,
    check_tool_packages,
    sync_generated,
    sync_node_requirements,
    sync_static_web,
    verify_distribution,
)
from cooksprite.environment import check_project, lock_project
from cooksprite.service import ServiceError, service_status, start_service, stop_service
from cooksprite.worker import (
    DEFAULT_RUNTIME_DIR_NAME,
    DEFAULT_WORKER_PORT,
    WorkerConfig,
    WorkerError,
    default_runtime_dir,
    doctor_worker,
    initialize_worker,
    install_worker,
    restart_worker,
    start_worker,
    stop_worker,
    sync_worker,
    worker_status,
)

TERMINAL = {"succeeded", "failed", "cancelled"}


def client(base: str | None) -> CookSpriteClient:
    return CookSpriteClient(base)


def show(response) -> int:
    if response.status_code >= 400:
        print(response.text, file=sys.stderr)
        return 2
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def parse_pairs(items: list[str], lists: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"expected key=value, got: {item}")
        key, raw = item.split("=", 1)
        if lists:
            result[key] = [value for value in raw.split(",") if value]
            if len(result[key]) == 1:
                result[key] = result[key][0]
            continue
        try:
            result[key] = json.loads(raw)
        except json.JSONDecodeError:
            result[key] = raw
    return result


def wait_for_run(http: CookSpriteClient, run: dict[str, Any]) -> int:
    while run["status"] not in TERMINAL:
        time.sleep(0.35)
        response = http.get(f"/api/v1/runs/{run['id']}")
        if response.status_code >= 400:
            return show(response)
        run = response.json()
        runtime_state = run.get("runtime_state") or {}
        message = runtime_state.get("message") or run.get("message") or ""
        print(f"{run['status']} {run['progress']:.0%} {message}", file=sys.stderr)
        if runtime_state.get("error"):
            print(
                f"ERROR {runtime_state['error'].get('code')}: {runtime_state['error'].get('message')}",
                file=sys.stderr,
            )
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "succeeded" else 1


def cmd_actions(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        response = http.get("/api/v1/actions")
    if response.status_code >= 400 or args.json:
        return show(response)
    for action in response.json():
        copy = action["i18n"][args.lang]
        state = "ready" if action["available"] else action.get("unavailable_reason") or "offline"
        print(f"{action['id']:<20} {copy['name']:<18} {state}")
    return 0


def cmd_action_describe(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        response = http.get(f"/api/v1/actions/{args.id}")
    if response.status_code >= 400 or args.json:
        return show(response)
    action = response.json()
    copy = action["i18n"][args.lang]
    print(f"{action['id']} — {copy['name']}")
    print(copy["description"])
    if action["accepts"]:
        print("inputs:")
        for name, rule in action["accepts"].items():
            requirement = "required" if rule["required"] else "optional"
            print(f"  {name}: {rule['type']} ({requirement}, max {rule['max']})")
    if action["controls"]:
        print("values:")
        for control in action["controls"]:
            label = control["i18n"][args.lang]["name"]
            print(
                f"  {control['id']}: {control['type']} = {json.dumps(control['default'])}  {label}"
            )
    return 0


def cmd_action_run(args: argparse.Namespace) -> int:
    payload = {
        "project": args.project,
        "inputs": parse_pairs(args.input, lists=True),
        "values": parse_pairs(args.value),
        "params": parse_pairs(args.param),
    }
    with client(args.api) as http:
        response = http.post(f"/api/v1/actions/{args.id}/runs", json=payload)
        if response.status_code >= 400:
            return show(response)
        run = response.json()
        if not args.wait:
            print(json.dumps(run, ensure_ascii=False, indent=2))
            return 0
        return wait_for_run(http, run)


def cmd_project(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        operation = getattr(args, "project_action", None)
        if operation is None:
            if getattr(args, "gallery", False):
                return show(http.get("/api/v1/gallery"))
            return show(http.get("/api/v1/projects"))
        if operation == "create":
            return show(
                http.post(
                    "/api/v1/projects",
                    json={"name": args.name, "type": args.type},
                )
            )
        if operation == "export":
            response = http.post(
                f"/api/v1/projects/{args.id}/exports",
                json={"allow_incomplete": args.allow_incomplete},
            )
            if response.status_code >= 400:
                return show(response)
            run = response.json()
            if args.wait:
                return wait_for_run(http, run)
            print(json.dumps(run, ensure_ascii=False, indent=2))
            return 0
        if operation != "manage":
            return 2
        manage_action = args.manage_action
        if manage_action == "show":
            return show(http.get(f"/api/v1/projects/{args.id}"))
        if manage_action == "update":
            body = {
                key: value
                for key, value in {
                    "name": args.name,
                    "type": args.type,
                    "favorite": args.favorite,
                }.items()
                if value is not None
            }
            return show(http.patch(f"/api/v1/projects/{args.id}", json=body))
        if manage_action == "publish":
            return show(
                http.post(
                    f"/api/v1/projects/{args.id}/publish",
                    json={"cover_artifact_id": args.cover},
                )
            )
        if manage_action == "sequence":
            return show(
                http.post(
                    f"/api/v1/projects/{args.id}/sequences",
                    json={
                        "action": args.clip,
                        "view": args.view,
                        "direction": args.direction,
                    },
                )
            )
        if manage_action == "document":
            if args.document_action == "get":
                response = http.get(f"/api/v1/projects/{args.id}/document")
                if response.status_code >= 400:
                    return show(response)
                body = json.dumps(response.json(), ensure_ascii=False, indent=2)
                if args.out:
                    Path(args.out).write_text(body + "\n", encoding="utf-8")
                else:
                    print(body)
                return 0
            document = json.loads(Path(args.file).read_text(encoding="utf-8"))
            return show(
                http.put(
                    f"/api/v1/projects/{args.id}/document",
                    json=document,
                    headers={"If-Match": args.etag},
                )
            )
        return 2


def cmd_artifact(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        operation = getattr(args, "artifact_action", None)
        if operation is None:
            params = {
                key: value
                for key, value in {
                    "project_id": args.project,
                    "kind": args.kind,
                    "trashed": args.trashed,
                    "search": args.search,
                }.items()
                if value not in {None, "", False}
            }
            return show(http.get("/api/v1/artifacts", params=params))
        if operation == "upload":
            path = Path(args.file)
            media_type = (
                args.media_type
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            )
            with path.open("rb") as handle:
                response = http.post(
                    "/api/v1/artifacts",
                    params={
                        "project_id": args.project,
                        "kind": args.kind,
                        "media_type": media_type,
                        "title": args.title or path.name,
                    },
                    headers={"Content-Length": str(path.stat().st_size)},
                    content=iter(lambda: handle.read(1024 * 1024), b""),
                    timeout=None,
                )
            return show(response)
        if operation == "get":
            get_action = getattr(args, "get_action", None) or "show"
            if get_action == "show":
                return show(http.get(f"/api/v1/artifacts/{args.id}"))
            if get_action == "sequence":
                return show(http.get(f"/api/v1/artifacts/{args.id}/sequence"))
            if get_action == "download":
                output = Path(args.out)
                temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
                try:
                    with http.stream(
                        "GET", f"/api/v1/artifacts/{args.id}/content", timeout=None
                    ) as response:
                        if response.status_code >= 400:
                            return show(response)
                        with temporary.open("xb") as handle:
                            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                                handle.write(chunk)
                    temporary.replace(output)
                finally:
                    temporary.unlink(missing_ok=True)
                print(args.out)
                return 0
        if operation == "edit":
            edit_action = args.edit_action
            if edit_action == "gc":
                return show(http.post("/api/v1/artifacts/gc"))
            if edit_action in {"trash", "restore"}:
                return show(http.post(f"/api/v1/artifacts/{args.id}/{edit_action}"))
            if edit_action == "favorite":
                return show(
                    http.patch(
                        f"/api/v1/artifacts/{args.id}",
                        json={"favorite": args.enabled},
                    )
                )
            return 2
    return 2


def cmd_run(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        if args.run_action == "list":
            return show(http.get("/api/v1/queue"))
        control_action = args.control_action
        response = http.get(f"/api/v1/runs/{args.id}")
        if response.status_code >= 400:
            return show(response)
        if control_action == "show":
            return show(response)
        if control_action == "wait":
            return wait_for_run(http, response.json())
        return show(http.post(f"/api/v1/runs/{args.id}/{control_action}"))


def cmd_dev(args: argparse.Namespace) -> int:
    try:
        if args.dev_action == "check":
            check_project(args.project_dir)
            check_dependencies()
            report = check_tool_packages(args.runtime_url)
            report["generated"] = check_generated()
            report["environment"] = {
                "cooksprite": "locked",
                "comfyui": "locked",
                "project": str(Path(args.project_dir).resolve()),
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.dev_action == "package":
            package_action = args.package_action
            if package_action == "sync":
                print(json.dumps({"written": sync_generated()}, indent=2))
                return 0
            if package_action == "web":
                print(json.dumps({"static": sync_static_web()}, indent=2))
                return 0
            if package_action == "verify":
                print(json.dumps(verify_distribution(args.archive), indent=2))
                return 0
            if package_action == "lock":
                lock_project(args.project_dir)
                sync_node_requirements(args.project_dir)
                lock_dependencies(
                    progress=lambda message, value: print(
                        f"{value:>6.1%} {message}", file=sys.stderr
                    )
                )
                print(json.dumps({"cooksprite": "uv.lock", "comfyui": "requirements.lock"}))
                return 0
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _worker_runtime_dir(args: argparse.Namespace) -> Path:
    """Resolve the dedicated worker-runtime sibling from a source clone."""

    if args.runtime_dir:
        return Path(args.runtime_dir).expanduser().resolve()
    source = Path(getattr(args, "source_dir", None) or ".").expanduser().resolve()
    return default_runtime_dir(source)


def cmd_worker(args: argparse.Namespace) -> int:
    """Operate one managed ComfyUI worker without starting a product API."""

    operation = args.worker_action
    try:
        runtime_dir = _worker_runtime_dir(args)
        if operation == "init":
            config = initialize_worker(
                args.source_dir,
                runtime_dir=runtime_dir,
                host=args.host,
                port=args.port,
                device=args.device,
                exclusive=args.exclusive,
                branch=args.branch,
                force=args.force,
            )
            result: dict[str, Any] = {"initialized": True, "config": config.__dict__}
        else:
            config = WorkerConfig.load(runtime_dir)
            if operation == "install":
                result = install_worker(config, python_executable=args.python)
            elif operation == "sync":
                result = sync_worker(config)
            elif operation == "start":
                result = start_worker(config, timeout=args.timeout)
            elif operation == "stop":
                result = stop_worker(config)
            elif operation == "restart":
                result = restart_worker(config, timeout=args.timeout)
            elif operation == "status":
                result = worker_status(config)
            elif operation == "doctor":
                result = doctor_worker(config)
                if not result["ok"]:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 1
            else:  # pragma: no cover - argparse constrains every action.
                raise WorkerError(f"unsupported worker action: {operation}")
    except (OSError, RuntimeError, WorkerError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    if args.json or operation in {"init", "install", "sync", "status", "doctor"}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif operation == "start":
        print(
            f"CookSprite worker ready at {result.get('runtime_identity', {}).get('comfy_url', '')}"
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_service(args: argparse.Namespace) -> int:
    """Manage the detached CookSprite API/Web daemon."""

    try:
        if args.service_action == "start":
            data_dir = save_data_dir(args.data_dir) if args.data_dir else resolve_data_dir()
            result = start_service(
                data_dir,
                host=args.host,
                port=args.port,
                serve_frontend=not args.no_frontend,
                public_api_url=args.public_api_url,
                restart=args.restart,
            )
        elif args.service_action == "stop":
            result = stop_service(timeout=args.timeout)
        elif args.service_action == "status":
            result = service_status()
        else:  # pragma: no cover - argparse constrains every action.
            raise ServiceError(f"unsupported service action: {args.service_action}")
    except (OSError, ServiceError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_comfy(args: argparse.Namespace) -> int:
    if args.comfy_action == "connect":
        connect_action = args.connect_action
        if connect_action == "probe":
            with client(args.api) as http:
                return show(http.post("/api/v1/comfyui/probe", json={"base_url": args.url}))
        if connect_action == "select":
            with client(args.api) as http:
                return show(http.post(f"/api/v1/runtimes/{args.runtime}/select"))
        if connect_action == "import":
            if args.location == "remote" and not args.callback_url:
                print(
                    "remote ComfyUI import requires --callback-url reachable from that runtime",
                    file=sys.stderr,
                )
                return 2
            with client(args.api) as http:
                return show(
                    http.post(
                        "/api/v1/runtimes",
                        json={
                            "id": args.runtime,
                            "label": args.label,
                            "base_url": args.url,
                            "location": args.location,
                            "transport": args.transport,
                            "callback_url": args.callback_url,
                            "worker_managed": args.worker_managed,
                        },
                    )
                )
        return 2
    if args.comfy_action != "inspect":
        return 2
    inspect_action = args.inspect_action
    if inspect_action == "doctor":
        with client(args.api) as http:
            response = http.post(f"/api/v1/runtimes/{args.runtime}/doctor")
        if response.status_code >= 400 or args.json:
            return show(response)
        report = response.json()
        system = report.get("system", {})
        print(
            f"{report['runtime_id']} ready · ComfyUI {system.get('comfyui_version', '?')} · "
            f"{report.get('recipe_count', len(report.get('recipes', [])))} recipe(s) · "
            f"{report.get('tool_count', '?')} node(s)"
        )
        for recipe in report.get("recipes", []):
            print(f"  {recipe['id']:<34} {recipe['label']} [{', '.join(recipe['modes'])}]")
        return 0
    if inspect_action == "capabilities":
        with client(args.api) as http:
            return show(http.get(f"/api/v1/runtimes/{args.runtime}/capabilities"))
    if inspect_action == "defaults" and getattr(args, "defaults_command", None) == "set":
        with client(args.api) as http:
            return show(
                http.put(
                    f"/api/v1/runtimes/{args.runtime}/defaults/{args.action_id}",
                    json={"model_id": args.model},
                )
            )
    if inspect_action == "defaults":
        if not args.runtime:
            print("comfy defaults requires --runtime", file=sys.stderr)
            return 2
        with client(args.api) as http:
            return show(http.get(f"/api/v1/runtimes/{args.runtime}/defaults"))
    if inspect_action == "recipe":
        body = json.loads(Path(args.file).read_text(encoding="utf-8"))
        with client(args.api) as http:
            return show(http.post(f"/api/v1/runtimes/{args.runtime}/recipes", json=body))
    return 2


def cmd_data(args: argparse.Namespace) -> int:
    try:
        report = (
            verify_data_dir(args.data_dir)
            if args.data_action == "verify"
            else migrate_data_dir(args.source, args.target)
        )
    except DataMigrationError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cspr")
    root.add_argument("--api", help="CookSprite API URL; defaults to config or localhost")
    root.add_argument("--lang", choices=["en", "zh-CN"], default="en")
    commands = root.add_subparsers(dest="command", required=True)

    service = commands.add_parser("service", help="manage the CookSprite API/Web daemon")
    service_commands = service.add_subparsers(dest="service_action", required=True)
    service_start = service_commands.add_parser("start", help="start in the background")
    service_start.add_argument("--data-dir")
    service_start.add_argument("--host", default="127.0.0.1")
    service_start.add_argument("--port", type=int, default=8000)
    service_start.add_argument("--no-frontend", action="store_true")
    service_start.add_argument("--public-api-url")
    service_start.add_argument("--restart", action="store_true")
    service_start.add_argument("--json", action="store_true")
    service_start.set_defaults(func=cmd_service)
    service_stop = service_commands.add_parser("stop", help="stop the owned daemon")
    service_stop.add_argument("--timeout", type=float, default=10)
    service_stop.add_argument("--json", action="store_true")
    service_stop.set_defaults(func=cmd_service)
    service_status_command = service_commands.add_parser("status", help="show daemon status")
    service_status_command.add_argument("--json", action="store_true")
    service_status_command.set_defaults(func=cmd_service)

    action = commands.add_parser("action", help="list, show, or run one Action")
    action.add_argument("--json", action="store_true")
    action_commands = action.add_subparsers(dest="action_command")
    action.set_defaults(func=cmd_actions)
    describe = action_commands.add_parser("show")
    describe.add_argument("id")
    describe.add_argument("--json", action="store_true")
    describe.set_defaults(func=cmd_action_describe)
    run = action_commands.add_parser("run")
    run.add_argument("id")
    run.add_argument("--project", required=True)
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--value", action="append", default=[])
    run.add_argument("--param", action="append", default=[])
    run.add_argument("--wait", action="store_true")
    run.set_defaults(func=cmd_action_run)

    project = commands.add_parser("project", help="list and manage projects")
    project.add_argument("--gallery", action="store_true", help="show published projects")
    project_commands = project.add_subparsers(dest="project_action")
    project.set_defaults(func=cmd_project)
    create = project_commands.add_parser("create")
    create.add_argument("--name", default="")
    create.add_argument("--type", choices=["static", "character", "tileset"], default="static")
    create.set_defaults(func=cmd_project)
    project_manage = project_commands.add_parser("manage")
    project_manage.add_argument("id")
    manage_commands = project_manage.add_subparsers(dest="manage_action", required=True)
    project_show = manage_commands.add_parser("show")
    project_show.set_defaults(func=cmd_project)
    project_update = manage_commands.add_parser("update")
    project_update.add_argument("--name")
    project_update.add_argument("--type", choices=["static", "character", "tileset"])
    favorite = project_update.add_mutually_exclusive_group()
    favorite.add_argument("--favorite", action="store_true", dest="favorite")
    favorite.add_argument("--unfavorite", action="store_false", dest="favorite")
    project_update.set_defaults(func=cmd_project, favorite=None)
    project_publish = manage_commands.add_parser("publish")
    project_publish.add_argument("--cover")
    project_publish.set_defaults(func=cmd_project)
    project_sequence = manage_commands.add_parser("sequence")
    project_sequence.add_argument(
        "--clip",
        required=True,
        choices=["idle", "walk", "run", "attack", "cast", "hit", "jump", "death"],
    )
    project_sequence.add_argument("--view", required=True, choices=["level", "top45"])
    project_sequence.add_argument(
        "--direction", required=True, choices=["n", "ne", "e", "se", "s", "sw", "w", "nw"]
    )
    project_sequence.set_defaults(func=cmd_project)
    project_document = manage_commands.add_parser("document")
    document_commands = project_document.add_subparsers(dest="document_action", required=True)
    document_get = document_commands.add_parser("get")
    document_get.add_argument("--out")
    document_get.set_defaults(func=cmd_project)
    document_put = document_commands.add_parser("put")
    document_put.add_argument("file")
    document_put.add_argument("--etag", required=True)
    document_put.set_defaults(func=cmd_project)
    project_export = project_commands.add_parser("export")
    project_export.add_argument("id")
    project_export.add_argument("--allow-incomplete", action="store_true")
    project_export.add_argument("--wait", action="store_true")
    project_export.set_defaults(func=cmd_project)
    artifact = commands.add_parser("artifact", help="list, upload, and edit artifacts")
    artifact.add_argument("--project")
    artifact.add_argument("--kind")
    artifact.add_argument("--trashed", action="store_true")
    artifact.add_argument("--search", default="")
    artifact_commands = artifact.add_subparsers(dest="artifact_action")
    artifact.set_defaults(func=cmd_artifact)
    upload = artifact_commands.add_parser("upload")
    upload.add_argument("file")
    upload.add_argument("--project", required=True)
    upload.add_argument(
        "--kind",
        choices=["Image", "SpriteSheet", "Video", "NormalMap", "FrameSeq"],
        default="Image",
    )
    upload.add_argument("--media-type")
    upload.add_argument("--title")
    upload.set_defaults(func=cmd_artifact)
    artifact_get = artifact_commands.add_parser("get")
    artifact_get.add_argument("id")
    artifact_get.set_defaults(func=cmd_artifact, artifact_action="get", get_action="show")
    get_commands = artifact_get.add_subparsers(dest="get_action")
    get_show = get_commands.add_parser("show")
    get_show.set_defaults(func=cmd_artifact)
    sequence = get_commands.add_parser("sequence", help="expand a FrameSeq manifest")
    sequence.set_defaults(func=cmd_artifact)
    artifact_download = get_commands.add_parser("download")
    artifact_download.add_argument("--out", required=True)
    artifact_download.set_defaults(func=cmd_artifact)
    artifact_edit = artifact_commands.add_parser("edit")
    edit_commands = artifact_edit.add_subparsers(dest="edit_action", required=True)
    for operation in ("trash", "restore"):
        command = edit_commands.add_parser(operation)
        command.add_argument("id")
        command.set_defaults(func=cmd_artifact, artifact_action="edit")
    artifact_gc = edit_commands.add_parser("gc", help="remove unreferenced Blob files")
    artifact_gc.set_defaults(func=cmd_artifact, artifact_action="edit")
    artifact_favorite = edit_commands.add_parser("favorite")
    artifact_favorite.add_argument("id")
    artifact_favorite.add_argument(
        "--off", action="store_false", dest="enabled", help="remove favorite"
    )
    artifact_favorite.set_defaults(func=cmd_artifact, artifact_action="edit", enabled=True)

    runs = commands.add_parser("run", help="list and control Runs")
    run_commands = runs.add_subparsers(dest="run_action", required=True)
    run_list = run_commands.add_parser("list", help="show the queue and recent Runs")
    run_list.set_defaults(func=cmd_run)
    run_control = run_commands.add_parser("control")
    run_control.add_argument("id")
    control_commands = run_control.add_subparsers(dest="control_action", required=True)
    for operation in ("show", "wait", "cancel", "retry"):
        command = control_commands.add_parser(operation)
        command.set_defaults(func=cmd_run)

    data = commands.add_parser("data", help="verify or relocate CookSprite API data")
    data_commands = data.add_subparsers(dest="data_action", required=True)
    data_verify = data_commands.add_parser("verify")
    data_verify.add_argument("data_dir")
    data_verify.set_defaults(func=cmd_data)
    data_migrate = data_commands.add_parser("migrate")
    data_migrate.add_argument("--from", required=True, dest="source")
    data_migrate.add_argument("--to", required=True, dest="target")
    data_migrate.set_defaults(func=cmd_data)

    comfy = commands.add_parser("comfy", help="connect, inspect, and manage ComfyUI")
    comfy_commands = comfy.add_subparsers(dest="comfy_action", required=True)
    connect = comfy_commands.add_parser("connect", help="connect to an existing ComfyUI")
    connect_commands = connect.add_subparsers(dest="connect_action", required=True)
    comfy_import = connect_commands.add_parser("import")
    comfy_import.add_argument("--runtime")
    comfy_import.add_argument("--label", default="ComfyUI")
    comfy_import.add_argument("--url", required=True)
    comfy_import.add_argument("--location", choices=["local", "remote"], default="remote")
    comfy_import.add_argument("--transport", default="http")
    comfy_import.add_argument("--callback-url")
    comfy_import.add_argument(
        "--worker-managed",
        action="store_true",
        help="require a CookSprite worker runtime identity at doctor time",
    )
    comfy_import.set_defaults(func=cmd_comfy)
    comfy_probe = connect_commands.add_parser("probe", help="probe ComfyUI at an explicit URL")
    comfy_probe.add_argument("--url", required=True, help="explicit ComfyUI URL")
    comfy_probe.set_defaults(func=cmd_comfy)
    comfy_select = connect_commands.add_parser("select", help="select the active ComfyUI runtime")
    comfy_select.add_argument("--runtime", required=True)
    comfy_select.set_defaults(func=cmd_comfy)

    inspect = comfy_commands.add_parser("inspect", help="inspect Runtime capabilities and recipes")
    inspect_commands = inspect.add_subparsers(dest="inspect_action", required=True)
    comfy_doctor = inspect_commands.add_parser("doctor")
    comfy_doctor.add_argument("--runtime", required=True)
    comfy_doctor.add_argument("--json", action="store_true")
    comfy_doctor.set_defaults(func=cmd_comfy)
    comfy_capabilities = inspect_commands.add_parser(
        "capabilities", help="show one runtime's discovered capabilities"
    )
    comfy_capabilities.add_argument("--runtime", required=True)
    comfy_capabilities.set_defaults(func=cmd_comfy)
    comfy_defaults = inspect_commands.add_parser(
        "defaults", help="show or set per-runtime Action defaults"
    )
    comfy_defaults.add_argument("--runtime")
    comfy_defaults.add_argument("--action", dest="action_id")
    comfy_defaults.add_argument("--model")
    defaults_commands = comfy_defaults.add_subparsers(dest="defaults_command")
    defaults_set = defaults_commands.add_parser("set", help="set one Action default")
    defaults_set.add_argument("--runtime", required=True)
    defaults_set.add_argument("--action", dest="action_id", required=True)
    defaults_set.add_argument("--model", required=True)
    defaults_set.set_defaults(func=cmd_comfy)
    comfy_defaults.set_defaults(func=cmd_comfy)
    comfy_recipe = inspect_commands.add_parser(
        "recipe", help="register an API-format workflow recipe"
    )
    comfy_recipe.add_argument("--runtime", required=True)
    comfy_recipe.add_argument("file")
    comfy_recipe.set_defaults(func=cmd_comfy)

    worker = comfy_commands.add_parser(
        "worker",
        help="install and manage one ComfyUI worker; never starts the product API",
    )
    worker_commands = worker.add_subparsers(dest="worker_action", required=True)

    worker_init = worker_commands.add_parser("init", help="write one non-secret worker manifest")
    worker_init.add_argument("--source-dir", default=".", help="CookSprite Git worktree root")
    worker_init.add_argument(
        "--runtime-dir",
        help=f"managed ComfyUI runtime; defaults to ../{DEFAULT_RUNTIME_DIR_NAME}",
    )
    worker_init.add_argument("--branch", help="must equal the checked-out Git branch")
    worker_init.add_argument("--host", default="127.0.0.1")
    worker_init.add_argument(
        "--port",
        type=int,
        default=DEFAULT_WORKER_PORT,
        help=f"dedicated ComfyUI listener port (default: {DEFAULT_WORKER_PORT})",
    )
    worker_init.add_argument(
        "--device",
        default="auto",
        help="ComfyUI device preference: auto, cpu, cuda[:N], rocm[:N], or mps",
    )
    worker_init.add_argument(
        "--exclusive",
        action="store_true",
        help="require a registered resource inspector to prove exclusive ownership",
    )
    worker_init.add_argument("--force", action="store_true")
    worker_init.add_argument("--json", action="store_true")
    worker_init.set_defaults(func=cmd_worker)

    for name, help_text in (
        ("install", "explicitly install the managed ComfyUI runtime without models"),
        ("sync", "Git fast-forward and atomically synchronize a stopped worker"),
        ("start", "start the configured ComfyUI worker"),
        ("stop", "stop only the configured ComfyUI worker"),
        ("restart", "restart only an idle configured worker"),
        ("status", "show read-only worker state"),
        ("doctor", "validate source, runtime identity, node pack, and ComfyUI"),
    ):
        command = worker_commands.add_parser(name, help=help_text)
        command.add_argument(
            "--runtime-dir",
            help=f"managed runtime; defaults to ../{DEFAULT_RUNTIME_DIR_NAME}",
        )
        command.add_argument("--json", action="store_true")
        if name == "install":
            command.add_argument("--python")
        if name in {"start", "restart"}:
            command.add_argument("--timeout", type=float, default=180)
        command.set_defaults(func=cmd_worker)

    dev = commands.add_parser("dev", help="check, lock, and package CookSprite")
    dev_commands = dev.add_subparsers(dest="dev_action", required=True)
    dev_check = dev_commands.add_parser("check")
    dev_check.add_argument("--runtime-url")
    dev_check.add_argument("--project-dir", default=".")
    dev_check.set_defaults(func=cmd_dev)
    dev_package = dev_commands.add_parser("package")
    package_commands = dev_package.add_subparsers(dest="package_action", required=True)
    dev_sync = package_commands.add_parser("sync")
    dev_sync.set_defaults(func=cmd_dev)
    dev_bundle_web = package_commands.add_parser("web")
    dev_bundle_web.set_defaults(func=cmd_dev)
    dev_verify_dist = package_commands.add_parser("verify")
    dev_verify_dist.add_argument("archive")
    dev_verify_dist.set_defaults(func=cmd_dev)
    env_lock = package_commands.add_parser("lock")
    env_lock.add_argument("--project-dir", default=".")
    env_lock.set_defaults(func=cmd_dev)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
