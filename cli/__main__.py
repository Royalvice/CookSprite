"""`cspr` — the agent-facing CLI for CookSprite.

Commands:
    cspr list [--json]                 introspect tasks / workflows / tools
    cspr run  <task> [opts]            run a task, write the sprite pair to --out
    cspr workspace init <dir>          create a workspace (folder + config + manifest)
    cspr export <workflow_id> [--out]  export a workflow to ComfyUI API JSON

`list` is the self-describing introspection surface: agents call it to discover
what exists (no guessing), then `run`. Each built-in workflow YAML is a copyable
reference example.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from workflow import REGISTRY
from workflow.clients import DirectClient
from workflow.export.comfyui import export_to_comfyui
from workflow.library import Library
from workflow.runner import run_task
from workflow.types import SpritePair, SpriteSheet
from workflow.workspace import Workspace


def _parse_params(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--param expects k=v, got {pair!r}")
        k, v = pair.split("=", 1)
        out[k] = _coerce(v)
    return out


def _coerce(v: str) -> Any:
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def cmd_list(args: argparse.Namespace) -> int:
    library = Library.load_builtin()
    data: dict[str, Any] = {"tasks": [], "workflows": [], "tools": []}
    for task in library.tasks():
        data["tasks"].append(
            {
                "id": task.id,
                "description": task.description,
                "params": task.params,
                "nodes": [
                    {"id": n.id, "candidates": n.candidates, "inputs": n.inputs}
                    for n in task.nodes
                ],
            }
        )
    for wf in library.workflows():
        data["workflows"].append(
            {
                "id": wf.id,
                "description": wf.description,
                "inputs": wf.inputs,
                "params": wf.params,
                "nodes": [n.id for n in wf.nodes],
            }
        )
    for t in REGISTRY.all():
        data["tools"].append(
            {
                "id": t.id,
                "kind": t.kind,
                "inputs": [{"name": p.name, "kind": p.kind} for p in t.inputs],
                "outputs": [{"name": p.name, "kind": p.kind} for p in t.outputs],
                "params": [{"name": p.name, "type": p.type, "default": p.default} for p in t.params],
                "description": t.description,
            }
        )
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        for task in data["tasks"]:
            print(f"task: {task['id']} — {task['description']}")
            for n in task["nodes"]:
                cands = n["candidates"]
                extra = f" (+{len(cands)-1} alt)" if len(cands) > 1 else ""
                print(f"  node {n['id']}: {cands[0]}{extra}")
        print(f"\n{len(data['workflows'])} workflows, {len(data['tools'])} tools "
              f"({sum(1 for c in data['tools'] if c['kind']=='deterministic')} deterministic, "
              f"{sum(1 for c in data['tools'] if c['kind']=='inference')} inference)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    library = Library.load_builtin()
    try:
        task = library.get_task(args.task)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    params = _parse_params(args.param)
    if args.prompt is not None:
        params["prompt"] = args.prompt
    # --choose node=workflow picks a non-default candidate for a task node.
    choices = _parse_params(args.choose)

    workspace = Workspace.init(args.out)
    from backend.ops import build_default_router

    client = DirectClient(build_default_router())

    def on_progress(fraction: float, message: str) -> None:
        if not args.quiet:
            print(f"  [{int(fraction*100):3d}%] {message}", file=sys.stderr)

    try:
        artifact = run_task(
            task, library.workflow_map(), REGISTRY, client,
            params=params, choices={k: str(v) for k, v in choices.items()},
            config_defaults=workspace.param_defaults(), on_progress=on_progress,
        )
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    entry = workspace.save_artifact(artifact)
    workspace.record_run({"task": args.task, "params": params, "choices": choices, "artifacts": [entry]})

    kind = "sprite pair" if isinstance(artifact, SpritePair) else (
        "sprite sheet" if isinstance(artifact, SpriteSheet) else artifact.kind)
    print(f"wrote {kind} to {workspace.root}/artifacts")
    print(json.dumps(entry, indent=2))
    return 0


def cmd_workspace(args: argparse.Namespace) -> int:
    if args.action == "init":
        ws = Workspace.init(args.dir)
        print(f"workspace ready at {ws.root}")
        return 0
    print(f"unknown workspace action {args.action}", file=sys.stderr)
    return 2


def cmd_export(args: argparse.Namespace) -> int:
    library = Library.load_builtin()
    try:
        spec = library.get_workflow(args.workflow_id)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    result = export_to_comfyui(spec)
    text = json.dumps(result.graph, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"exported {args.workflow_id} -> {args.out}")
    else:
        print(text)
    if result.unmapped:
        print(f"warning: unmapped tools: {sorted(set(result.unmapped))}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cspr", description="Cook up sprites with AI.")
    sub = p.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("list", help="introspect tasks/workflows/tools")
    lp.add_argument("--json", action="store_true")
    lp.set_defaults(func=cmd_list)

    rp = sub.add_parser("run", help="run a task")
    rp.add_argument("task")
    rp.add_argument("--prompt", default=None)
    rp.add_argument("--param", action="append", default=[], help="k=v (repeatable)")
    rp.add_argument("--choose", action="append", default=[],
                    help="node=workflow: pick a non-default candidate (repeatable)")
    rp.add_argument("--out", default="./cooksprite_workspace", help="workspace dir")
    rp.add_argument("--quiet", action="store_true")
    rp.set_defaults(func=cmd_run)

    wp = sub.add_parser("workspace", help="manage a workspace")
    wp.add_argument("action", choices=["init"])
    wp.add_argument("dir")
    wp.set_defaults(func=cmd_workspace)

    ep = sub.add_parser("export", help="export a workflow to ComfyUI API JSON")
    ep.add_argument("workflow_id")
    ep.add_argument("--out", default=None)
    ep.set_defaults(func=cmd_export)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
