"""Small runtime-only contracts used by the migrated pixel algorithms.

The API environment deliberately does not import this module.  These classes
replace the source package's Pydantic request models at the ComfyUI boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetGrid:
    width: int
    height: int
    padding_x: int | None = None
    padding_y: int | None = None
    palette_budget: int | None = None

    def __post_init__(self) -> None:
        if not 16 <= int(self.width) <= 512 or not 16 <= int(self.height) <= 512:
            raise ValueError("target grid dimensions must be between 16 and 512")
        if self.resolved_padding_x * 2 >= self.width or self.resolved_padding_y * 2 >= self.height:
            raise ValueError("padding leaves no drawable target area")
        if self.resolved_palette_budget < 2 or self.resolved_palette_budget > 256:
            raise ValueError("palette budget must be between 2 and 256")

    @property
    def resolved_padding_x(self) -> int:
        return int(self.padding_x) if self.padding_x is not None else max(1, (self.width + 15) // 16)

    @property
    def resolved_padding_y(self) -> int:
        return int(self.padding_y) if self.padding_y is not None else max(1, (self.height + 15) // 16)

    @property
    def resolved_palette_budget(self) -> int:
        if self.palette_budget is not None:
            return int(self.palette_budget)
        shortest = min(self.width, self.height)
        if shortest <= 64:
            return 32
        if shortest <= 128:
            return 48
        if shortest <= 256:
            return 96
        return 128


@dataclass(frozen=True)
class GridSpec:
    mode: str = "auto"
    pixel_size_x: float | None = None
    pixel_size_y: float | None = None
    phase_x: float | None = None
    phase_y: float | None = None
    constrained_warp: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"auto", "manual"}:
            raise ValueError("grid mode must be auto or manual")
        if self.mode == "manual" and (self.pixel_size_x is None or self.pixel_size_y is None):
            raise ValueError("manual grid requires pixel_size_x and pixel_size_y")
        if self.pixel_size_x is not None and self.pixel_size_x < 1:
            raise ValueError("pixel_size_x must be at least 1")
        if self.pixel_size_y is not None and self.pixel_size_y < 1:
            raise ValueError("pixel_size_y must be at least 1")

    @classmethod
    def auto(cls) -> GridSpec:
        return cls()

    @classmethod
    def manual(cls, pixel_size: float, phase_x: float = 0.0, phase_y: float = 0.0) -> GridSpec:
        return cls("manual", pixel_size, pixel_size, phase_x, phase_y)
