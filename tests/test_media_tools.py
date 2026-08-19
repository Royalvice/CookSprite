from __future__ import annotations

import io
import sys
import types

import numpy as np
import pytest
from PIL import Image

from cooksprite.nodes.alpha import _session, remove_background_batch
from cooksprite.nodes.cooksprite_nodes import CS_Pixelize, CS_PixelSnap, CS_RemoveBackground, _png


def test_new_node_contracts_expose_image_and_mask():
    assert CS_Pixelize.RETURN_TYPES == ("IMAGE", "MASK")
    assert CS_PixelSnap.RETURN_TYPES == ("IMAGE", "MASK")
    assert CS_RemoveBackground.RETURN_TYPES == ("IMAGE", "MASK")
    assert CS_RemoveBackground.INPUT_TYPES()["required"]["model"][1]["default"] == "u2net"


def test_rembg_adapter_caches_session_and_preserves_batch(monkeypatch):
    calls: list[str] = []

    def new_session(model):
        calls.append(model)
        return {"model": model}

    def remove(_payload, *, session, **kwargs):
        assert session == {"model": "u2net"}
        assert kwargs["alpha_matting"] is True
        image = Image.new("RGBA", (2, 2), (10, 20, 30, 0))
        image.putpixel((0, 0), (10, 20, 30, 255))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    fake = types.ModuleType("rembg")
    fake.new_session = new_session
    fake.remove = remove
    monkeypatch.setitem(sys.modules, "rembg", fake)
    _session.cache_clear()
    source = np.ones((2, 3, 5, 3), dtype=np.float32)

    foreground, mask = remove_background_batch(source, alpha_matting=True, batch_size=2)
    again, again_mask = remove_background_batch(source, alpha_matting=True, batch_size=1)

    assert calls == ["u2net"]
    assert foreground.shape == (2, 3, 5, 3)
    assert mask.shape == (2, 3, 5)
    assert float(mask[0, 0, 0]) > 0.99
    assert float(mask[0, -1, -1]) < 0.01
    np.testing.assert_array_equal(foreground, again)
    np.testing.assert_array_equal(mask, again_mask)


def test_pixel_adapter_is_deterministic_and_keeps_batch_mask():
    pytest.importorskip("cv2", reason="pixel algorithm dependencies are ComfyUI-only")
    from cooksprite.nodes.pixel.adapter import pixelize_batch, snap_batch

    source = np.zeros((2, 32, 32, 3), dtype=np.float32)
    source[:, 8:24, 8:24] = (0.9, 0.2, 0.1)
    mask = np.zeros((2, 32, 32), dtype=np.float32)
    mask[:, 8:24, 8:24] = 1.0

    first = pixelize_batch(source, mask, 16, 16, palette_budget=8)
    second = pixelize_batch(source, mask, 16, 16, palette_budget=8)
    np.testing.assert_array_equal(first[0], second[0])
    np.testing.assert_array_equal(first[1], second[1])
    assert first[0].shape == (2, 16, 16, 3)
    assert first[1].shape == (2, 16, 16)

    snapped_image, snapped_mask = snap_batch(
        source,
        mask,
        grid_mode="manual",
        pixel_size_x=4,
        pixel_size_y=4,
        palette_budget=8,
    )
    assert snapped_image.shape[0] == 2
    assert snapped_mask.shape == snapped_image.shape[:3]


def test_pixel_palette_detail_wins_over_forced_outline():
    pytest.importorskip("cv2", reason="pixel algorithm dependencies are ComfyUI-only")
    from cooksprite.nodes.pixel.color import srgb_to_oklab
    from cooksprite.nodes.pixel.stages.palette import PaletteBuildResult, map_palette

    colors = np.asarray(
        [
            (0.03, 0.03, 0.03),
            (0.92, 0.92, 0.92),
        ],
        dtype=np.float32,
    )
    palette = PaletteBuildResult(
        colors,
        srgb_to_oklab(colors),
        outline_index=0,
        fixed_count=2,
        inertia=0.0,
        receipt={},
    )
    cell_lab = palette.lab[1][None, None, :]
    alpha = np.ones((1, 1), dtype=np.float32)
    strokes = np.ones((1, 1), dtype=bool)
    details = np.ones((1, 1), dtype=bool)

    labels, _ = map_palette(cell_lab, alpha, palette, strokes, details)

    assert int(labels[0, 0]) == 1


def test_store_png_preserves_official_rgba_output():
    class FakeTensor:
        def __init__(self, value):
            self.value = value

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.value

    rgba = np.zeros((2, 2, 4), dtype=np.float32)
    rgba[..., :3] = (0.2, 0.8, 0.3)
    rgba[0, 0, 3] = 1.0
    output = _png(FakeTensor(rgba), "Image")

    with Image.open(io.BytesIO(output)) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 255
        assert image.getpixel((1, 1))[3] == 0

    rgb = np.zeros((2, 2, 3), dtype=np.float32)
    rgb[...] = (0.2, 0.8, 0.3)
    mask = np.zeros((2, 2), dtype=np.float32)
    mask[0, 0] = 1.0
    masked_output = _png(FakeTensor(rgb), "Image", FakeTensor(mask))

    with Image.open(io.BytesIO(masked_output)) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0)) == (51, 204, 76, 255)
        assert image.getpixel((1, 1)) == (0, 0, 0, 0)
