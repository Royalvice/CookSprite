"""Deterministic geometry Tools: crop-to-content, center-align, pack-sheet.

Signal/geometry only, no model. These implement the practical sprite-cleanup
operations from a DSP/layout standpoint.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..tool import ParamSpec, Port, RunContext, tool
from ..types import Artifact, FrameSeq, Image, SpriteSheet
from . import imaging


@tool(
    id="crop_to_content",
    inputs=[Port("image", "image")],
    outputs=[Port("image", "image")],
    params=[
        ParamSpec("threshold", "int", 128, "Alpha threshold for content"),
        ParamSpec("padding", "int", 0, "Transparent padding to keep around content"),
    ],
    description="Crop an image to the alpha bounding box of its content.",
)
def crop_to_content(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    src: Image = inputs["image"]  # type: ignore[assignment]
    threshold = int(params.get("threshold", 128))
    padding = max(0, int(params.get("padding", 0)))
    ctx.progress(0.5, "computing content bounds")
    bbox = imaging.alpha_bbox(src.pixels, threshold)
    if bbox is None:
        ctx.progress(1.0, "empty image, unchanged")
        return {"image": src}
    x0, y0, x1, y1 = bbox
    cropped = imaging.ensure_rgba(src.pixels)[y0:y1, x0:x1]
    if padding:
        ph, pw = cropped.shape[:2]
        cropped = imaging.paste_onto(pw + 2 * padding, ph + 2 * padding, cropped, padding, padding)
    ctx.progress(1.0, "cropped")
    return {"image": Image(pixels=cropped, meta={**src.meta, "cropped": True})}


@tool(
    id="center_align",
    inputs=[Port("image", "image")],
    outputs=[Port("image", "image")],
    params=[
        ParamSpec("width", "int", 96, "Output canvas width"),
        ParamSpec("height", "int", 96, "Output canvas height"),
        ParamSpec("anchor", "string", "bottom_center", "center | bottom_center"),
    ],
    description="Place content on a fixed canvas aligned to an anchor (e.g. feet).",
)
def center_align(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    src: Image = inputs["image"]  # type: ignore[assignment]
    cw = max(1, int(params.get("width", 96)))
    ch = max(1, int(params.get("height", 96)))
    anchor = str(params.get("anchor", "bottom_center"))

    ctx.progress(0.4, "measuring content")
    bbox = imaging.alpha_bbox(src.pixels)
    rgba = imaging.ensure_rgba(src.pixels)
    content = rgba if bbox is None else rgba[bbox[1] : bbox[3], bbox[0] : bbox[2]]
    conth, contw = content.shape[:2]

    x = (cw - contw) // 2
    if anchor == "bottom_center":
        y = ch - conth
    else:  # center
        y = (ch - conth) // 2

    ctx.progress(0.8, "compositing onto canvas")
    out = imaging.paste_onto(cw, ch, content, x, y)
    meta = {**src.meta, "anchor": anchor, "canvas": [cw, ch], "pivot": _pivot(anchor, cw, ch, x, contw)}
    ctx.progress(1.0, "aligned")
    return {"image": Image(pixels=out, meta=meta)}


def _pivot(anchor: str, cw: int, ch: int, x: int, contw: int) -> list[int]:
    px = x + contw // 2
    py = ch - 1 if anchor == "bottom_center" else ch // 2
    return [int(px), int(py)]


@tool(
    id="pack_sheet",
    inputs=[Port("frames", "frame_seq")],
    outputs=[Port("sheet", "sprite_sheet")],
    params=[],
    description="Pack an ordered frame sequence into one horizontal sprite sheet.",
)
def pack_sheet(
    inputs: dict[str, Artifact], params: dict[str, Any], ctx: RunContext
) -> dict[str, Artifact]:
    seq: FrameSeq = inputs["frames"]  # type: ignore[assignment]
    images = seq.frames
    if not images:
        raise ValueError("pack_sheet received an empty frame sequence")
    fw, fh = images[0].width, images[0].height
    for im in images:
        if im.width != fw or im.height != fh:
            raise ValueError("pack_sheet requires equal-size frames")
    ctx.progress(0.5, f"packing {len(images)} frames")
    strip = np.concatenate([imaging.ensure_rgba(im.pixels) for im in images], axis=1)
    sheet = SpriteSheet(
        diffuse=Image(pixels=strip),
        frames=len(images),
        frame_w=fw,
        frame_h=fh,
        meta={**seq.meta, "packed": True},
    )
    ctx.progress(1.0, "packed")
    return {"sheet": sheet}
