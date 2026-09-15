#!/usr/bin/env python3
"""Experimental PC-8801 converter using image-balanced empirical tile data."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import pc8801_digital8_v1 as legacy


PATTERN_MODES = legacy.PATTERN_MODES
PALETTE = legacy.PALETTE


@dataclass(frozen=True)
class TileRecipe:
    name: str
    family: str
    tile: tuple[int, ...]
    shape: tuple[int, int]
    target: tuple[float, float, float]
    penalty: float


def reference_path() -> Path:
    return Path(__file__).with_name("pc8801_reference_v2.json")


def load_reference(path: Path | None = None) -> dict:
    return json.loads((path or reference_path()).read_text(encoding="utf-8"))


def rendered_legacy_tile(recipe: legacy.Recipe, pattern_mode: str) -> np.ndarray:
    if recipe.fixed_tile is not None:
        return np.asarray(recipe.fixed_tile, dtype=np.uint8).reshape(recipe.tile_shape)
    use_44 = recipe.tile_shape == (4, 4) or pattern_mode == "normal-44"
    if use_44:
        ranks = legacy.TILES_44[recipe.tile % len(legacy.TILES_44)]
        coverage = round(recipe.coverage * 16 / recipe.denominator)
    else:
        ranks = legacy.TILES[recipe.tile % len(legacy.TILES)]
        coverage = recipe.coverage
    return np.where(ranks < coverage, recipe.b, recipe.a).astype(np.uint8)


def build_recipes(pattern_mode: str, reference: dict) -> tuple[TileRecipe, ...]:
    pattern_mode = legacy.normalise_pattern_mode(pattern_mode)
    recipes: list[TileRecipe] = []
    for recipe in legacy.RECIPES:
        if pattern_mode == "normal-42" and recipe.fine_pattern:
            continue
        if pattern_mode == "normal-44" and not (recipe.fine_pattern or recipe.a == recipe.b):
            continue
        tile = rendered_legacy_tile(recipe, pattern_mode)
        target = PALETTE[tile].astype(np.float32).mean(axis=(0, 1))
        # In V2 the observed patterns win a tie.  Hand-authored fixed skin and
        # shadow tiles remain close fallbacks; generated density ramps carry a
        # small cost so they do not mask empirical structures.
        penalty = 60.0 if recipe.fixed_tile is not None else 180.0
        if recipe.a == recipe.b:
            penalty = 0.0
        recipes.append(
            TileRecipe(recipe.name, recipe.family, tuple(int(value) for value in tile.reshape(-1)), tile.shape,
                       tuple(float(value) for value in target), penalty)
        )

    # Many source patterns have the same colour average.  Keep the strongest
    # one for each family/shape/target so the uncurated sequence cannot cause
    # arbitrary pattern flicker or an unnecessarily large candidate matrix.
    empirical: dict[tuple, dict] = {}
    for pattern in reference["patterns"]:
        shape = (int(pattern["tile_height"]), int(pattern["tile_width"]))
        if pattern_mode == "normal-42" and shape != (2, 4):
            continue
        if pattern_mode == "normal-44" and shape != (4, 4):
            continue
        target_key = tuple(round(float(value), 3) for value in pattern["target"])
        key = (pattern["family"], shape, target_key)
        if key not in empirical or float(pattern["score"]) > float(empirical[key]["score"]):
            empirical[key] = pattern

    for pattern in empirical.values():
        shape = (int(pattern["tile_height"]), int(pattern["tile_width"]))
        support = int(pattern["image_support"])
        penalty = max(0.0, 150.0 - support * 12.0)
        if pattern_mode == "normal-mix" and shape == (4, 4):
            penalty += 180.0
        elif pattern_mode == "gradient" and shape == (2, 4):
            penalty += 80.0
        recipes.append(
            TileRecipe(
                f"observed-{pattern['rank']}",
                str(pattern["family"]),
                tuple(int(value) for value in pattern["tile"]),
                shape,
                tuple(float(value) for value in pattern["target"]),
                penalty,
            )
        )
    return tuple(recipes)


def coherent_colours(image: np.ndarray, step: int = 12) -> np.ndarray:
    """Map nearby source colours to one deterministic logical-colour bucket."""
    if step <= 1:
        return image
    values = image.astype(np.uint16)
    return np.clip(((values + step // 2) // step) * step, 0, 255).astype(np.uint8)


def family_masks(image: np.ndarray, pattern_mode: str) -> dict[str, np.ndarray]:
    rgb = image.astype(np.float32)
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    chroma = maximum - minimum
    saturation = np.divide(chroma, maximum, out=np.zeros_like(chroma), where=maximum > 0)
    hue = np.zeros_like(maximum)
    non_gray = chroma > 0
    red = non_gray & (rgb[:, :, 0] == maximum)
    green = non_gray & (rgb[:, :, 1] == maximum)
    blue = non_gray & (rgb[:, :, 2] == maximum)
    hue[red] = np.mod((rgb[:, :, 1][red] - rgb[:, :, 2][red]) / chroma[red], 6) * 60
    hue[green] = ((rgb[:, :, 2][green] - rgb[:, :, 0][green]) / chroma[green] + 2) * 60
    hue[blue] = ((rgb[:, :, 0][blue] - rgb[:, :, 1][blue]) / chroma[blue] + 4) * 60
    floor = 0.025 if pattern_mode == "gradient" else 0.075
    return {
        "dark": maximum < 56,
        "neutral": (maximum >= 56) & (saturation < floor) & (maximum < 224),
        "light": (maximum >= 56) & (saturation < floor) & (maximum >= 224),
        "warm": (saturation >= floor) & ((hue < 65) | (hue >= 345)),
        "green": (saturation >= floor) & (hue >= 65) & (hue < 145),
        "teal": (saturation >= floor) & (hue >= 145) & (hue < 195),
        "cool": (saturation >= floor) & (hue >= 195) & (hue < 265),
        "magenta": (saturation >= floor) & (hue >= 265) & (hue < 345),
    }


def choose_recipes(
    image: np.ndarray,
    recipes: tuple[TileRecipe, ...],
    yellow_bias: float,
    pattern_mode: str,
    colour_coherence: int = 12,
    chunk_size: int = 4096,
) -> np.ndarray:
    source_image = coherent_colours(image, colour_coherence).astype(np.float32)
    result = np.zeros(image.shape[:2], dtype=np.uint16)
    targets = np.asarray([recipe.target for recipe in recipes], dtype=np.float32)
    penalties = np.asarray([recipe.penalty for recipe in recipes], dtype=np.float32)
    masks = family_masks(source_image.astype(np.uint8), pattern_mode)

    for family, mask in masks.items():
        allowed = np.asarray([index for index, recipe in enumerate(recipes) if recipe.family == family], dtype=np.uint16)
        if not mask.any() or not len(allowed):
            continue
        source = source_image[mask]
        chosen = np.empty(len(source), dtype=np.uint16)
        candidate_targets = targets[allowed]
        candidate_penalties = penalties[allowed]
        yellow = np.asarray([6 in recipes[int(index)].tile for index in allowed])
        for start in range(0, len(source), chunk_size):
            part = source[start : start + chunk_size]
            delta = part[:, None, :] - candidate_targets[None, :, :]
            scores = np.sum(delta * delta, axis=2) + candidate_penalties[None, :]
            if family == "warm" and yellow_bias:
                scores[:, yellow] -= yellow_bias * 16000.0
            if family == "warm" and yellow.any():
                maximum = part.max(axis=1)
                minimum = part.min(axis=1)
                saturation = np.divide(maximum - minimum, maximum, out=np.zeros_like(maximum), where=maximum > 0)
                pale_skin = (
                    (part[:, 0] >= part[:, 1]) & (part[:, 1] >= part[:, 2])
                    & (maximum >= 205) & (saturation >= 0.07) & (saturation <= 0.35)
                )
                scores[:, yellow] -= pale_skin[:, None] * 1400.0
            chosen[start : start + len(part)] = allowed[np.argmin(scores, axis=1)]
        result[mask] = chosen
    return result


def render(recipe_ids: np.ndarray, ink: np.ndarray, recipes: tuple[TileRecipe, ...]) -> np.ndarray:
    height, width = recipe_ids.shape
    yy, xx = np.indices((height, width))
    output = np.zeros((height, width), dtype=np.uint8)
    for index, recipe in enumerate(recipes):
        selected = recipe_ids == index
        if not selected.any():
            continue
        tile = np.asarray(recipe.tile, dtype=np.uint8).reshape(recipe.shape)
        output[selected] = tile[yy[selected] % recipe.shape[0], xx[selected] % recipe.shape[1]]
    output[ink] = 0
    return output


def convert(
    source: Path,
    destination: Path,
    pixel_size: int,
    line_threshold: int,
    brightness: float = 1.0,
    saturation: float = 1.0,
    yellow_bias: float = 0.0,
    edge_strength: float = 0.0,
    pc8801_200: bool = False,
    pattern_mode: str | bool = "normal-mix",
    gradient_mode: bool | None = None,
    reference: Path | None = None,
    colour_coherence: int = 12,
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
    pattern_mode = legacy.normalise_pattern_mode(pattern_mode, gradient_mode)
    original, alpha = legacy.load_source_with_alpha(source)
    original = legacy.resize_input(original, resize_640)
    if alpha is not None:
        alpha = legacy.resize_input(alpha, resize_640)
    output_size = original.size
    if pc8801_200:
        logical_height = (original.height + 1) // 2
        original = original.resize((original.width, logical_height), Image.Resampling.LANCZOS)
    if pixel_size > 1:
        grid = original.resize((max(1, original.width // pixel_size), max(1, original.height // pixel_size)), Image.Resampling.LANCZOS)
    else:
        grid = original
    smoothed = grid.filter(ImageFilter.MedianFilter(size=3))
    pixels = legacy.adjust_colours(
        np.asarray(smoothed, dtype=np.uint8), brightness, saturation,
        red_gain, green_gain, blue_gain, contrast,
    )
    ink = legacy.line_mask(np.asarray(grid, dtype=np.uint8), line_threshold, edge_strength)
    recipes = build_recipes(pattern_mode, load_reference(reference))
    indices = render(choose_recipes(pixels, recipes, yellow_bias, pattern_mode, colour_coherence), ink, recipes)
    result = legacy.paletted_image(indices)
    if pixel_size > 1:
        result = result.resize(original.size, Image.Resampling.NEAREST)
    if pc8801_200:
        result = result.resize((output_size[0], original.height * 2), Image.Resampling.NEAREST)
        if result.height != output_size[1]:
            result = result.crop((0, 0, output_size[0], output_size[1]))
    alpha = legacy.prepare_output_alpha(alpha, alpha_mode, alpha_threshold, pc8801_200, output_size)
    destination.parent.mkdir(parents=True, exist_ok=True)
    legacy.preserve_alpha(result, alpha).save(destination, optimize=False)
    if alpha_mask is not None:
        alpha_mask.parent.mkdir(parents=True, exist_ok=True)
        mask_alpha = alpha if alpha is not None else Image.new("L", result.size, 255)
        legacy.alpha_mask_image(mask_alpha, invert_alpha_mask).save(alpha_mask)


def main() -> None:
    parser = argparse.ArgumentParser(description="Experimental image-balanced PC-8801 digital-8 converter")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
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
    parser.add_argument("--pc8801-200", action="store_true")
    parser.add_argument("--pattern-mode", choices=PATTERN_MODES, default="normal-mix")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--colour-coherence", type=int, default=12, help="RGB bucket step; 1 disables grouping")
    parser.add_argument("--alpha-mask", type=Path, help="optional RGB grayscale alpha mask; white=opaque, black=transparent")
    parser.add_argument("--invert-alpha-mask", action="store_true", help="invert the optional alpha mask")
    parser.add_argument("--alpha-mode", choices=legacy.ALPHA_MODES, default="preserve", help="preserve source alpha or convert it to binary transparency")
    parser.add_argument("--alpha-threshold", type=int, default=128, help="binary alpha threshold, 0..255")
    args = parser.parse_args()
    if args.pixel_size < 1 or not 1 <= args.colour_coherence <= 32 or not 0 <= args.alpha_threshold <= 255:
        parser.error("pixel size >= 1; colour coherence 1..32; alpha threshold 0..255")
    convert(args.input, args.output, args.pixel_size, args.line_threshold, args.brightness, args.saturation,
            args.yellow_bias, args.edge_ink, args.pc8801_200, args.pattern_mode, reference=args.reference,
            colour_coherence=args.colour_coherence, red_gain=args.red, green_gain=args.green,
            blue_gain=args.blue, contrast=args.contrast, resize_640=args.resize_640,
            alpha_mask=args.alpha_mask, invert_alpha_mask=args.invert_alpha_mask,
            alpha_mode=args.alpha_mode, alpha_threshold=args.alpha_threshold)


if __name__ == "__main__":
    main()
