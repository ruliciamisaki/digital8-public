#!/usr/bin/env python3
"""Experimental PC-9801 converter using image-balanced reference masks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import pc9801_16_v1 as legacy


PATTERN_MODES = legacy.PATTERN_MODES


def reference_path() -> Path:
    return Path(__file__).with_name("pc9801_reference_v2.json")


def load_reference(path: Path | None = None) -> dict:
    return json.loads((path or reference_path()).read_text(encoding="utf-8"))


def masks_by_coverage(reference: dict) -> dict[int, np.ndarray]:
    masks: dict[int, np.ndarray] = {}
    # V2 JSON is sorted by coverage and then by cross-image score.  Choosing
    # the first pattern fixes one stable visual vocabulary for the whole image.
    for pattern in reference["patterns"]:
        coverage = int(pattern["coverage"])
        if 0 < coverage < 16 and coverage not in masks:
            masks[coverage] = np.asarray(pattern["bits"], dtype=bool).reshape(4, 4)
    for coverage in range(1, 16):
        masks.setdefault(coverage, legacy.bayer_mask(coverage))
    return masks


def coherent_colours(image: np.ndarray, step: int = 10) -> np.ndarray:
    if step <= 1:
        return image
    values = image.astype(np.uint16)
    return np.clip(((values + step // 2) // step) * step, 0, 255).astype(np.uint8)


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
    colour_coherence: int = 10,
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
    palette = legacy.palette_by_id(data, palette_id)
    original, alpha = legacy.load_source_with_alpha(source)
    original = legacy.resize_input(original, resize_640)
    if alpha is not None:
        alpha = legacy.resize_input(alpha, resize_640)
    if pixel_size > 1:
        grid = original.resize((max(1, original.width // pixel_size), max(1, original.height // pixel_size)), Image.Resampling.LANCZOS)
    else:
        grid = original
    smoothed = np.asarray(grid.filter(ImageFilter.MedianFilter(3)), dtype=np.uint8)
    adjusted = legacy.adjust_colours(
        smoothed, brightness, saturation, red_gain, green_gain, blue_gain, contrast,
    )
    rgb = coherent_colours(legacy.apply_yellow_bias(adjusted, yellow_bias), colour_coherence)
    ink = legacy.line_mask(np.asarray(grid, dtype=np.uint8), line_threshold, edge_strength)
    height, width, _ = rgb.shape
    recipe_a, recipe_b, recipe_coverage, targets = legacy.build_candidates(palette, pattern_mode)
    chosen = legacy.choose_candidates(rgb, targets)
    chosen = legacy.stabilise_fill_regions(chosen, rgb, fill_stability)
    a, b, coverage = recipe_a[chosen], recipe_b[chosen], recipe_coverage[chosen]
    indices = a.copy()
    yy, xx = np.indices((height, width))
    masks = {density: legacy.bayer_mask(density) for density in range(1, 16)} if pattern_mode == "gradient" else masks_by_coverage(data)
    for density, mask in masks.items():
        selected = coverage == density
        choose_b = mask[yy % 4, xx % 4]
        indices[selected & choose_b] = b[selected & choose_b]
    darkest = int(np.argmin(palette.astype(np.float32) @ np.array((0.2126, 0.7152, 0.0722), dtype=np.float32)))
    indices[ink] = darkest
    result = Image.fromarray(indices, mode="P")
    result.putpalette(palette.flatten().tolist() + [0] * (768 - len(palette) * 3))
    if pixel_size > 1:
        result = result.resize(original.size, Image.Resampling.NEAREST)
    alpha = legacy.prepare_output_alpha(alpha, alpha_mode, alpha_threshold)
    destination.parent.mkdir(parents=True, exist_ok=True)
    legacy.preserve_alpha(result, alpha).save(destination, optimize=False)
    if alpha_mask is not None:
        alpha_mask.parent.mkdir(parents=True, exist_ok=True)
        mask_alpha = alpha if alpha is not None else Image.new("L", result.size, 255)
        legacy.alpha_mask_image(mask_alpha, invert_alpha_mask).save(alpha_mask)


def main() -> None:
    parser = argparse.ArgumentParser(description="Experimental image-balanced PC-9801 fixed-palette converter")
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
    parser.add_argument("--fill-stability", type=float, default=0.0)
    parser.add_argument("--pattern-mode", choices=PATTERN_MODES, default="reference")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--colour-coherence", type=int, default=10, help="RGB bucket step; 1 disables grouping")
    parser.add_argument("--alpha-mask", type=Path, help="optional RGB grayscale alpha mask; white=opaque, black=transparent")
    parser.add_argument("--invert-alpha-mask", action="store_true", help="invert the optional alpha mask")
    parser.add_argument("--alpha-mode", choices=legacy.ALPHA_MODES, default="preserve", help="preserve source alpha or convert it to binary transparency")
    parser.add_argument("--alpha-threshold", type=int, default=128, help="binary alpha threshold, 0..255")
    args = parser.parse_args()
    if args.pixel_size < 1 or not 0 <= args.fill_stability <= 1 or not 1 <= args.colour_coherence <= 32 or not 0 <= args.alpha_threshold <= 255:
        parser.error("pixel size >= 1; fill stability 0..1; colour coherence 1..32; alpha threshold 0..255")
    convert(args.input, args.output, args.palette, args.pixel_size, args.line_threshold, args.brightness,
            args.saturation, args.yellow_bias, args.edge_ink, args.fill_stability, args.pattern_mode,
            args.reference, args.colour_coherence, args.red, args.green, args.blue, args.contrast,
            args.resize_640, args.alpha_mask, args.invert_alpha_mask,
            args.alpha_mode, args.alpha_threshold)


if __name__ == "__main__":
    main()
