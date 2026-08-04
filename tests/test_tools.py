"""Tests for the deterministic tools."""

from __future__ import annotations

import numpy as np

import workflow  # noqa: F401  (registers tools)
from workflow.tool import REGISTRY
from workflow.types import FrameSeq, Image, NormalMap, SpriteSheet

from .helpers import NullCtx, blob_image, gradient_image


def run_tool(tool_id, inputs, params):
    comp = REGISTRY.get(tool_id)
    return comp.fn(inputs, params, NullCtx())


def test_pixelize_reduces_and_is_deterministic():
    img = gradient_image(128, 128)
    out1 = run_tool("pixelize", {"image": img}, {"target_width": 32, "target_height": 32, "colors": 16})
    out2 = run_tool("pixelize", {"image": img}, {"target_width": 32, "target_height": 32, "colors": 16})
    a = out1["image"]
    assert isinstance(a, Image)
    assert (a.width, a.height) == (32, 32)
    # Deterministic: same input, identical output.
    np.testing.assert_array_equal(a.pixels, out2["image"].pixels)
    # Palette really limited: unique opaque RGB colors <= requested.
    rgb = a.pixels[..., :3].reshape(-1, 3)
    assert len({tuple(c) for c in rgb}) <= 16


def test_pixelize_upscale_yields_solid_blocks():
    img = gradient_image(64, 64)
    out = run_tool("pixelize", {"image": img}, {"target_width": 8, "target_height": 8, "colors": 8, "upscale": 4})
    a = out["image"]
    assert (a.width, a.height) == (32, 32)
    # Each 4x4 block is uniform (nearest upscale).
    block = a.pixels[0:4, 0:4]
    assert (block == block[0, 0]).all()


def test_pixel_perfect_snaps_to_cells():
    img = gradient_image(64, 64)
    out = run_tool("pixel_perfect", {"image": img}, {"cell": 4})
    a = out["image"]
    # First 4x4 cell must be a single solid color.
    block = a.pixels[0:4, 0:4]
    assert (block == block[0, 0]).all()


def test_crop_to_content_tightens_bbox():
    img = blob_image(64, 64)
    out = run_tool("crop_to_content", {"image": img}, {"threshold": 128})
    a = out["image"]
    assert a.width < 64 and a.height < 64
    # Cropped result still has opaque content.
    assert (a.pixels[..., 3] >= 128).any()


def test_center_align_places_on_canvas_with_pivot():
    img = blob_image(64, 64)
    out = run_tool("center_align", {"image": img}, {"width": 96, "height": 96, "anchor": "bottom_center"})
    a = out["image"]
    assert (a.width, a.height) == (96, 96)
    assert a.meta["pivot"][1] == 95  # bottom row


def test_pack_sheet_concatenates_frames():
    frames = [blob_image(32, 32) for _ in range(4)]
    out = run_tool("pack_sheet", {"frames": FrameSeq(frames=frames, meta={"fps": 12})}, {})
    sheet = out["sheet"]
    assert isinstance(sheet, SpriteSheet)
    assert sheet.frames == 4
    assert sheet.diffuse.width == 128 and sheet.diffuse.height == 32
    assert sheet.meta["fps"] == 12  # sequence meta carried onto the sheet


def test_pack_sheet_rejects_unequal_frames():
    frames = [blob_image(32, 32), blob_image(16, 16)]
    try:
        run_tool("pack_sheet", {"frames": FrameSeq(frames=frames)}, {})
    except ValueError:
        return
    raise AssertionError("expected ValueError for unequal frames")


def test_normal_estimate_shape_and_flat_on_transparent():
    img = blob_image(48, 48)
    out = run_tool("normal_estimate", {"image": img}, {"strength": 2.0})
    n = out["normal"]
    assert isinstance(n, NormalMap)
    assert n.pixels.shape == (48, 48, 3)
    # Transparent corner pixel → flat up normal (128,128,255).
    assert tuple(n.pixels[0, 0]) == (128, 128, 255)
