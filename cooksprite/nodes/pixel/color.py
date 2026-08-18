"""Color transforms used by deterministic quantization and metrics."""

from __future__ import annotations

import numpy as np


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    x = np.asarray(rgb, dtype=np.float32)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    x = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055).astype(np.float32)


def linear_rgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    x = np.asarray(rgb, dtype=np.float32)
    l = 0.4122214708 * x[..., 0] + 0.5363325363 * x[..., 1] + 0.0514459929 * x[..., 2]
    m = 0.2119034982 * x[..., 0] + 0.6806995451 * x[..., 1] + 0.1073969566 * x[..., 2]
    s = 0.0883024619 * x[..., 0] + 0.2817188376 * x[..., 1] + 0.6299787005 * x[..., 2]
    lms = np.cbrt(np.stack((l, m, s), axis=-1))
    out_l = 0.2104542553 * lms[..., 0] + 0.7936177850 * lms[..., 1] - 0.0040720468 * lms[..., 2]
    out_a = 1.9779984951 * lms[..., 0] - 2.4285922050 * lms[..., 1] + 0.4505937099 * lms[..., 2]
    out_b = 0.0259040371 * lms[..., 0] + 0.7827717662 * lms[..., 1] - 0.8086757660 * lms[..., 2]
    return np.stack((out_l, out_a, out_b), axis=-1).astype(np.float32)


def oklab_to_linear_rgb(lab: np.ndarray) -> np.ndarray:
    x = np.asarray(lab, dtype=np.float32)
    l_ = x[..., 0] + 0.3963377774 * x[..., 1] + 0.2158037573 * x[..., 2]
    m_ = x[..., 0] - 0.1055613458 * x[..., 1] - 0.0638541728 * x[..., 2]
    s_ = x[..., 0] - 0.0894841775 * x[..., 1] - 1.2914855480 * x[..., 2]
    l, m, s = l_**3, m_**3, s_**3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return np.clip(np.stack((r, g, b), axis=-1), 0.0, 1.0).astype(np.float32)


def srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    return linear_rgb_to_oklab(srgb_to_linear(rgb))


def oklab_to_srgb(lab: np.ndarray) -> np.ndarray:
    return linear_to_srgb(oklab_to_linear_rgb(lab))


def delta_e_oklab(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.linalg.norm(np.asarray(a) - np.asarray(b), axis=-1)


def hex_to_srgb(value: str) -> np.ndarray:
    text = value.lstrip("#")
    if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ValueError(f"invalid RGB hex color: {value}")
    return np.array([int(text[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float32) / 255.0


def srgb_to_hex(rgb: np.ndarray) -> str:
    values = np.clip(np.rint(np.asarray(rgb) * 255), 0, 255).astype(np.uint8)
    return "#" + "".join(f"{int(channel):02X}" for channel in values)
