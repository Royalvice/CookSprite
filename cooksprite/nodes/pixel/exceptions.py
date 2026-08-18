"""Public exception hierarchy for the 1.0 API."""


class PixelPerfectError(RuntimeError):
    """Base exception for all public toolkit failures."""


class InvalidAlphaError(PixelPerfectError):
    """Raised when an input is not a usable transparent RGBA Sprite."""


class CanvasMismatchError(PixelPerfectError):
    """Raised when frames in one sequence do not share a canvas."""


class GridDetectionError(PixelPerfectError):
    """Raised when a pseudo-pixel grid cannot be recovered confidently."""


class HardGateError(PixelPerfectError):
    """Raised when a produced artifact violates a non-negotiable contract."""
