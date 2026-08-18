"""Public CookSprite Prompt Tool contract.

The implementation is shared with the installable ComfyUI node so the API,
CLI-side contract tests, and compute-plane node cannot drift.  The node pack
copies its sibling implementation into ComfyUI during explicit installation.
"""

from .nodes import prompting as _prompting
from .nodes.prompting import *

__all__ = _prompting.__all__
