#!/usr/bin/env python3
"""PC-8801 V4 experiment: coherent colour regions and six fixed two-colour tiles."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import pc8801_digital8_v1 as legacy
import pc8801_digital8_v2 as v2


# Row order and phase are fixed.  The display doubles each native row in 200-line mode.
TEMPLATES: dict[str, tuple[str, ...]] = {
    "solid": ("A",),
    "vertical": ("AB",),
    "checker": ("AB", "BA"),
    "quarter_single": ("AB", "AA"),
    "quarter_staggered": ("AABA", "BAAA"),
    "eighth": ("AAAB", "AAAA"),
}
HALF_TONES = ("checker", "vertical")
QUARTER_TONES = ("quarter_staggered", "quarter_single")


@dataclass(frozen=True)
class TileRecipe:
    name: str
    a: int
    b: int
    tile: tuple[int, ...]
    shape: tuple[int, int]
    target: tuple[float, float, float]
    penalty: float


def build_recipes(
    half_tone: str = "checker",
    quarter_tone: str = "quarter_staggered",
    reference: dict | None = None,
) -> tuple[TileRecipe, ...]:
    if half_tone not in HALF_TONES or quarter_tone not in QUARTER_TONES:
        raise ValueError("invalid half-tone or quarter-tone preference")
    reference = v2.load_reference() if reference is None else reference
    observed_pairs = {
        tuple(sorted(int(colour) for colour in pattern["colours"]))
        for pattern in reference["patterns"] if len(pattern["colours"]) == 2
    }
    palette = legacy.PALETTE.astype(np.float32)
    brightness = palette @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    recipes: list[TileRecipe] = []
    for a in range(len(palette)):
        for b in range(len(palette)):
            if a == b:
                names = ("solid",)
            else:
                names = tuple(name for name in TEMPLATES if name != "solid")
            for name in names:
                # The half-tone reversed A/B pair has the same average.  Pick
                # one fixed polarity, while 3:1 and 7:1 keep both directions.
                if name in HALF_TONES and brightness[a] < brightness[b]:
                    continue
                rows = TEMPLATES[name]
                tile = tuple(a if char == "A" else b for row in rows for char in row)
                target = tuple(float(value) for value in palette[np.asarray(tile)].mean(axis=0))
                if name == "solid":
                    penalty = 0.0
                else:
                    contrast = float(np.sum((palette[a] - palette[b]) ** 2))
                    penalty = contrast * 0.002
                    if tuple(sorted((a, b))) not in observed_pairs:
                        penalty += 240.0
                    if name in HALF_TONES and name != half_tone:
                        penalty += 500.0
                    if name in QUARTER_TONES and name != quarter_tone:
                        penalty += 500.0
                    # Prefer black/white for neutral tones over complementary
                    # chromatic pairs with the same RGB average.
                    if a not in (0, 7) and b not in (0, 7) and a ^ b == 7:
                        penalty += 1800.0
                recipes.append(TileRecipe(name, a, b, tile, (len(rows), len(rows[0])), target, penalty))
    return tuple(recipes)


def source_ink(image: np.ndarray, threshold: int, edge_strength: float = 0.0) -> np.ndarray:
    """Promote source black and near-black to ink before any colour clustering."""
    if not 0 <= threshold <= 120:
        raise ValueError("black threshold must be 0..120")
    raw = image.astype(np.float32)
    maximum = raw.max(axis=2)
    luma = raw @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    near_black = (maximum <= threshold) | ((luma <= threshold * 0.65) & (maximum <= threshold * 1.5))
    return near_black | legacy.line_mask(image, threshold, edge_strength)


def choose_recipes(
    image: np.ndarray,
    ink: np.ndarray,
    recipes: tuple[TileRecipe, ...],
    colour_groups: int = 48,
) -> np.ndarray:
    """Assign one tile recipe to each image-wide group of similar colours."""
    if not 2 <= colour_groups <= 64:
        raise ValueError("colour groups must be 2..64")
    grouped = Image.fromarray(image, "RGB").quantize(
        colors=colour_groups, method=Image.Quantize.FASTOCTREE,
    )
    groups = np.asarray(grouped, dtype=np.uint8)
    result = np.zeros(groups.shape, dtype=np.uint16)
    targets = np.asarray([recipe.target for recipe in recipes], dtype=np.float32)
    penalties = np.asarray([recipe.penalty for recipe in recipes], dtype=np.float32)
    target_chroma = targets - targets.mean(axis=1, keepdims=True)
    target_chroma_length = np.linalg.norm(target_chroma, axis=1)
    for group in np.unique(groups[~ink]):
        selected = (groups == group) & ~ink
        colour = image[selected].astype(np.float32).mean(axis=0)
        difference = targets - colour
        scores = np.sum(difference * difference, axis=1) + penalties
        source_chroma = colour - colour.mean()
        source_chroma_length = float(np.linalg.norm(source_chroma))
        if source_chroma_length > 12:
            cosine = (target_chroma @ source_chroma) / np.maximum(
                target_chroma_length * source_chroma_length, 1.0)
            scores += 6000.0 * min(1.0, source_chroma_length / 80.0) * (1.0 - cosine)
        # Keep a clearly dominant source primary dominant in the tile average.
        # In particular, green eyes must not become yellow-black merely because
        # that pair happens to have a closer arithmetic RGB average.
        for primary in range(3):
            others = [channel for channel in range(3) if channel != primary]
            if all(colour[primary] - colour[channel] >= 35 for channel in others):
                wrong_hue = np.any(targets[:, primary, None] <= targets[:, others] + 5, axis=1)
                scores[wrong_hue] += 9000.0
                break
        result[selected] = int(np.argmin(scores))
    return result


def render(recipe_ids: np.ndarray, ink: np.ndarray, recipes: tuple[TileRecipe, ...]) -> np.ndarray:
    height, width = recipe_ids.shape
    yy, xx = np.indices((height, width))
    output = np.zeros((height, width), dtype=np.uint8)
    for index in np.unique(recipe_ids[~ink]):
        selected = (recipe_ids == index) & ~ink
        recipe = recipes[int(index)]
        tile = np.asarray(recipe.tile, dtype=np.uint8).reshape(recipe.shape)
        output[selected] = tile[yy[selected] % recipe.shape[0], xx[selected] % recipe.shape[1]]
    output[ink] = 0
    return output


def convert_image(
    source: Path,
    pixel_size: int = 1,
    line_threshold: int = 72,
    brightness: float = 1.0,
    saturation: float = 1.0,
    edge_strength: float = 0.0,
    pc8801_200: bool = True,
    half_tone: str = "checker",
    quarter_tone: str = "quarter_staggered",
    reference: Path | None = None,
    colour_groups: int = 48,
    red_gain: float = 1.0,
    green_gain: float = 1.0,
    blue_gain: float = 1.0,
    contrast: float = 1.0,
    resize_640: bool = False,
    alpha_mode: str = "preserve",
    alpha_threshold: int = 128,
) -> tuple[Image.Image, Image.Image | None]:
    if pixel_size < 1 or not 0 <= alpha_threshold <= 255:
        raise ValueError("pixel size >= 1; alpha threshold 0..255")
    original, alpha = legacy.load_source_with_alpha(source)
    original = legacy.resize_input(original, resize_640)
    if alpha is not None:
        alpha = legacy.resize_input(alpha, resize_640)
    output_size = original.size
    if pc8801_200:
        original = original.resize((original.width, (original.height + 1) // 2), Image.Resampling.LANCZOS)
    if pixel_size > 1:
        grid = original.resize(
            (max(1, original.width // pixel_size), max(1, original.height // pixel_size)),
            Image.Resampling.LANCZOS,
        )
    else:
        grid = original
    raw = np.asarray(grid, dtype=np.uint8)
    ink = source_ink(raw, line_threshold, edge_strength)
    smoothed = np.asarray(grid.filter(ImageFilter.MedianFilter(size=3)), dtype=np.uint8)
    pixels = legacy.adjust_colours(smoothed, brightness, saturation, red_gain, green_gain, blue_gain, contrast)
    recipes = build_recipes(half_tone, quarter_tone, v2.load_reference(reference))
    indices = render(choose_recipes(pixels, ink, recipes, colour_groups), ink, recipes)
    result = legacy.paletted_image(indices)
    if pixel_size > 1:
        result = result.resize(original.size, Image.Resampling.NEAREST)
    if pc8801_200:
        result = result.resize((output_size[0], original.height * 2), Image.Resampling.NEAREST)
        if result.height != output_size[1]:
            result = result.crop((0, 0, output_size[0], output_size[1]))
    alpha = legacy.prepare_output_alpha(alpha, alpha_mode, alpha_threshold, pc8801_200, output_size)
    return legacy.preserve_alpha(result, alpha), alpha


def convert(source: Path, destination: Path, **settings: object) -> None:
    alpha_mask = settings.pop("alpha_mask", None)
    invert_alpha_mask = bool(settings.pop("invert_alpha_mask", False))
    result, alpha = convert_image(source, **settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(destination, optimize=False)
    if alpha_mask is not None:
        mask_path = Path(alpha_mask)
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        mask_alpha = alpha if alpha is not None else Image.new("L", result.size, 255)
        legacy.alpha_mask_image(mask_alpha, invert_alpha_mask).save(mask_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="PC-8801 V4 restricted two-colour tile experiment")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pixel-size", type=int, default=1)
    parser.add_argument("--line-threshold", type=int, default=72)
    parser.add_argument("--brightness", type=float, default=1.0)
    parser.add_argument("--saturation", type=float, default=1.0)
    parser.add_argument("--red", type=float, default=1.0)
    parser.add_argument("--green", type=float, default=1.0)
    parser.add_argument("--blue", type=float, default=1.0)
    parser.add_argument("--contrast", type=float, default=1.0)
    parser.add_argument("--resize-640", action="store_true")
    parser.add_argument("--edge-ink", type=float, default=0.0)
    parser.add_argument("--pc8801-200", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--half-tone", choices=HALF_TONES, default="checker")
    parser.add_argument("--quarter-tone", choices=QUARTER_TONES, default="quarter_staggered")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--colour-groups", type=int, default=48)
    parser.add_argument("--alpha-mask", type=Path)
    parser.add_argument("--invert-alpha-mask", action="store_true")
    parser.add_argument("--alpha-mode", choices=legacy.ALPHA_MODES, default="preserve")
    parser.add_argument("--alpha-threshold", type=int, default=128)
    args = parser.parse_args()
    convert(args.input, args.output, pixel_size=args.pixel_size, line_threshold=args.line_threshold,
            brightness=args.brightness, saturation=args.saturation, red_gain=args.red,
            green_gain=args.green, blue_gain=args.blue, contrast=args.contrast,
            resize_640=args.resize_640, edge_strength=args.edge_ink,
            pc8801_200=args.pc8801_200, half_tone=args.half_tone,
            quarter_tone=args.quarter_tone, reference=args.reference,
            colour_groups=args.colour_groups, alpha_mask=args.alpha_mask,
            invert_alpha_mask=args.invert_alpha_mask, alpha_mode=args.alpha_mode,
            alpha_threshold=args.alpha_threshold)


if __name__ == "__main__":
    main()
