"""Public CookSprite Prompt Tool contract.

The implementation is shared with the installable ComfyUI node so the API,
CLI-side contract tests, and compute-plane node cannot drift.  The node pack
copies its sibling implementation into ComfyUI during explicit installation.
"""

from .nodes.prompting import *  # noqa: F401,F403 - this module is the public facade.
from .nodes.prompting import __all__
