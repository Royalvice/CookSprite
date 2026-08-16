"""Stable CookSprite tools. Their implementations are Comfy custom nodes."""

from __future__ import annotations

from .domain import PortDescriptor, ToolDescriptor


def builtin_tools() -> list[ToolDescriptor]:
    def tool(
        name: str,
        title: str,
        inputs: list[tuple[str, str]],
        outputs: list[tuple[str, str, bool]],
        params: dict | None = None,
    ) -> ToolDescriptor:
        return ToolDescriptor(
            id=f"cooksprite.{name}",
            source="cooksprite",
            title=title,
            inputs=[PortDescriptor(name=port, type=kind) for port, kind in inputs],
            outputs=[
                PortDescriptor(name=port, type=kind, persistable=persistable)
                for port, kind, persistable in outputs
            ],
            params_schema=params or {},
        )

    return [
        tool(
            "load_artifact",
            "Load an artifact",
            [("artifact_url", "Text")],
            [("image", "Image", False)],
        ),
        tool(
            "store_artifact",
            "Store an artifact",
            [("value", "Image"), ("upload_url", "Text")],
            [("receipt", "Text", False)],
        ),
        tool(
            "pixelize",
            "Pixelize",
            [("image", "Image")],
            [("image", "Image", True)],
            {"target_width": {"type": "integer"}, "target_height": {"type": "integer"}},
        ),
        tool(
            "isolate_on_green",
            "Isolate on chroma green",
            [("image", "Image")],
            [("image", "Image", True)],
            {"tolerance": {"type": "number"}},
        ),
        tool("center_align", "Center align", [("image", "Image")], [("image", "Image", True)]),
        tool(
            "normal_estimate",
            "Estimate normal map",
            [("image", "Image")],
            [("normal", "NormalMap", True)],
        ),
        tool(
            "make_sprite_pair",
            "Make sprite pair",
            [("diffuse", "Image"), ("normal", "NormalMap")],
            [("diffuse", "Image", True), ("normal", "NormalMap", True)],
        ),
    ]
