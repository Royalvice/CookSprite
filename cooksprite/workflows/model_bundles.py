"""One model-bundle registry shared by runtime discovery and downloads."""

from __future__ import annotations

from typing import Any

from .flux2_klein import FLUX2_BUNDLES
from .lotus_normal import LOTUS_NORMAL_BUNDLE, LOTUS_NORMAL_BUNDLE_ID
from .normalcrafter import NORMALCRAFTER_BUNDLE, NORMALCRAFTER_BUNDLE_ID

MODEL_BUNDLES: dict[str, dict[str, Any]] = {
    **FLUX2_BUNDLES,
    LOTUS_NORMAL_BUNDLE_ID: LOTUS_NORMAL_BUNDLE,
    NORMALCRAFTER_BUNDLE_ID: NORMALCRAFTER_BUNDLE,
}

__all__ = ["MODEL_BUNDLES"]
