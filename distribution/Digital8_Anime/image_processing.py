"""Shared V4 colour, ink and alpha operations."""

import numpy as np
from PIL import Image

ALPHA_MODES = ("preserve", "binary")
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)


def source_ink(image: np.ndarray, threshold: int, edge_strength: float) -> np.ndarray:
    """Use the same near-black and contrast tests as the Photoshop filter."""
    red, green, blue = image.astype(np.float32).transpose(2, 0, 1)
    luma = red * LUMA_WEIGHTS[0] + green * LUMA_WEIGHTS[1] + blue * LUMA_WEIGHTS[2]
    maximum = image.max(axis=2)
    ink = (maximum <= threshold) | ((luma <= threshold * 0.65) & (maximum <= threshold * 1.5))
    if edge_strength > 0:
        padded = np.pad(luma, 1, mode="edge")
        height, width = luma.shape
        neighbours = np.zeros_like(luma)
        for y in range(3):
            for x in range(3):
                neighbours += padded[y:y + height, x:x + width]
        ink |= (luma < 170) & ((neighbours - luma) / 8 - luma >= 72 - 36 * edge_strength)
    return ink


def srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    """Convert sRGB 0..255 values to OKLab for perceptual recipe matching."""
    value = rgb.astype(np.float32) / 255.0
    linear = np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)
    r, g, b = linear[..., 0], linear[..., 1], linear[..., 2]
    l = np.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = np.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = np.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return np.stack(
        (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
         1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
         0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s), axis=-1
    )


def adjust_colours(
    image: np.ndarray,
    brightness: float,
    saturation: float,
    red_gain: float = 1.0,
    green_gain: float = 1.0,
    blue_gain: float = 1.0,
    contrast: float = 1.0,
) -> np.ndarray:
    """Apply the GUI colour controls before palette quantisation."""
    rgb = image.astype(np.float32) * np.array((red_gain, green_gain, blue_gain), dtype=np.float32)
    luma = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    adjusted = luma[:, :, None] + (rgb - luma[:, :, None]) * saturation
    adjusted = (adjusted - 127.5) * contrast + 127.5
    return np.clip(adjusted * brightness, 0, 255).astype(np.uint8)


def alpha_mask_image(alpha: Image.Image, invert: bool = False) -> Image.Image:
    """Create an RGB grayscale mask: white is opaque, black is transparent."""
    values = np.asarray(alpha, dtype=np.uint8)
    if invert:
        values = 255 - values
    return Image.fromarray(np.repeat(values[:, :, None], 3, axis=2), mode="RGB")


def preserve_alpha(result: Image.Image, alpha: Image.Image | None) -> Image.Image:
    """Attach source alpha without changing the converter's palette RGB."""
    if alpha is None:
        return result
    rgba = result.convert("RGBA")
    rgba.putalpha(alpha)
    return rgba


def prepare_output_alpha(
    alpha: Image.Image | None,
    mode: str,
    threshold: int,
) -> Image.Image | None:
    """Preserve source alpha or reduce it to transparent/opaque values."""
    if alpha is None:
        return None
    if mode == "preserve":
        return alpha
    if mode != "binary":
        raise ValueError(f"unknown alpha mode: {mode}")
    values = np.where(np.asarray(alpha, dtype=np.uint8) >= threshold, 255, 0).astype(np.uint8)
    return Image.fromarray(values, mode="L")
