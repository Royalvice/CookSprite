"""Deterministic composition Tools that assemble typed sprite outputs."""

from __future__ import annotations

from typing import Any

from ..tool import Port, RunContext, tool
from ..types import Artifact, Image, NormalMap, SpritePair


@tool(
    id="make_sprite_pair",
    inputs=[Port("diffuse", "image"), Port("normal", "normal_map", required=False)],
    outputs=[Port("pair", "sprite_pair")],
    params=[],
    description="Combine a diffuse image and an optional normal map into a sprite pair.",
)
def make_sprite_pair(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    diffuse: Image = inputs["diffuse"]  # type: ignore[assignment]
    normal: NormalMap | None = inputs.get("normal")  # type: ignore[assignment]
    ctx.progress(1.0, "assembled sprite pair")
    return {"pair": SpritePair(diffuse=diffuse, normal=normal)}
