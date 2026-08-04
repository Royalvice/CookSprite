"""`cspr` — the agent-facing CLI for CookSprite.

Commands:
    cspr list [--json]                 introspect capabilities / workflows / components
    cspr run  <capability> [opts]      run a workflow, write the sprite pair to --out
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
from workflow.runner import run_workflow
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
    data: dict[str, Any] = {"capabilities": [], "components": []}
    for cap in library.capabilities():
        data["capabilities"].append(
            {
                "id": cap.id,
                "workflows": [
                    {"id": wf.id, "default": wf.default, "params": wf.params, "description": wf.description}
                    for wf in cap.workflows
                ],
            }
        )
    for comp in REGISTRY.all():
        data["components"].append(
            {
                "id": comp.id,
                "category": comp.category,
                "inputs": [{"name": p.name, "kind": p.kind} for p in comp.inputs],
                "outputs": [{"name": p.name, "kind": p.kind} for p in comp.outputs],
                "params": [{"name": p.name, "type": p.type, "default": p.default} for p in comp.params],
                "description": comp.description,
            }
        )
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        for cap in data["capabilities"]:
            print(f"capability: {cap['id']}")
            for wf in cap["workflows"]:
                mark = " (default)" if wf["default"] else ""
                print(f"  workflow: {wf['id']}{mark} — {wf['description']}")
        print(f"\n{len(data['components'])} components registered "
              f"({sum(1 for c in data['components'] if c['category']=='tool')} tools, "
              f"{sum(1 for c in data['components'] if c['category']=='op')} ops)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    library = Library.load_builtin()
    try:
        spec = library.resolve(args.capability, args.workflow)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    params = _parse_params(args.param)
    if args.prompt is not None:
        params["prompt"] = args.prompt

    workspace = Workspace.init(args.out)
    from backend.ops import build_default_router

    client = DirectClient(build_default_router())

    def on_progress(fraction: float, message: str) -> None:
        if not args.quiet:
            print(f"  [{int(fraction*100):3d}%] {message}", file=sys.stderr)

    artifact = run_workflow(
        spec, REGISTRY, client,
        params=params, config_defaults=workspace.param_defaults(), on_progress=on_progress,
    )
    entry = workspace.save_artifact(artifact)
    workspace.record_run({"capability": args.capability, "workflow": spec.id, "params": params, "artifacts": [entry]})

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
        print(f"warning: unmapped components: {sorted(set(result.unmapped))}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cspr", description="Cook up sprites with AI.")
    sub = p.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("list", help="introspect capabilities/workflows/components")
    lp.add_argument("--json", action="store_true")
    lp.set_defaults(func=cmd_list)

    rp = sub.add_parser("run", help="run a workflow")
    rp.add_argument("capability")
    rp.add_argument("--workflow", default=None, help="named workflow (default: capability default)")
    rp.add_argument("--prompt", default=None)
    rp.add_argument("--param", action="append", default=[], help="k=v (repeatable)")
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
