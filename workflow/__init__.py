"""CookSprite workflow layer.

Importing this package registers all built-in Tools and Ops into the default
component REGISTRY, so `from workflow import REGISTRY` is fully populated.
"""

from .component import REGISTRY  # noqa: F401
from . import ops, tools  # noqa: F401  (import side effect: register components)

__all__ = ["REGISTRY", "ops", "tools"]
