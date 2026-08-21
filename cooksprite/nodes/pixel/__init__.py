"""Deterministic pixel-art algorithms migrated from Perfect Pixel Toolkit.

Keep package import metadata-only: CookSprite's API/test environment must be
able to inspect a PixelGeometryPlan without importing the OpenCV compute
stack.  The actual adapters are resolved only inside the ComfyUI node runtime.
"""

__all__ = ["pixelize_batch", "pixelize_pair_batch", "snap_batch"]


def __getattr__(name: str):
    if name in __all__:
        from .adapter import pixelize_batch, pixelize_pair_batch, snap_batch

        return {
            "pixelize_batch": pixelize_batch,
            "pixelize_pair_batch": pixelize_pair_batch,
            "snap_batch": snap_batch,
        }[name]
    raise AttributeError(name)
