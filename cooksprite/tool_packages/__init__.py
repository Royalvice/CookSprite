"""Built-in CookSprite Tool packages.

The package list is intentionally static in v0.1. Third-party plugin discovery
would add installation and trust complexity without helping the current
product surface.
"""

from .registry import ToolPackageError, ToolPackageRegistry, tool_packages

__all__ = ["ToolPackageError", "ToolPackageRegistry", "tool_packages"]
