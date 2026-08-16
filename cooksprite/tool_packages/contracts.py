"""Small helpers used by the built-in Tool package manifests."""

from __future__ import annotations

from typing import Any

from ..domain import PortDescriptor, ToolDescriptor


def tool(
    package_id: str,
    name: str,
    title: str,
    inputs: list[tuple[str, str]],
    outputs: list[tuple[str, str, bool]],
    params: dict[str, Any] | None = None,
) -> ToolDescriptor:
    return ToolDescriptor(
        id=f"cooksprite.{name}",
        source="cooksprite",
        package_id=package_id,
        title=title,
        inputs=[PortDescriptor(name=port, type=kind) for port, kind in inputs],
        outputs=[
            PortDescriptor(name=port, type=kind, persistable=persistable)
            for port, kind, persistable in outputs
        ],
        params_schema=params or {},
    )
