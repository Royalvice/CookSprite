"""Importing this package registers every built-in tool (both deterministic and
inference kinds) into the default REGISTRY."""

from . import compose, generate, normal_estimate, pixelize, transform  # noqa: F401

__all__ = ["compose", "generate", "normal_estimate", "pixelize", "transform"]
