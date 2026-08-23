"""Public exception hierarchy for the 1.0 API."""


class PixelPerfectError(RuntimeError):
    """Base exception for all public toolkit failures."""


class GridDetectionError(PixelPerfectError):
    """Raised when a pseudo-pixel grid cannot be recovered confidently."""
