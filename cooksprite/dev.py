"""Contributor checks and generated registry projections."""

from __future__ import annotations

import ast
import difflib
import json
from pathlib import Path
from typing import Any

from .comfy import ComfyClient
from .registry import ACTION_IDS, CookSpriteRegistry
from .tool_packages import ToolPackageError, tool_packages


class DevCheckError(ValueError):
    pass


LEGACY_NODE_CLASSES = {"CS_CompilePromptPacket"}


def check_tool_packages(runtime_url: str | None = None) -> dict[str, Any]:
    manifests = tool_packages.manifests
    declared_nodes = {node for package in manifests for node in package.node_classes}
    node_source = Path(__file__).with_name("nodes") / "cooksprite_nodes.py"
    tree = ast.parse(node_source.read_text(encoding="utf-8"))
    installed_nodes: set[str] = set()
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "NODE_CLASSES"
                for target in statement.targets
            )
            and isinstance(statement.value, ast.List)
        ):
            installed_nodes = {
                item.id for item in statement.value.elts if isinstance(item, ast.Name)
            }
    active_installed_nodes = installed_nodes - LEGACY_NODE_CLASSES
    if declared_nodes != active_installed_nodes:
        raise DevCheckError(
            f"node manifest drift; missing={sorted(declared_nodes - installed_nodes)}, "
            f"unknown={sorted(active_installed_nodes - declared_nodes)}"
        )
    for package in manifests:
        for tool in package.tools:
            class_type = package.lowerings.get(tool.id)
            if class_type and class_type not in installed_nodes:
                raise DevCheckError(f"{tool.id}: lowering node {class_type} is absent")
            sealed = package.sealed_graphs.get(tool.id) or {}
            for node in (sealed.get("workflow") or {}).values():
                sealed_class = str(node.get("class_type") or "")
                if sealed_class.startswith("CS_") and sealed_class not in installed_nodes:
                    raise DevCheckError(f"{tool.id}: sealed graph node {sealed_class} is absent")
    requirements_path = Path(__file__).with_name("nodes") / "requirements.txt"
    packaged_requirements = sorted(
        line.strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if packaged_requirements != tool_packages.requirements():
        raise DevCheckError(
            "node requirements differ from Tool Package manifests: "
            f"{packaged_requirements} != {tool_packages.requirements()}"
        )
    runtime_nodes: set[str] | None = None
    if runtime_url:
        report = ComfyClient(runtime_url).doctor()
        runtime_nodes = set((report.get("object_info") or {}).keys())
        missing = declared_nodes - runtime_nodes
        if missing:
            raise DevCheckError(f"runtime is missing CookSprite nodes: {sorted(missing)}")
        object_info = report.get("object_info") or {}
        for package in manifests:
            for tool in package.tools:
                class_type = package.lowerings.get(tool.id)
                if not class_type:
                    sealed = package.sealed_graphs.get(tool.id) or {}
                    sealed_classes = {
                        str(node.get("class_type") or "")
                        for node in (sealed.get("workflow") or {}).values()
                    }
                    missing_sealed = sealed_classes - runtime_nodes
                    if missing_sealed:
                        raise DevCheckError(
                            f"{tool.id}: runtime is missing sealed nodes {sorted(missing_sealed)}"
                        )
                    continue
                spec = object_info[class_type]
                node_inputs = spec.get("input") or {}
                runtime_inputs = set(node_inputs.get("required", {})) | set(
                    node_inputs.get("optional", {})
                )
                declared_inputs = {item.name for item in tool.inputs} | set(tool.params_schema)
                if declared_inputs != runtime_inputs:
                    raise DevCheckError(
                        f"{tool.id}: runtime input drift; declared={sorted(declared_inputs)}, "
                        f"runtime={sorted(runtime_inputs)}"
                    )
                if len(tool.outputs) != len(spec.get("output") or []):
                    raise DevCheckError(
                        f"{tool.id}: runtime output count differs from {class_type}"
                    )
    return {
        "packages": tool_packages.versions(),
        "tools": len(tool_packages.tools()),
        "nodes": sorted(declared_nodes),
        "runtime_checked": bool(runtime_url),
        "runtime_nodes": len(runtime_nodes or ()),
    }


def node_requirements_text() -> str:
    """Return the one dependency input owned by CookSprite ComfyUI nodes."""

    lines = [
        "# Generated by `cspr dev sync`; edit Tool Package manifests instead.",
        *tool_packages.requirements(),
        "",
    ]
    return "\n".join(lines)


def sync_node_requirements(root: str | Path | None = None) -> str:
    """Write the generated dependency input consumed by ComfyUI locking."""

    repository = Path(root).resolve() if root else Path(__file__).resolve().parents[1]
    path = repository / "cooksprite" / "nodes" / "requirements.txt"
    path.write_text(node_requirements_text(), encoding="utf-8")
    return str(path)


def registry_snapshot() -> dict[str, Any]:
    registry = CookSpriteRegistry()
    return {
        "schema": "cooksprite.registry/v1",
        "actions": [
            action.model_dump(mode="json", exclude={"available", "unavailable_reason", "models"})
            for action in registry.list()
        ],
        "action_ids": list(ACTION_IDS),
        "artifact_kinds": [
            "Image",
            "ImageBatch",
            "SpriteSheet",
            "FrameSeq",
            "Video",
            "Mask",
            "NormalMap",
            "Palette",
            "SpritePair",
            "CookSpritePack",
        ],
        "tool_packages": registry.package_manifests(),
    }


def snapshot_json() -> str:
    return json.dumps(registry_snapshot(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _typescript_projection(snapshot: dict[str, Any]) -> str:
    action_ids = json.dumps(snapshot["action_ids"], ensure_ascii=False)
    package_versions = json.dumps(
        {item["id"]: item["version"] for item in snapshot["tool_packages"]},
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        "/* Generated by `cspr dev sync`; do not edit by hand. */\n"
        f"export const REGISTERED_ACTION_IDS = {action_ids} as const;\n"
        "export type RegisteredActionId = typeof REGISTERED_ACTION_IDS[number];\n"
        f"export const TOOL_PACKAGE_VERSIONS = {package_versions} as const;\n"
    )


def _skill_projection(snapshot: dict[str, Any]) -> str:
    lines = [
        "<!-- Generated by `cspr dev sync`; do not edit by hand. -->",
        "# Registered Actions",
        "",
        "| Action | Inputs | Values | Outputs |",
        "| --- | --- | --- | --- |",
    ]
    for action in snapshot["actions"]:
        inputs = (
            ", ".join(
                f"`{name}:{rule['type']}`" for name, rule in action.get("accepts", {}).items()
            )
            or "-"
        )
        values = ", ".join(f"`{item['id']}`" for item in action.get("controls", [])) or "-"
        outputs = ", ".join(f"`{item}`" for item in action.get("produces", [])) or "-"
        lines.append(f"| `{action['id']}` | {inputs} | {values} | {outputs} |")
    lines.extend(
        [
            "",
            "Project export is intentionally not an Action. Use `cspr project export`.",
            "",
        ]
    )
    return "\n".join(lines)


def generated_outputs(root: str | Path | None = None) -> dict[Path, str]:
    repository = Path(root).resolve() if root else Path(__file__).resolve().parents[1]
    snapshot = registry_snapshot()
    return {
        repository / "cooksprite" / "generated" / "registry.json": snapshot_json(),
        repository / "web" / "src" / "api" / "registry.generated.ts": _typescript_projection(
            snapshot
        ),
        repository / "skills" / "cooksprite" / "ACTIONS.generated.md": _skill_projection(snapshot),
        repository / "cooksprite" / "nodes" / "requirements.txt": node_requirements_text(),
    }


def sync_generated(root: str | Path | None = None) -> list[str]:
    written = []
    for path, content in generated_outputs(root).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written


def check_generated(root: str | Path | None = None) -> list[str]:
    checked = []
    for path, expected in generated_outputs(root).items():
        actual = path.read_text(encoding="utf-8") if path.is_file() else ""
        if actual != expected:
            diff = "".join(
                difflib.unified_diff(
                    actual.splitlines(keepends=True),
                    expected.splitlines(keepends=True),
                    fromfile=str(path),
                    tofile=f"{path} (generated)",
                    n=2,
                )
            )
            raise DevCheckError(f"generated registry projection is stale:\n{diff[:4000]}")
        checked.append(str(path))
    return checked


__all__ = [
    "DevCheckError",
    "ToolPackageError",
    "check_generated",
    "check_tool_packages",
    "generated_outputs",
    "node_requirements_text",
    "registry_snapshot",
    "snapshot_json",
    "sync_generated",
    "sync_node_requirements",
]
