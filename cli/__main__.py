"""Thin HTTP CLI used directly by people and by the CookSprite agent Skill."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from cooksprite.client import CookSpriteClient
from cooksprite.comfy.managed import (
    install,
    install_node_pack,
    launch,
    wait_until_ready,
)
from cooksprite.dev import check_generated, check_tool_packages, sync_generated

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
        if args.action == "list":
            return show(http.get("/api/v1/projects"))
        if args.action == "show":
            return show(http.get(f"/api/v1/projects/{args.id}"))
        if args.action == "sequence":
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
        if args.action == "update":
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
        if args.action == "publish":
            return show(
                http.post(
                    f"/api/v1/projects/{args.id}/publish",
                    json={"cover_artifact_id": args.cover},
                )
            )
        if args.action == "export":
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
        return show(
            http.post(
                "/api/v1/projects",
                json={"name": args.name, "type": args.type},
            )
        )


def cmd_document(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        if args.action == "get":
            response = http.get(f"/api/v1/projects/{args.project}/document")
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
                f"/api/v1/projects/{args.project}/document",
                json=document,
                headers={"If-Match": args.etag},
            )
        )


def cmd_artifact(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        if args.action == "sequence":
            return show(http.get(f"/api/v1/artifacts/{args.id}/sequence"))
        if args.action == "list":
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
        if args.action == "show":
            return show(http.get(f"/api/v1/artifacts/{args.id}"))
        if args.action == "download":
            response = http.get(f"/api/v1/artifacts/{args.id}/content")
            if response.status_code >= 400:
                return show(response)
            Path(args.out).write_bytes(response.content)
            print(args.out)
            return 0
        if args.action in {"trash", "restore"}:
            return show(http.post(f"/api/v1/artifacts/{args.id}/{args.action}"))
        if args.action == "favorite":
            return show(
                http.patch(
                    f"/api/v1/artifacts/{args.id}",
                    json={"favorite": args.enabled},
                )
            )
        path = Path(args.file)
        media_type = (
            args.media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        response = http.post(
            "/api/v1/artifacts",
            params={
                "project_id": args.project,
                "kind": args.kind,
                "media_type": media_type,
                "title": args.title or path.name,
            },
            content=path.read_bytes(),
        )
    return show(response)


def cmd_run(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        response = http.get(f"/api/v1/runs/{args.id}")
        if response.status_code >= 400:
            return show(response)
        if args.action == "show":
            return show(response)
        if args.action == "wait":
            return wait_for_run(http, response.json())
        return show(http.post(f"/api/v1/runs/{args.id}/{args.action}"))


def cmd_simple_get(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        return show(http.get(f"/api/v1/{args.path}"))


def cmd_dev(args: argparse.Namespace) -> int:
    try:
        if args.action == "sync":
            print(json.dumps({"written": sync_generated()}, indent=2))
            return 0
        report = check_tool_packages(args.runtime_url)
        report["generated"] = check_generated()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def cmd_install(args: argparse.Namespace) -> int:
    target = install(
        args.dir,
        python_executable=args.python,
        progress=lambda message, value: print(f"{value:>6.1%} {message}", file=sys.stderr),
    )
    print(json.dumps({"comfyui": str(target), "api": "cspr start"}, indent=2))
    return 0


def _port_is_listening(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.15)
        return sock.connect_ex((probe_host, port)) == 0


def _next_available_port(
    host: str,
    preferred: int,
    *,
    reserved: set[int] | None = None,
    attempts: int = 100,
) -> int:
    reserved = reserved or set()
    for port in range(preferred, preferred + attempts):
        if port not in reserved and not _port_is_listening(host, port):
            return port
    raise RuntimeError(
        f"no available port found near {host}:{preferred}; tried {attempts} ports"
    )


def _frontend_dir(requested: str | None) -> Path:
    candidates = []
    if requested:
        candidates.append(Path(requested).expanduser())
    candidates.extend(
        [
            Path.cwd() / "web",
            Path(__file__).resolve().parents[1] / "web",
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "package.json").is_file():
            return resolved
    searched = ", ".join(str(candidate.resolve()) for candidate in candidates)
    raise RuntimeError(
        "CookSprite frontend source was not found; pass --frontend-dir <web> "
        f"or run the packaged frontend separately. searched: {searched}"
    )


def _start_frontend(
    args: argparse.Namespace,
    *,
    frontend_port: int,
    api_port: int,
) -> subprocess.Popen[bytes]:
    frontend_root = _frontend_dir(args.frontend_dir)
    npm = shutil.which(args.npm)
    if not npm:
        raise RuntimeError(
            f"{args.npm} was not found; install Node.js/npm or pass --no-frontend "
            "to start only the API and ComfyUI"
        )
    command = [
        npm,
        "run",
        "dev",
        "--",
        "--host",
        args.frontend_host,
        "--port",
        str(frontend_port),
        "--strictPort",
    ]
    environment = os.environ.copy()
    proxy_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    environment["COOKSPRITE_API_PROXY_TARGET"] = f"http://{proxy_host}:{api_port}"
    print(
        f"Starting CookSprite frontend at http://127.0.0.1:{frontend_port} "
        f"from {frontend_root}",
        file=sys.stderr,
    )
    return subprocess.Popen(
        command,
        cwd=frontend_root,
        env=environment,
        start_new_session=os.name != "nt",
    )


def _stop_frontend(process: subprocess.Popen[bytes] | None) -> None:
    if not process or process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()


def cmd_start(args: argparse.Namespace) -> int:
    import uvicorn

    from cooksprite.api.app import create_app

    api_port = _next_available_port(args.host, args.port)
    frontend_port = None
    if not args.no_frontend:
        frontend_port = _next_available_port(
            args.frontend_host,
            args.frontend_port,
            reserved={api_port},
        )
    comfy_url = None if args.no_comfy else args.comfy_url
    runtime_location = "local" if not args.comfy_url else args.runtime_location
    runtime_transport = "local-process" if not args.comfy_url else args.runtime_transport
    if not args.no_comfy and not comfy_url:
        comfy_port = _next_available_port(
            args.comfy_host,
            args.comfy_port,
            reserved={api_port, frontend_port} if frontend_port else {api_port},
        )
        launch(args.dir, host=args.comfy_host, port=comfy_port, cuda_device=args.cuda_device)
        comfy_url = f"http://{args.comfy_host}:{comfy_port}"
    if comfy_url:
        wait_until_ready(comfy_url, timeout=args.timeout)
    api_base = f"http://{args.host}:{api_port}"

    def register_runtime() -> None:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            try:
                with CookSpriteClient(api_base) as http:
                    created = http.post(
                        "/api/v1/runtimes",
                        json={
                            "id": args.runtime,
                            "label": args.label,
                            "base_url": comfy_url,
                            "location": runtime_location,
                            "transport": runtime_transport,
                            "callback_url": f"{api_base}/api/v1",
                        },
                    )
                    if created.status_code < 400:
                        http.post(f"/api/v1/runtimes/{args.runtime}/doctor").raise_for_status()
                        return
            except (httpx.HTTPError, OSError):
                time.sleep(0.25)

    frontend_process = None
    try:
        if comfy_url:
            threading.Thread(target=register_runtime, daemon=True).start()
        if not args.no_frontend:
            frontend_process = _start_frontend(
                args,
                frontend_port=frontend_port,
                api_port=api_port,
            )
        print(f"Starting CookSprite API at {api_base}", file=sys.stderr)
        previous_public_api_url = os.environ.get("COOKSPRITE_PUBLIC_API_URL")
        os.environ["COOKSPRITE_PUBLIC_API_URL"] = f"{api_base}/api/v1"
        try:
            uvicorn.run(create_app(args.data_dir), host=args.host, port=api_port)
        finally:
            if previous_public_api_url is None:
                os.environ.pop("COOKSPRITE_PUBLIC_API_URL", None)
            else:
                os.environ["COOKSPRITE_PUBLIC_API_URL"] = previous_public_api_url
        return 0
    finally:
        _stop_frontend(frontend_process)


def cmd_list(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        return show(http.get(f"/api/v1/{args.kind}"))


def cmd_contributor_run(args: argparse.Namespace) -> int:
    inputs = {key: {"literal": value} for key, value in parse_pairs(args.input).items()}
    payload = {
        "target": {"kind": args.kind, "id": args.id, "revision": args.revision},
        "runtime_id": args.runtime,
        "inputs": inputs,
    }
    with client(args.api) as http:
        response = http.post("/api/v1/runs", json=payload)
        if response.status_code >= 400:
            return show(response)
        run = response.json()
        if not args.wait:
            print(json.dumps(run, indent=2))
            return 0
        return wait_for_run(http, run)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("cooksprite.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_comfy(args: argparse.Namespace) -> int:
    if args.action == "install":
        print(
            install(
                args.dir,
                python_executable=args.python,
                progress=lambda message, value: print(f"{value:>6.1%} {message}", file=sys.stderr),
            )
        )
        return 0
    if args.action == "install-nodes":
        print(install_node_pack(args.dir, install_dependencies=not args.no_deps))
        return 0
    if args.action == "doctor":
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
    if args.action == "probe-local":
        with client(args.api) as http:
            return show(http.post("/api/v1/local/probe"))
    if args.action == "select":
        with client(args.api) as http:
            return show(http.post(f"/api/v1/runtimes/{args.runtime}/select"))
    if args.action == "capabilities":
        with client(args.api) as http:
            return show(http.get(f"/api/v1/runtimes/{args.runtime}/capabilities"))
    if args.action == "defaults" and getattr(args, "defaults_command", None) == "set":
        with client(args.api) as http:
            return show(
                http.put(
                    f"/api/v1/runtimes/{args.runtime}/defaults/{args.action_id}",
                    json={"workflow_id": args.workflow, "model_id": args.model},
                )
            )
    if args.action == "defaults":
        if not args.runtime:
            print("comfy defaults requires --runtime", file=sys.stderr)
            return 2
        with client(args.api) as http:
            return show(http.get(f"/api/v1/runtimes/{args.runtime}/defaults"))
    if args.action == "import":
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
                    },
                )
            )
    if args.action == "run":
        pid = launch(
            args.dir,
            host=args.host,
            port=args.port,
            cuda_device=args.cuda_device,
        )
        print(json.dumps({"pid": pid, "url": f"http://{args.host}:{args.port}"}))
        return 0
    if args.action == "recipe":
        body = json.loads(Path(args.file).read_text(encoding="utf-8"))
        with client(args.api) as http:
            return show(http.post(f"/api/v1/runtimes/{args.runtime}/recipes", json=body))
    return 2


def cmd_gc(args: argparse.Namespace) -> int:
    with client(args.api) as http:
        return show(http.post("/api/v1/artifacts/gc"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cspr")
    root.add_argument("--api", help="CookSprite API URL; defaults to config or localhost")
    root.add_argument("--lang", choices=["en", "zh-CN"], default="en")
    commands = root.add_subparsers(dest="command", required=True)

    action_list = commands.add_parser("actions", help="list registered Actions")
    action_list.add_argument("--json", action="store_true")
    action_list.set_defaults(func=cmd_actions)

    action = commands.add_parser("action", help="inspect or run one Action")
    action_commands = action.add_subparsers(dest="action_command", required=True)
    describe = action_commands.add_parser("describe")
    describe.add_argument("id")
    describe.add_argument("--json", action="store_true")
    describe.set_defaults(func=cmd_action_describe)
    run = action_commands.add_parser("run")
    run.add_argument("id")
    run.add_argument("--project", required=True)
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--value", action="append", default=[])
    run.add_argument("--wait", action="store_true")
    run.set_defaults(func=cmd_action_run)

    project = commands.add_parser("project")
    project_commands = project.add_subparsers(dest="action", required=True)
    create = project_commands.add_parser("create")
    create.add_argument("--name", default="")
    create.add_argument("--type", choices=["static", "character", "tileset"], default="static")
    create.set_defaults(func=cmd_project)
    project_list = project_commands.add_parser("list")
    project_list.set_defaults(func=cmd_project)
    project_show = project_commands.add_parser("show")
    project_show.add_argument("id")
    project_show.set_defaults(func=cmd_project)
    project_update = project_commands.add_parser("update")
    project_update.add_argument("id")
    project_update.add_argument("--name")
    project_update.add_argument("--type", choices=["static", "character", "tileset"])
    favorite = project_update.add_mutually_exclusive_group()
    favorite.add_argument("--favorite", action="store_true", dest="favorite")
    favorite.add_argument("--unfavorite", action="store_false", dest="favorite")
    project_update.set_defaults(func=cmd_project, favorite=None)
    project_publish = project_commands.add_parser("publish")
    project_publish.add_argument("id")
    project_publish.add_argument("--cover")
    project_publish.set_defaults(func=cmd_project)
    project_export = project_commands.add_parser("export")
    project_export.add_argument("id")
    project_export.add_argument("--allow-incomplete", action="store_true")
    project_export.add_argument("--wait", action="store_true")
    project_export.set_defaults(func=cmd_project)
    project_sequence = project_commands.add_parser(
        "sequence", help="materialize one curated document track as FrameSeq"
    )
    project_sequence.add_argument("id")
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

    document = commands.add_parser("document")
    document_commands = document.add_subparsers(dest="action", required=True)
    document_get = document_commands.add_parser("get")
    document_get.add_argument("project")
    document_get.add_argument("--out")
    document_get.set_defaults(func=cmd_document)
    document_put = document_commands.add_parser("put")
    document_put.add_argument("project")
    document_put.add_argument("file")
    document_put.add_argument("--etag", required=True)
    document_put.set_defaults(func=cmd_document)

    artifact = commands.add_parser("artifact")
    artifact_commands = artifact.add_subparsers(dest="action", required=True)
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
    sequence = artifact_commands.add_parser("sequence", help="expand a FrameSeq manifest")
    sequence.add_argument("id")
    sequence.set_defaults(func=cmd_artifact)
    artifact_list = artifact_commands.add_parser("list")
    artifact_list.add_argument("--project")
    artifact_list.add_argument("--kind")
    artifact_list.add_argument("--trashed", action="store_true")
    artifact_list.add_argument("--search", default="")
    artifact_list.set_defaults(func=cmd_artifact)
    artifact_show = artifact_commands.add_parser("show")
    artifact_show.add_argument("id")
    artifact_show.set_defaults(func=cmd_artifact)
    artifact_download = artifact_commands.add_parser("download")
    artifact_download.add_argument("id")
    artifact_download.add_argument("--out", required=True)
    artifact_download.set_defaults(func=cmd_artifact)
    for operation in ("trash", "restore"):
        command = artifact_commands.add_parser(operation)
        command.add_argument("id")
        command.set_defaults(func=cmd_artifact)
    artifact_favorite = artifact_commands.add_parser("favorite")
    artifact_favorite.add_argument("id")
    artifact_favorite.add_argument(
        "--off", action="store_false", dest="enabled", help="remove favorite"
    )
    artifact_favorite.set_defaults(func=cmd_artifact, enabled=True)

    runs = commands.add_parser("run", help="show, wait, cancel, or retry a Run")
    run_commands = runs.add_subparsers(dest="action", required=True)
    for operation in ("show", "wait", "cancel", "retry"):
        command = run_commands.add_parser(operation)
        command.add_argument("id")
        command.set_defaults(func=cmd_run)

    queue = commands.add_parser("queue")
    queue.set_defaults(func=cmd_simple_get, path="queue")
    gallery = commands.add_parser("gallery")
    gallery.set_defaults(func=cmd_simple_get, path="gallery")

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    contributor_list = commands.add_parser("list")
    contributor_list.add_argument("kind", choices=["tools", "workflows", "tasks", "runtimes"])
    contributor_list.set_defaults(func=cmd_list)
    contributor_run = commands.add_parser("contributor-run")
    contributor_run.add_argument("kind", choices=["workflow", "task"])
    contributor_run.add_argument("id")
    contributor_run.add_argument("--revision", type=int, required=True)
    contributor_run.add_argument("--runtime", required=True)
    contributor_run.add_argument("--input", action="append", default=[])
    contributor_run.add_argument("--wait", action="store_true")
    contributor_run.set_defaults(func=cmd_contributor_run)

    gc = commands.add_parser("gc")
    gc.set_defaults(func=cmd_gc)

    comfy = commands.add_parser("comfy")
    comfy_commands = comfy.add_subparsers(dest="action", required=True)
    comfy_install = comfy_commands.add_parser("install")
    comfy_install.add_argument("dir")
    comfy_install.add_argument("--python")
    comfy_install.set_defaults(func=cmd_comfy)
    comfy_nodes = comfy_commands.add_parser("install-nodes")
    comfy_nodes.add_argument("dir", help="existing ComfyUI directory or managed root")
    comfy_nodes.add_argument("--no-deps", action="store_true")
    comfy_nodes.set_defaults(func=cmd_comfy)
    comfy_import = comfy_commands.add_parser("import")
    comfy_import.add_argument("--runtime", required=True)
    comfy_import.add_argument("--label", default="ComfyUI")
    comfy_import.add_argument("--url", required=True)
    comfy_import.add_argument("--location", choices=["local", "remote"], default="remote")
    comfy_import.add_argument("--transport", default="http")
    comfy_import.add_argument("--callback-url")
    comfy_import.set_defaults(func=cmd_comfy)
    comfy_doctor = comfy_commands.add_parser("doctor")
    comfy_doctor.add_argument("--runtime", required=True)
    comfy_doctor.add_argument("--json", action="store_true")
    comfy_doctor.set_defaults(func=cmd_comfy)
    comfy_probe = comfy_commands.add_parser("probe-local", help="probe ComfyUI on the API host")
    comfy_probe.set_defaults(func=cmd_comfy)
    comfy_select = comfy_commands.add_parser("select", help="select the active ComfyUI runtime")
    comfy_select.add_argument("--runtime", required=True)
    comfy_select.set_defaults(func=cmd_comfy)
    comfy_capabilities = comfy_commands.add_parser(
        "capabilities", help="show one runtime's discovered capabilities"
    )
    comfy_capabilities.add_argument("--runtime", required=True)
    comfy_capabilities.set_defaults(func=cmd_comfy)
    comfy_defaults = comfy_commands.add_parser("defaults", help="show or set per-runtime Action defaults")
    comfy_defaults.add_argument("--runtime")
    comfy_defaults.add_argument("--action", dest="action_id")
    comfy_defaults.add_argument("--workflow")
    comfy_defaults.add_argument("--model")
    defaults_commands = comfy_defaults.add_subparsers(dest="defaults_command")
    defaults_set = defaults_commands.add_parser("set", help="set one Action default")
    defaults_set.add_argument("--runtime", required=True)
    defaults_set.add_argument("--action", dest="action_id", required=True)
    defaults_set.add_argument("--workflow", required=True)
    defaults_set.add_argument("--model", required=True)
    defaults_set.set_defaults(func=cmd_comfy)
    comfy_defaults.set_defaults(func=cmd_comfy)
    comfy_run = comfy_commands.add_parser("run")
    comfy_run.add_argument("dir")
    comfy_run.add_argument("--host", default="127.0.0.1")
    comfy_run.add_argument("--port", type=int, default=8188)
    comfy_run.add_argument("--cuda-device", type=int)
    comfy_run.set_defaults(func=cmd_comfy)
    comfy_recipe = comfy_commands.add_parser(
        "recipe", help="register an API-format workflow recipe"
    )
    comfy_recipe.add_argument("--runtime", required=True)
    comfy_recipe.add_argument("file")
    comfy_recipe.set_defaults(func=cmd_comfy)

    dev = commands.add_parser("dev")
    dev_commands = dev.add_subparsers(dest="action", required=True)
    dev_check = dev_commands.add_parser("check")
    dev_check.add_argument("--runtime-url")
    dev_check.set_defaults(func=cmd_dev)
    dev_sync = dev_commands.add_parser("sync")
    dev_sync.set_defaults(func=cmd_dev)

    install_command = commands.add_parser(
        "install", help="install isolated ComfyUI and CookSprite nodes"
    )
    install_command.add_argument("--dir", default="~/.cooksprite/runtime")
    install_command.add_argument("--python")
    install_command.set_defaults(func=cmd_install)

    start = commands.add_parser(
        "start",
        help="start the API and frontend, plus a managed or existing ComfyUI runtime",
    )
    start.add_argument("--dir", default="~/.cooksprite/runtime")
    start.add_argument("--data-dir", default="~/.cooksprite/data")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8000)
    comfy_start = start.add_mutually_exclusive_group()
    comfy_start.add_argument(
        "--no-comfy",
        action="store_true",
        help="start only the CookSprite API and frontend; leave ComfyUI offline",
    )
    start.add_argument(
        "--no-frontend",
        action="store_true",
        help="start the API and ComfyUI without starting the Vite frontend",
    )
    start.add_argument("--frontend-dir", help="frontend web directory containing package.json")
    start.add_argument("--frontend-host", default="127.0.0.1")
    start.add_argument("--frontend-port", type=int, default=5173)
    start.add_argument("--npm", default="npm", help="npm executable used to start Vite")
    comfy_start.add_argument("--comfy-url", help="use an existing local or remote ComfyUI")
    start.add_argument("--runtime-location", choices=["local", "remote"], default="remote")
    start.add_argument("--runtime-transport", default="http")
    start.add_argument("--comfy-host", default="127.0.0.1")
    start.add_argument("--comfy-port", type=int, default=8188)
    start.add_argument("--cuda-device", type=int)
    start.add_argument("--runtime", default="rt_default")
    start.add_argument("--label", default="ComfyUI")
    start.add_argument("--timeout", type=float, default=180)
    start.set_defaults(func=cmd_start)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
