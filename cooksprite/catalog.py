"""Compatibility accessors for the single Tool package registry."""

from __future__ import annotations

from .domain import ToolDescriptor
from .tool_packages import tool_packages


def builtin_tools() -> list[ToolDescriptor]:
    return tool_packages.tools()
