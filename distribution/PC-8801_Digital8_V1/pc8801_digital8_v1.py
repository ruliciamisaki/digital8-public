#!/usr/bin/env python3
"""Deterministic PC-8801 digital 8-colour converter.

The converter intentionally never performs a nearest-colour conversion to the
whole 8-colour palette.  It first selects a *logical colour recipe* from a
restricted hue family, then renders that recipe with two PC-8801 colours and a
fixed tile pattern.  A warm source pixel therefore cannot select blue/cyan.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageFilter


# RGB values used for the usual PC-8801 digital 8-colour display.
PALETTE = np.array(
    [
        (0, 0, 0),       # 0: black
        (0, 0, 255),     # 1: blue
        (255, 0, 0),     # 2: red
        (255, 0, 255),   # 3: magenta
        (0, 255, 0),     # 4: green
        (0, 255, 255),   # 5: cyan
        (255, 255, 0),   # 6: yellow
        (255, 255, 255), # 7: white
    ],
    dtype=np.uint8,
)


# A PC-8801 dot is twice as tall as it is wide.  A 4 x 2 native-dot tile is
# therefore visually square on the 640 x 200 display (and 640 x 400 preview).
# Every rank matrix is a permutation of 0..7, so ``rank < coverage`` paints
# exactly coverage/8 of a two-colour tile.  There is deliberately no horizontal
# stripe tile: it reads as an unwanted scanline effect on this display.
# Occupancy orders derived from the 4x2 structures recurring in the supplied
# PC-8801 captures.  They deliberately avoid the old full-height stripe.
SPARSE_42 = np.array(((2, 4, 0, 6), (1, 5, 3, 7)))
CHECKER_42 = np.array(((0, 5, 2, 7), (6, 3, 4, 1)))
DIAGONAL_42 = np.array(((0, 6, 3, 5), (4, 2, 7, 1)))
CLUSTER_42 = np.array(((0, 1, 5, 6), (2, 3, 4, 7)))
SCATTER_42 = np.array(((3, 0, 5, 2), (6, 4, 1, 7)))
TILES = (SPARSE_42, CHECKER_42, DIAGONAL_42, CLUSTER_42, SCATTER_42)

# 4 x 4 native-dot tiles are deliberately reserved for fine intermediate
# tones.  They have sixteen occupancy steps rather than eight, which lets a
# white-to-cyan sky begin with one cyan dot in sixteen instead of jumping to
# one in eight.  These are ordered, dispersed structures; none forms a full
# horizontal stripe when displayed with the PC-8801's 1:2 dot aspect.
SPARSE_44 = np.array(((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5)))
DIAGONAL_44 = np.array(((0, 12, 3, 15), (8, 4, 11, 7), (2, 14, 1, 13), (10, 6, 9, 5)))
SCATTER_44 = np.array(((5, 0, 13, 8), (12, 9, 4, 1), (3, 6, 15, 10), (14, 11, 2, 7)))
TILES_44 = (SPARSE_44, DIAGONAL_44, SCATTER_44)

PATTERN_MODES = ("normal-42", "normal-44", "normal-mix", "gradient")


@dataclass(frozen=True)
class Recipe:
    name: str
    family: str
    a: int
    b: int
    coverage: int  # B coverage, 0..8
    tile: int
    fixed_tile: tuple[int, ...] | None = None
    tile_shape: tuple[int, int] = (2, 4)
    denominator: int = 8
    fine_pattern: bool = False

    @property
    def target(self) -> np.ndarray:
        # Convert before multiplication.  Keeping uint8 here silently wraps
        # 255 * 12 to 244 and wrecks the logical-colour nearest match.
        colours = PALETTE.astype(np.float32)
        if self.fixed_tile is not None:
            return colours[np.asarray(self.fixed_tile)].mean(axis=0)
        return (colours[self.a] * (self.denominator - self.coverage) + colours[self.b] * self.coverage) / self.denominator


# These are logical colours, not an RGB palette to be applied directly.
# The family field is the guardrail that stops blue/cyan entering skin tones.
BASE_RECIPES = (
    Recipe("ink", "dark", 0, 0, 0, 0),
    Recipe("paper", "light", 7, 7, 0, 0),
    # Five neutral steps; neutral black itself is selected through "ink".
    Recipe("neutral-very-light", "neutral", 7, 0, 1, 1),
    Recipe("neutral-light", "neutral", 7, 0, 3, 0),
    Recipe("neutral-mid", "neutral", 7, 0, 4, 4),
    Recipe("neutral-dark", "neutral", 7, 0, 6, 2),
    Recipe("neutral-very-dark", "neutral", 7, 0, 7, 3),
    # Warm (skin / red / yellow): a finer white-to-red ramp prevents a face
    # from collapsing into one strong red stripe pattern.
    Recipe("skin-1", "warm", 7, 2, 1, 1),
    Recipe("skin-2", "warm", 7, 2, 2, 0),
    Recipe("skin-4", "warm", 7, 2, 3, 3),
    Recipe("skin-6", "warm", 7, 2, 4, 4),
    Recipe("skin-8", "warm", 7, 2, 5, 1),
    # Observed PC-8801 skin tile: Y W W W / W W Y W.
    Recipe("skin-yellow-white", "warm", 7, 6, 0, 0, (7, 7, 6, 7, 6, 7, 7, 7)),
    # Reference #46 in the original sample-only sheet: yellow-white checker.
    Recipe("skin-yellow-checker", "warm", 7, 6, 0, 0, (7, 6, 7, 6, 6, 7, 6, 7)),
    # Observed shadow tile: M Y W Y / Y W R W.
    Recipe("skin-shadow-multi", "warm", 7, 2, 0, 0, (3, 6, 7, 6, 6, 7, 2, 7)),
    # Reference red-black patterns (6:2 and checkerboard 4:4).
    Recipe("warm-red-shadow", "warm", 2, 0, 0, 0, (2, 2, 0, 2, 0, 2, 2, 2)),
    Recipe("warm-red-dark", "warm", 2, 0, 0, 0, (2, 0, 2, 0, 0, 2, 0, 2)),
    Recipe("warm-red-deep", "warm", 2, 0, 6, 2),
    # Cool (sky / water / blue): white-cyan and cyan-blue are separate ramps.
    Recipe("cool-cyan-highlight", "cool", 7, 5, 1, 3),
    # Reference white-cyan 6:2 pattern.
    Recipe("cool-cyan-light", "cool", 7, 5, 0, 0, (7, 7, 5, 7, 5, 7, 7, 7)),
    Recipe("cool-cyan-pale", "cool", 7, 5, 3, 1),
    # Reference cyan-blue 6:2 pattern.
    Recipe("cool-cyan-blue-3", "cool", 5, 1, 0, 0, (5, 5, 1, 5, 1, 5, 5, 5)),
    Recipe("cool-cyan-blue-6", "cool", 5, 1, 3, 2),
    Recipe("cool-cyan-blue-9", "cool", 5, 1, 5, 0),
    Recipe("cool-blue-shadow", "cool", 1, 0, 2, 1),
    # Reference blue-black checkerboard.
    Recipe("cool-blue-dark", "cool", 1, 0, 0, 0, (1, 0, 1, 0, 0, 1, 0, 1)),
    Recipe("cool-blue-deep", "cool", 1, 0, 6, 4),
    # Magenta must be its own hue family. Treating it as warm red was the
    # reason violet/purple source colours collapsed into red or blue.
    Recipe("magenta-pure", "magenta", 3, 3, 0, 0),
    Recipe("magenta-pink", "magenta", 7, 3, 2, 1),
    Recipe("magenta-red", "magenta", 3, 2, 3, 3),
    Recipe("purple-light", "magenta", 3, 1, 2, 4),
    Recipe("purple-mid", "magenta", 3, 1, 4, 1),
    Recipe("purple-deep", "magenta", 3, 0, 4, 2),
    # Exact primaries stop fully saturated source colours from being forced
    # into a dithered neighbour merely because every other recipe is mixed.
    Recipe("red-pure", "warm", 2, 2, 0, 0),
    Recipe("yellow-pure", "warm", 6, 6, 0, 0),
    Recipe("blue-pure", "cool", 1, 1, 0, 0),
    Recipe("cyan-pure", "cool", 5, 5, 0, 0),
    Recipe("green-pure", "green", 4, 4, 0, 0),
    # Green (foliage / green clothing).
    Recipe("green-highlight", "green", 7, 4, 2, 2),
    Recipe("green-light", "green", 7, 4, 3, 0),
    Recipe("green-cyan-light", "green", 4, 5, 2, 3),
    Recipe("green-cyan-mid", "green", 4, 5, 4, 1),
    Recipe("green-shadow", "green", 4, 0, 2, 4),
    Recipe("green-dark", "green", 4, 0, 5, 2),
    # Teal (blue-green) has to be separate from both green and blue/cyan.
    # It is common in shaded backgrounds, where cyan plus black is especially
    # important; otherwise it collapses to an unrelated pure blue or green.
    Recipe("teal-green", "teal", 4, 5, 2, 2),
    Recipe("teal-mid", "teal", 4, 5, 4, 1),
    Recipe("teal-cyan", "teal", 4, 5, 6, 3),
    Recipe("teal-cyan-pure", "teal", 5, 5, 0, 0),
    Recipe("teal-dark", "teal", 5, 0, 4, 4),
    Recipe("teal-highlight-1", "teal", 7, 5, 1, 0),
    Recipe("teal-highlight-2", "teal", 7, 5, 2, 1),
    Recipe("teal-highlight-3", "teal", 7, 5, 3, 2),
)


def fine_ramp(name: str, family: str, a: int, b: int, coverages: Iterable[int] = range(1, 16)) -> tuple[Recipe, ...]:
    """Build a 4 x 4, sixteen-step ramp between two PC-8801 colours."""
    return tuple(
        Recipe(
            f"{name}-fine-{coverage:02d}",
            family,
            a,
            b,
            coverage,
            coverage % len(TILES_44),
            tile_shape=(4, 4),
            denominator=16,
            fine_pattern=True,
        )
        for coverage in coverages
    )


# 4x4 is a density mechanism, never a hue-specific effect.  Build a full
# sixteen-step ramp for every two-colour pair used by the historical recipes:
# white/red skin, yellow/white skin, cyan/blue, green/black, and so on.
def all_fine_ramps() -> tuple[Recipe, ...]:
    ramps: list[Recipe] = []
    seen: set[tuple[str, int, int]] = set()
    for base in BASE_RECIPES:
        if base.a == base.b:
            continue  # A solid PC-8801 colour has no density to interpolate.
        key = (base.family, base.a, base.b)
        if key in seen:
            continue
        seen.add(key)
        ramps.extend(fine_ramp(base.name, base.family, base.a, base.b))
    return tuple(ramps)


# These fine ramps are shared by both modes.  Normal Mix still favours the
# 4x2 historical tiles, whereas gradient mode fully uses 1/16 increments.
RECIPES = BASE_RECIPES + all_fine_ramps()


def normalise_pattern_mode(pattern_mode: str | bool = "normal-mix", gradient_mode: bool | None = None) -> str:
    """Validate modes and accept the previous boolean API as a compatibility shim."""
    if isinstance(pattern_mode, bool):
        pattern_mode = "gradient" if pattern_mode else "normal-mix"
    if gradient_mode:
        pattern_mode = "gradient"
    if pattern_mode not in PATTERN_MODES:
        raise ValueError(f"unknown pattern mode: {pattern_mode}")
    return pattern_mode


def choose_recipes(
    image: np.ndarray,
    yellow_bias: float = 0.0,
    pattern_mode: str | bool = "normal-mix",
    gradient_mode: bool | None = None,
) -> np.ndarray:
    """Choose a logical recipe for each source pixel, within its hue family."""
    h, w, _ = image.shape
    pattern_mode = normalise_pattern_mode(pattern_mode, gradient_mode)
    result = np.zeros((h, w), dtype=np.uint16)
    targets = np.array([r.target for r in RECIPES], dtype=np.float32)
    families = [r.family for r in RECIPES]
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

    chromatic_floor = 0.015 if pattern_mode == "gradient" else 0.07
    masks = {
        "dark": maximum < 56,
        # A 0.16 cut-off incorrectly classified pale peach/yellow skin as
        # neutral white.  Only genuinely near-neutral light colours are paper.
        "neutral": (maximum >= 56) & (saturation < chromatic_floor) & (maximum < 224),
        "light": (maximum >= 56) & (saturation < chromatic_floor) & (maximum >= 224),
        # Keep muted yellow-green/olive interiors out of the skin ramp.  Pale
        # skin is typically red-dominant and sits below ~65 degrees; 75 made
        # ceiling greys such as (153,157,139) turn into white-red patterns.
        "warm": (saturation >= chromatic_floor) & ((hue < 65) | (hue >= 345)),
        "green": (saturation >= chromatic_floor) & (hue >= 65) & (hue < 145),
        "teal": (saturation >= chromatic_floor) & (hue >= 145) & (hue < 195),
        "cool": (saturation >= chromatic_floor) & (hue >= 195) & (hue < 265),
        "magenta": (saturation >= chromatic_floor) & (hue >= 265) & (hue < 345),
    }
    for family, mask in masks.items():
        allowed_list = [i for i, candidate in enumerate(families) if candidate == family]
        if pattern_mode == "normal-42":
            allowed_list = [i for i in allowed_list if not RECIPES[i].fine_pattern]
        elif pattern_mode == "normal-44":
            # Solid colours remain valid; every mixed colour must use a 4x4
            # recipe, irrespective of whether it is skin, blue, green, etc.
            allowed_list = [i for i in allowed_list if RECIPES[i].fine_pattern or RECIPES[i].a == RECIPES[i].b]
        allowed = np.asarray(allowed_list, dtype=np.uint16)
        if not mask.any():
            continue
        source = rgb[mask]
        # Squared RGB distance is sufficient after the family restriction.
        delta = source[:, None, :] - targets[allowed][None, :, :]
        scores = np.sum(delta * delta, axis=2)
        fine_recipes = np.array([RECIPES[i].fine_pattern for i in allowed])
        if pattern_mode == "normal-mix" and fine_recipes.any():
            # Preserve the bolder 4x2 vocabulary for ordinary anime/cell
            # shading.  The small cost is overcome only when a 4x4 recipe is
            # materially closer in colour; in gradient mode there is no cost.
            scores[:, fine_recipes] += 260
        elif pattern_mode == "gradient" and fine_recipes.any():
            # At exact 1/8 positions the 4x2 and 4x4 targets can coincide.
            # Prefer the 4x4 candidate in gradient mode so the surrounding
            # 1/16 steps keep one coherent dither vocabulary.
            scores[:, fine_recipes] -= 0.25
        if family == "warm" and yellow_bias:
            # Bright skin is naturally closest to white-red.  This adjustable
            # reward admits the characteristic PC-8801 white-yellow and
            # red-yellow tiles without allowing cool colours into skin.
            yellow_recipes = np.array(
                [
                    recipe.a == 6 or recipe.b == 6 or (recipe.fixed_tile is not None and 6 in recipe.fixed_tile)
                    for recipe in (RECIPES[i] for i in allowed)
                ]
            )
            scores[:, yellow_recipes] -= yellow_bias * 16000
        if family == "warm":
            source_maximum = source.max(axis=1)
            source_minimum = source.min(axis=1)
            source_saturation = np.divide(source_maximum - source_minimum, source_maximum, out=np.zeros_like(source_maximum), where=source_maximum > 0)
            # Pale, red >= green >= blue source pixels are the usual skin/highlight
            # zone. Give the historical yellow-white skin tile a small default
            # preference so it wins before an almost-white area turns into paper.
            pale_skin = (
                (source[:, 0] >= source[:, 1])
                & (source[:, 1] >= source[:, 2])
                & (source_maximum >= 205)
                & (source_saturation >= 0.07)
                & (source_saturation <= 0.35)
            )
            yellow_recipes = np.array(
                [
                    recipe.a == 6 or recipe.b == 6 or (recipe.fixed_tile is not None and 6 in recipe.fixed_tile)
                    for recipe in (RECIPES[i] for i in allowed)
                ]
            )
            scores[:, yellow_recipes] -= pale_skin[:, None] * 1400
        result[mask] = allowed[np.argmin(scores, axis=1)]
    return result


def line_mask(image: np.ndarray, threshold: int, edge_strength: float = 0.0) -> np.ndarray:
    """Extract ink, optionally including dark anti-aliased/coloured contours."""
    value = image.max(axis=2)
    chroma = image.max(axis=2).astype(np.int16) - image.min(axis=2).astype(np.int16)
    # Black / near-black only.  A coloured dark blue remains a blue recipe.
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
    # Only a pixel that is clearly darker than its immediate surroundings is
    # promoted to ink. Interior portions of a dark hair/clothing area remain
    # available for pattern rendering.
    contrast_required = 72 - 36 * edge_strength
    contour = (luma < 170) & ((neighbour_mean - luma) >= contrast_required)
    return near_black | contour


def adjust_colours(
    image: np.ndarray,
    brightness: float,
    saturation: float,
    red_gain: float = 1.0,
    green_gain: float = 1.0,
    blue_gain: float = 1.0,
    contrast: float = 1.0,
) -> np.ndarray:
    """Apply the GUI colour controls before palette quantisation.

    RGB gains are applied first, followed by saturation around Rec.709 luma,
    contrast around the RGB midpoint, and finally the brightness multiplier.
    The GUI preview calls this same function so its colours match conversion.
    """
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


def render(recipe_ids: np.ndarray, ink: np.ndarray, pattern_mode: str | bool = "normal-mix") -> np.ndarray:
    pattern_mode = normalise_pattern_mode(pattern_mode)
    h, w = recipe_ids.shape
    yy, xx = np.indices((h, w))
    output = np.empty((h, w), dtype=np.uint8)
    for index, recipe in enumerate(RECIPES):
        selected = recipe_ids == index
        if recipe.fixed_tile is not None:
            tile = np.asarray(recipe.fixed_tile, dtype=np.uint8).reshape(recipe.tile_shape)
            output[selected] = tile[yy[selected] % recipe.tile_shape[0], xx[selected] % recipe.tile_shape[1]]
        else:
            use_44 = recipe.tile_shape == (4, 4) or pattern_mode == "normal-44"
            tile_shape = (4, 4) if use_44 else recipe.tile_shape
            tile = TILES_44[recipe.tile % len(TILES_44)] if use_44 else TILES[recipe.tile]
            coverage = round(recipe.coverage * 16 / recipe.denominator) if use_44 else recipe.coverage
            b_cells = tile[yy % tile_shape[0], xx % tile_shape[1]] < coverage
            output[selected] = np.where(b_cells[selected], recipe.b, recipe.a)
    output[ink] = 0
    return output


def paletted_image(indices: np.ndarray) -> Image.Image:
    image = Image.fromarray(indices, mode="P")
    flat_palette = PALETTE.flatten().tolist() + [0] * (256 * 3 - 24)
    image.putpalette(flat_palette)
    return image


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
    red_gain: float = 1.0,
    green_gain: float = 1.0,
    blue_gain: float = 1.0,
    contrast: float = 1.0,
    resize_640: bool = False,
) -> None:
    pattern_mode = normalise_pattern_mode(pattern_mode, gradient_mode)
    original = Image.open(source).convert("RGB")
    original = resize_input(original, resize_640)
    output_size = original.size
    if pc8801_200:
        # Work at half vertical resolution, but retain the source width and
        # final dimensions.  The final nearest-neighbour vertical expansion
        # duplicates every logical scanline.
        logical_height = (original.height + 1) // 2
        original = original.resize((original.width, logical_height), Image.Resampling.LANCZOS)
    if pixel_size > 1:
        grid = original.resize(
            (max(1, original.width // pixel_size), max(1, original.height // pixel_size)), Image.Resampling.LANCZOS
        )
    else:
        grid = original
    # This keeps drawn boundaries while eliminating isolated anti-alias pixels.
    smoothed = grid.filter(ImageFilter.MedianFilter(size=3))
    pixels = adjust_colours(
        np.asarray(smoothed, dtype=np.uint8), brightness, saturation,
        red_gain, green_gain, blue_gain, contrast,
    )
    # Extract ink before smoothing: a one-pixel hand-drawn line must not be
    # removed merely because it is surrounded by a flat skin or clothing area.
    original_pixels = np.asarray(grid, dtype=np.uint8)
    indices = render(choose_recipes(pixels, yellow_bias, pattern_mode), line_mask(original_pixels, line_threshold, edge_strength), pattern_mode)
    result = paletted_image(indices)
    if pixel_size > 1:
        result = result.resize(original.size, Image.Resampling.NEAREST)
    if pc8801_200:
        doubled_height = original.height * 2
        result = result.resize((output_size[0], doubled_height), Image.Resampling.NEAREST)
        # For an odd-height input, retain its exact height by dropping the
        # final extra duplicate; all complete scanline pairs still match.
        if result.height != output_size[1]:
            result = result.crop((0, 0, output_size[0], output_size[1]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(destination, optimize=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert an illustration to deterministic PC-8801 digital 8 colours.")
    parser.add_argument("input", type=Path, help="source illustration")
    parser.add_argument("output", type=Path, help="8-colour indexed PNG")
    parser.add_argument("--pixel-size", type=int, default=1, help="virtual-pixel size before nearest-neighbour enlargement")
    parser.add_argument("--line-threshold", type=int, default=58, help="0-255 threshold for near-black line extraction")
    parser.add_argument("--brightness", type=float, default=1.0, help="pre-quantisation brightness, 0.5..1.5")
    parser.add_argument("--saturation", type=float, default=1.0, help="pre-quantisation saturation, 0.0..2.0")
    parser.add_argument("--red", type=float, default=1.0, help="red channel multiplier, 0.5..1.5")
    parser.add_argument("--green", type=float, default=1.0, help="green channel multiplier, 0.5..1.5")
    parser.add_argument("--blue", type=float, default=1.0, help="blue channel multiplier, 0.5..1.5")
    parser.add_argument("--contrast", type=float, default=1.0, help="pre-quantisation contrast, 0.5..1.5")
    parser.add_argument("--resize-640", action="store_true", help="downscale sources wider than 640 pixels, preserving aspect ratio")
    parser.add_argument("--yellow-bias", type=float, default=0.0, help="prefer yellow-containing warm tiles, 0.0..1.0")
    parser.add_argument("--edge-ink", type=float, default=0.0, help="promote dark contrast edges to ink, 0.0..1.0")
    parser.add_argument("--pc8801-200", action="store_true", help="convert at half input height, then duplicate each scanline to the original size")
    parser.add_argument("--pattern-mode", choices=PATTERN_MODES, default="normal-mix", help="normal-42, normal-44, normal-mix, or gradient")
    parser.add_argument("--gradient-mode", action="store_true", help="legacy alias for --pattern-mode gradient")
    args = parser.parse_args()
    colour_controls = (args.red, args.green, args.blue, args.contrast)
    if args.pixel_size < 1 or not 0 <= args.line_threshold <= 255 or not 0.5 <= args.brightness <= 1.5 or not 0 <= args.saturation <= 2 or not all(0.5 <= value <= 1.5 for value in colour_controls) or not 0 <= args.yellow_bias <= 1 or not 0 <= args.edge_ink <= 1:
        parser.error("pixel size >= 1; line threshold 0..255; RGB/brightness/contrast 0.5..1.5; saturation 0..2; yellow bias 0..1; edge ink 0..1")
    convert(
        args.input, args.output, args.pixel_size, args.line_threshold,
        args.brightness, args.saturation, args.yellow_bias, args.edge_ink,
        args.pc8801_200, args.pattern_mode, args.gradient_mode,
        args.red, args.green, args.blue, args.contrast, args.resize_640,
    )


if __name__ == "__main__":
    main()
