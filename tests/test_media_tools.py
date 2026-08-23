from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from cooksprite.nodes.cooksprite_nodes import (
    CS_Pixelize,
    CS_PixelizePair,
    CS_PixelizeSequence,
    CS_PixelSnap,
    CS_ProjectNormalToPixelPlan,
    _png,
)


def test_new_node_contracts_expose_image_and_mask():
    assert CS_Pixelize.RETURN_TYPES == ("IMAGE", "MASK")
    assert CS_PixelizePair.RETURN_TYPES == ("IMAGE", "MASK", "IMAGE")
    assert CS_PixelizePair.RETURN_NAMES == ("image", "mask", "normal")
    assert CS_PixelizePair.INPUT_TYPES()["optional"]["sequence_mode"][1]["default"] == "auto"
    assert CS_PixelizeSequence.RETURN_TYPES == ("CS_PIXEL_SEQUENCE", "CS_PIXEL_PLAN")
    assert CS_PixelizeSequence.INPUT_TYPES()["required"]["temporal_mode"][1]["default"] == "auto"
    assert CS_ProjectNormalToPixelPlan.RETURN_TYPES == ("IMAGE", "MASK")
    assert CS_ProjectNormalToPixelPlan.INPUT_TYPES()["required"]["pixel_plan"][0] == "CS_PIXEL_PLAN"
    assert CS_PixelSnap.RETURN_TYPES == ("IMAGE", "MASK")


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
