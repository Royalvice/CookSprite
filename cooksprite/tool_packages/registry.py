"""The single built-in Tool package registry."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..domain import ToolDescriptor, ToolPackageManifest
from .alpha import MANIFEST as ALPHA
from .bridge import MANIFEST as BRIDGE
from .frames import MANIFEST as FRAMES
from .image import MANIFEST as IMAGE
from .normal import MANIFEST as NORMAL
from .pixel import MANIFEST as PIXEL
from .prompt import MANIFEST as PROMPT


class ToolPackageError(ValueError):
    pass


class ToolPackageRegistry:
    def __init__(self, manifests: list[ToolPackageManifest]):
        self.manifests = list(manifests)
        package_ids = [item.id for item in manifests]
        duplicate_packages = [key for key, count in Counter(package_ids).items() if count > 1]
        if duplicate_packages:
            raise ToolPackageError(f"duplicate package ids: {sorted(duplicate_packages)}")
        tool_ids = [tool.id for item in manifests for tool in item.tools]
        duplicate_tools = [key for key, count in Counter(tool_ids).items() if count > 1]
        if duplicate_tools:
            raise ToolPackageError(f"duplicate tool ids: {sorted(duplicate_tools)}")
        node_ids = [node for item in manifests for node in item.node_classes]
        duplicate_nodes = [key for key, count in Counter(node_ids).items() if count > 1]
        if duplicate_nodes:
            raise ToolPackageError(f"duplicate node classes: {sorted(duplicate_nodes)}")

    def tools(self) -> list[ToolDescriptor]:
        return [tool for package in self.manifests for tool in package.tools]

    def lowerings(self) -> dict[str, str]:
        return {
            tool_id: class_type
            for package in self.manifests
            for tool_id, class_type in package.lowerings.items()
        }

    def versions(self) -> dict[str, str]:
        return {package.id: package.version for package in self.manifests}

    def requirements(self) -> list[str]:
        return sorted({item for package in self.manifests for item in package.requirements})

    def dump(self) -> list[dict[str, Any]]:
        return [package.model_dump(mode="json") for package in self.manifests]


tool_packages = ToolPackageRegistry([BRIDGE, PROMPT, IMAGE, PIXEL, ALPHA, FRAMES, NORMAL])
