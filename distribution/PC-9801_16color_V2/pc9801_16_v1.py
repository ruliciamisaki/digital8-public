#!/usr/bin/env python3
"""Local PC-9801-style, fixed 16-colour palette converter prototype."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

PATTERN_MODES = ("cell", "reference", "gradient")
ALPHA_MODES = ("preserve", "binary")


def reference_path() -> Path:
    return Path(__file__).with_name("pc9801_reference.json")


def load_reference(path: Path | None = None) -> dict:
    path = path or reference_path()
    return json.loads(path.read_text(encoding="utf-8"))


def bayer_mask(coverage: int) -> np.ndarray:
    ranks = np.array(((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5)))
    return ranks < coverage


def masks_by_coverage(reference: dict) -> dict[int, np.ndarray]:
    masks: dict[int, np.ndarray] = {}
    # Input is frequency-ranked, so the first mask for a density is the one
    # actually observed most often in the supplied PC-9801 examples.
    for pattern in reference["patterns"]:
        coverage = int(pattern["coverage"])
        if 0 < coverage < 16 and coverage not in masks:
            masks[coverage] = np.asarray(pattern["bits"], dtype=bool).reshape(4, 4)
    for coverage in range(1, 16):
        masks.setdefault(coverage, bayer_mask(coverage))
    return masks


def palette_by_id(reference: dict, palette_id: str) -> np.ndarray:
    for palette in reference["palettes"]:
        if palette["id"] == palette_id:
            return np.asarray(palette["colours"], dtype=np.uint8)
    raise ValueError(f"unknown palette: {palette_id}")


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


def apply_yellow_bias(image: np.ndarray, amount: float) -> np.ndarray:
    rgb = image.astype(np.float32)
    rgb[..., 0] += 22 * amount
    rgb[..., 1] += 12 * amount
    rgb[..., 2] -= 26 * amount
    return np.clip(rgb, 0, 255).astype(np.uint8)


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


def resize_input(image: Image.Image, enabled: bool, target_width: int = 640) -> Image.Image:
    """Downscale a wide source while preserving its display aspect ratio."""
    if not enabled or image.width <= target_width:
        return image
    target_height = max(1, round(image.height * target_width / image.width))
    return image.resize(
        (target_width, target_height),
        Image.Resampling.LANCZOS,
        reducing_gap=3.0,
    )


def load_source_with_alpha(source: Path) -> tuple[Image.Image, Image.Image | None]:
    """Return RGB artwork and its original alpha channel, when one exists."""
    image = Image.open(source)
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    if not has_alpha:
        return image.convert("RGB"), None
    rgba = image.convert("RGBA")
    return rgba.convert("RGB"), rgba.getchannel("A")


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


def line_mask(image: np.ndarray, threshold: int, edge_strength: float = 0.0) -> np.ndarray:
    """Extract near-black ink and optional dark contrast contours."""
    value = image.max(axis=2)
    chroma = image.max(axis=2).astype(np.int16) - image.min(axis=2).astype(np.int16)
    near_black = (value <= threshold) & (chroma <= max(32, threshold // 2))
    if edge_strength <= 0:
        return near_black
    luma = image[:, :, 0] * 0.2126 + image[:, :, 1] * 0.7152 + image[:, :, 2] * 0.0722
    padded = np.pad(luma, 1, mode="edge")
    local_sum = np.zeros_like(luma)
    for y in range(3):
        for x in range(3):
            local_sum += padded[y : y + luma.shape[0], x : x + luma.shape[1]]
    neighbour_mean = (local_sum - luma) / 8
    contour = (luma < 170) & ((neighbour_mean - luma) >= 72 - 36 * edge_strength)
    return near_black | contour


def build_candidates(palette: np.ndarray, pattern_mode: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if pattern_mode not in PATTERN_MODES:
        raise ValueError(f"unknown pattern mode: {pattern_mode}")
    # Duplicate palette slots (digital8_half has two black entries) must stay
    # in the saved palette, but need not create duplicate colour recipes.
    unique_indices = []
    seen = set()
    for index, colour in enumerate(palette):
        key = tuple(map(int, colour))
        if key not in seen:
            seen.add(key); unique_indices.append(index)
    coverages = (4, 8, 12) if pattern_mode == "cell" else tuple(range(1, 16))
    recipe_a, recipe_b, recipe_coverage, targets = [], [], [], []
    colours = palette.astype(np.float32)
    for index in unique_indices:
        recipe_a.append(index); recipe_b.append(index); recipe_coverage.append(0); targets.append(colours[index])
    for position, a in enumerate(unique_indices):
        for b in unique_indices[position + 1:]:
            for coverage in coverages:
                recipe_a.append(a); recipe_b.append(b); recipe_coverage.append(coverage)
                targets.append((colours[a] * (16 - coverage) + colours[b] * coverage) / 16)
    return (np.asarray(recipe_a, dtype=np.uint8), np.asarray(recipe_b, dtype=np.uint8),
            np.asarray(recipe_coverage, dtype=np.uint8), np.asarray(targets, dtype=np.float32))


def choose_candidates(rgb: np.ndarray, targets: np.ndarray, chunk_size: int = 4096) -> np.ndarray:
    source = srgb_to_oklab(rgb.reshape(-1, 3))
    candidates = srgb_to_oklab(targets)
    candidate_norm = np.sum(candidates * candidates, axis=1)
    result = np.empty(len(source), dtype=np.uint16)
    for start in range(0, len(source), chunk_size):
        part = source[start : start + chunk_size]
        # ||x-y||^2 = ||x||^2 + ||y||^2 - 2 x.y.  This gives exactly the
        # same result without allocating a large pixels*candidates*3 array.
        scores = np.sum(part * part, axis=1)[:, None] + candidate_norm[None, :] - 2 * (part @ candidates.T)
        result[start : start + len(part)] = np.argmin(scores, axis=1)
    return result.reshape(rgb.shape[:2])


def stabilise_fill_regions(chosen: np.ndarray, rgb: np.ndarray, strength: float) -> np.ndarray:
    """Remove isolated recipe changes inside locally similar colour regions.

    This operates on selected fill recipes, not the ink mask.  Neighbour votes
    are accepted only when their source RGB is close to the centre pixel, so a
    real colour boundary does not get crossed merely because its recipe is
    locally frequent.
    """
    if strength <= 0:
        return chosen
    threshold = 4.0 + 36.0 * strength
    iterations = 1 + int(strength * 2.999)
    height, width = chosen.shape
    offsets = ((0, 0), (-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    centre_rgb = rgb.astype(np.int32)
    for _ in range(iterations):
        padded_ids = np.pad(chosen, 1, mode="edge")
        padded_rgb = np.pad(centre_rgb, ((1, 1), (1, 1), (0, 0)), mode="edge")
        maps, valid = [], []
        for dy, dx in offsets:
            ids = padded_ids[1 + dy : 1 + dy + height, 1 + dx : 1 + dx + width]
            neighbour = padded_rgb[1 + dy : 1 + dy + height, 1 + dx : 1 + dx + width]
            difference = neighbour - centre_rgb
            maps.append(ids)
            valid.append(np.sum(difference * difference, axis=2) <= threshold * threshold)
        maps_array = np.stack(maps)
        valid_array = np.stack(valid)
        votes = np.stack([np.sum((maps_array == maps_array[index]) & valid_array, axis=0) for index in range(len(offsets))])
        best = np.argmax(votes, axis=0)
        chosen = np.take_along_axis(maps_array, best[None, :, :], axis=0)[0]
    return chosen


def convert(
    source: Path,
    destination: Path,
    palette_id: str,
    pixel_size: int = 1,
    line_threshold: int = 58,
    brightness: float = 1.0,
    saturation: float = 1.0,
    yellow_bias: float = 0.0,
    edge_strength: float = 0.0,
    fill_stability: float = 0.0,
    pattern_mode: str = "reference",
    reference: Path | None = None,
    red_gain: float = 1.0,
    green_gain: float = 1.0,
    blue_gain: float = 1.0,
    contrast: float = 1.0,
    resize_640: bool = False,
    alpha_mask: Path | None = None,
    invert_alpha_mask: bool = False,
    alpha_mode: str = "preserve",
    alpha_threshold: int = 128,
) -> None:
    data = load_reference(reference)
    palette = palette_by_id(data, palette_id)
    original, alpha = load_source_with_alpha(source)
    original = resize_input(original, resize_640)
    if alpha is not None:
        alpha = resize_input(alpha, resize_640)
    if pixel_size > 1:
        grid = original.resize((max(1, original.width // pixel_size), max(1, original.height // pixel_size)), Image.Resampling.LANCZOS)
    else:
        grid = original
    smoothed = np.asarray(grid.filter(ImageFilter.MedianFilter(3)), dtype=np.uint8)
    rgb = apply_yellow_bias(
        adjust_colours(smoothed, brightness, saturation, red_gain, green_gain, blue_gain, contrast),
        yellow_bias,
    )
    ink = line_mask(np.asarray(grid, dtype=np.uint8), line_threshold, edge_strength)
    height, width, _ = rgb.shape
    recipe_a, recipe_b, recipe_coverage, targets = build_candidates(palette, pattern_mode)
    chosen = choose_candidates(rgb, targets)
    chosen = stabilise_fill_regions(chosen, rgb, fill_stability)
    a, b, coverage = recipe_a[chosen], recipe_b[chosen], recipe_coverage[chosen]
    indices = a.copy()
    yy, xx = np.indices((height, width))
    masks = {density: bayer_mask(density) for density in range(1, 16)} if pattern_mode == "gradient" else masks_by_coverage(data)
    for density, mask in masks.items():
        selected = coverage == density
        choose_b = mask[yy % 4, xx % 4]
        indices[selected & choose_b] = b[selected & choose_b]
    darkest = int(np.argmin(palette.astype(np.float32) @ np.array((0.2126, 0.7152, 0.0722), dtype=np.float32)))
    indices[ink] = darkest
    result = Image.fromarray(indices, mode="P")
    flat = palette.flatten().tolist() + [0] * (768 - len(palette) * 3)
    result.putpalette(flat)
    if pixel_size > 1:
        result = result.resize(original.size, Image.Resampling.NEAREST)
    alpha = prepare_output_alpha(alpha, alpha_mode, alpha_threshold)
    destination.parent.mkdir(parents=True, exist_ok=True)
    preserve_alpha(result, alpha).save(destination, optimize=False)
    if alpha_mask is not None:
        alpha_mask.parent.mkdir(parents=True, exist_ok=True)
        mask_alpha = alpha if alpha is not None else Image.new("L", result.size, 255)
        alpha_mask_image(mask_alpha, invert_alpha_mask).save(alpha_mask)


def main() -> None:
    parser = argparse.ArgumentParser(description="PC-9801-style fixed-palette 16-colour converter")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--palette", default="palette_01")
    parser.add_argument("--pixel-size", type=int, default=1)
    parser.add_argument("--line-threshold", type=int, default=58)
    parser.add_argument("--brightness", type=float, default=1.0)
    parser.add_argument("--saturation", type=float, default=1.0)
    parser.add_argument("--red", type=float, default=1.0)
    parser.add_argument("--green", type=float, default=1.0)
    parser.add_argument("--blue", type=float, default=1.0)
    parser.add_argument("--contrast", type=float, default=1.0)
    parser.add_argument("--resize-640", action="store_true")
    parser.add_argument("--yellow-bias", type=float, default=0.0)
    parser.add_argument("--edge-ink", type=float, default=0.0)
    parser.add_argument("--fill-stability", type=float, default=0.0, help="merge isolated fill recipes inside similar-colour regions, 0..1")
    parser.add_argument("--pattern-mode", choices=PATTERN_MODES, default="reference")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--alpha-mask", type=Path, help="optional RGB grayscale alpha mask; white=opaque, black=transparent")
    parser.add_argument("--invert-alpha-mask", action="store_true", help="invert the optional alpha mask")
    parser.add_argument("--alpha-mode", choices=ALPHA_MODES, default="preserve", help="preserve source alpha or convert it to binary transparency")
    parser.add_argument("--alpha-threshold", type=int, default=128, help="binary alpha threshold, 0..255")
    args = parser.parse_args()
    colour_controls = (args.red, args.green, args.blue, args.brightness, args.contrast)
    if args.pixel_size < 1 or not 0 <= args.fill_stability <= 1 or not 0 <= args.alpha_threshold <= 255 or not all(0.5 <= value <= 1.5 for value in colour_controls) or not 0 <= args.saturation <= 2:
        parser.error("pixel size >= 1; fill stability 0..1; alpha threshold 0..255; RGB/brightness/contrast 0.5..1.5; saturation 0..2")
    convert(
        args.input, args.output, args.palette, args.pixel_size, args.line_threshold,
        args.brightness, args.saturation, args.yellow_bias, args.edge_ink,
        args.fill_stability, args.pattern_mode, args.reference,
        args.red, args.green, args.blue, args.contrast, args.resize_640,
        args.alpha_mask, args.invert_alpha_mask,
        args.alpha_mode, args.alpha_threshold,
    )


if __name__ == "__main__":
    main()
