"""Thin HTTP CLI used directly by people and by the CookSprite agent Skill."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from cooksprite.comfy.managed import install, install_node_pack, launch

TERMINAL = {"succeeded", "failed", "cancelled"}


def client(base: str) -> httpx.Client:
    return httpx.Client(base_url=base.rstrip("/"), timeout=30)


def show(response: httpx.Response) -> int:
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


def wait_for_run(http: httpx.Client, run: dict[str, Any]) -> int:
    while run["status"] not in TERMINAL:
        time.sleep(0.35)
        response = http.get(f"/api/v1/runs/{run['id']}")
        if response.status_code >= 400:
            return show(response)
        run = response.json()
        print(f"{run['status']} {run['progress']:.0%}", file=sys.stderr)
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
    if args.action == "sequence":
        with client(args.api) as http:
            return show(http.get(f"/api/v1/artifacts/{args.id}/sequence"))
    path = Path(args.file)
    media_type = args.media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with client(args.api) as http:
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
                with_models=not args.no_models,
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
        system = report.get("system", {}).get("system", {})
        print(
            f"{report['runtime_id']} ready · ComfyUI {system.get('comfyui_version', '?')} · "
            f"{len(report.get('recipes', []))} recipe(s)"
        )
        for recipe in report.get("recipes", []):
            print(f"  {recipe['id']:<34} {recipe['label']} [{', '.join(recipe['modes'])}]")
        return 0
    if args.action == "import":
        with client(args.api) as http:
            return show(
                http.post(
                    "/api/v1/runtimes",
                    json={
                        "id": args.runtime,
                        "label": args.label,
                        "base_url": args.url,
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
    root.add_argument("--api", default="http://127.0.0.1:8000")
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

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    contributor_list = commands.add_parser("list")
    contributor_list.add_argument("kind", choices=["tools", "workflows", "tasks", "runtimes"])
    contributor_list.set_defaults(func=cmd_list)
    contributor_run = commands.add_parser("run")
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
    comfy_install.add_argument("--no-models", action="store_true")
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
    comfy_import.add_argument("--callback-url")
    comfy_import.set_defaults(func=cmd_comfy)
    comfy_doctor = comfy_commands.add_parser("doctor")
    comfy_doctor.add_argument("--runtime", required=True)
    comfy_doctor.add_argument("--json", action="store_true")
    comfy_doctor.set_defaults(func=cmd_comfy)
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
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
