"""The single built-in Tool package registry."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from ..domain import ToolDescriptor, ToolPackageManifest
from .bridge import MANIFEST as BRIDGE
from .frames import MANIFEST as FRAMES
from .image import MANIFEST as IMAGE
from .normal import MANIFEST as NORMAL
from .pixel import MANIFEST as PIXEL


class ToolPackageError(ValueError):
    pass


class ToolPackageRegistry:
    def __init__(self, manifests: list[ToolPackageManifest]):
        self.manifests = list(manifests)
        self._recipe_builders: dict[str, Callable[..., dict[str, Any]]] = {}
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

    def sealed_graphs(self) -> dict[str, dict[str, Any]]:
        return {
            tool_id: graph
            for package in self.manifests
            for tool_id, graph in package.sealed_graphs.items()
        }

    def versions(self) -> dict[str, str]:
        return {package.id: package.version for package in self.manifests}

    def requirements(self) -> list[str]:
        return sorted({item for package in self.manifests for item in package.requirements})

    def register_recipe_builder(
        self,
        family: str,
        builder: Callable[..., dict[str, Any]],
    ) -> None:
        existing = self._recipe_builders.get(family)
        if existing is not None and existing is not builder:
            raise ToolPackageError(f"duplicate Recipe builder: {family}")
        self._recipe_builders[family] = builder

    def recipe_builder(self, family: str) -> Callable[..., dict[str, Any]] | None:
        return self._recipe_builders.get(family)

    def recipe_builder_for(
        self, family: str
    ) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
        def decorate(
            builder: Callable[..., dict[str, Any]],
        ) -> Callable[..., dict[str, Any]]:
            self.register_recipe_builder(family, builder)
            return builder

        return decorate

    def dump(self) -> list[dict[str, Any]]:
        return [package.model_dump(mode="json") for package in self.manifests]


tool_packages = ToolPackageRegistry([BRIDGE, IMAGE, PIXEL, FRAMES, NORMAL])
