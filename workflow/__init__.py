"""CookSprite workflow layer.

Importing this package registers all built-in tools (deterministic + inference)
into the default REGISTRY, so `from workflow import REGISTRY` is fully populated.
"""

from .tool import REGISTRY  # noqa: F401
from . import tools  # noqa: F401  (import side effect: register tools)

__all__ = ["REGISTRY", "tools"]
