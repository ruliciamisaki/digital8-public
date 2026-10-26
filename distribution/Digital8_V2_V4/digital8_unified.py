#!/usr/bin/env python3
"""One PC-8801/PC-9801 converter for the adopted V2 and V4 methods."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parent
# The source checkout keeps each machine's history in its own directory.  A
# standalone distribution has these uniquely named modules beside this file.
for directory in (ROOT / "PC-8801", ROOT / "PC-9801"):
    if directory.is_dir() and str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import pc8801_digital8_v1 as pc88_base  # noqa: E402
import pc8801_digital8_v2 as pc88_frequent  # noqa: E402
import pc8801_digital8_v4 as pc88_two_colour  # noqa: E402
import pc9801_16_v1 as pc98_base  # noqa: E402
import pc9801_16_v2 as pc98_frequent  # noqa: E402
import pc9801_16_v4 as pc98_two_colour  # noqa: E402


MACHINES = ("pc8801", "pc9801")
METHODS = ("frequent", "two_colour")
PC98_CODES = (
    "Dovema", "Doveya", "Suiral", "Sudara", "Dukena", "Dukeri", "Silmor",
    "Sillun", "Silven", "Dukesu", "Camuri", "Camura", "Dukino", "Nuvora",
)


@dataclass(frozen=True)
class PaletteSpec:
    machine: str
    id: str
    label: str
    colours: tuple[tuple[int, int, int], ...]

    def array(self) -> np.ndarray:
        return np.asarray(self.colours, dtype=np.uint8)


# RGB on PC-8801mkIISR uses 3 bits per channel.  Dinsor's supplied colours are
# rounded to the nearest of the eight display levels (round(n * 255 / 7)).
# Rendering receives the selected RGB table independently of scanline mode and
# pattern method, so future analog palettes can be registered here as well.
PC88_PALETTES = (
    PaletteSpec(
        "pc8801", "digital8", "01  Digita",
        tuple(tuple(int(value) for value in colour) for colour in pc88_base.PALETTE),
    ),
    PaletteSpec(
        "pc8801", "dinsor", "02  Dinsor",
        (
            (0, 0, 0), (0, 0, 219), (219, 0, 0), (0, 109, 0),
            (0, 182, 0), (0, 182, 219), (255, 219, 0), (255, 255, 255),
        ),
    ),
)


def palettes_for(machine: str) -> tuple[PaletteSpec, ...]:
    if machine == "pc8801":
        return PC88_PALETTES
    if machine != "pc9801":
        raise ValueError(f"unknown machine: {machine}")
    reference = pc98_two_colour.load_reference()
    regular = [item for item in reference["palettes"] if item["id"] != "digital8_half"]
    if len(regular) != len(PC98_CODES):
        raise ValueError("PC-9801 palette names do not match the palette data")
    specs = [
        PaletteSpec(
            machine, item["id"], f"{index:02d}  {PC98_CODES[index - 1]}",
            tuple(tuple(int(value) for value in colour) for colour in item["colours"]),
        )
        for index, item in enumerate(regular, 1)
    ]
    digital = next(item for item in reference["palettes"] if item["id"] == "digital8_half")
    specs.append(PaletteSpec(
        machine, digital["id"], "    Dighaf",
        tuple(tuple(int(value) for value in colour) for colour in digital["colours"]),
    ))
    return tuple(specs)


def palette_for(machine: str, palette_id: str) -> PaletteSpec:
    for palette in palettes_for(machine):
        if palette.id == palette_id:
            return palette
    raise ValueError(f"palette {palette_id!r} is unavailable for {machine}")


@dataclass(frozen=True)
class ConversionOptions:
    machine: str = "pc8801"
    method: str = "two_colour"
    palette_id: str = "digital8"
    tall_dots: bool = True
    pixel_size: int = 1
    line_threshold: int = 72
    brightness: float = 1.0
    saturation: float = 1.0
    red_gain: float = 1.0
    green_gain: float = 1.0
    blue_gain: float = 1.0
    contrast: float = 1.0
    edge_strength: float = 0.0
    resize_640: bool = False
    alpha_mode: str = "preserve"
    alpha_threshold: int = 128
    frequent_mode: str = "normal-mix"
    colour_coherence: int = 12
    fill_stability: float = 0.0
    half_tone: str = "checker"
    quarter_tone: str = "quarter_staggered"
    colour_groups: int = 48


def pc8801_standard() -> ConversionOptions:
    return ConversionOptions(resize_640=True)


def pc9801_standard() -> ConversionOptions:
    return ConversionOptions(
        machine="pc9801", method="frequent", palette_id="palette_01",
        tall_dots=False, resize_640=True, line_threshold=58, frequent_mode="gradient",
        colour_coherence=10, fill_stability=0.20,
    )


def _paletted(indices: np.ndarray, palette: np.ndarray) -> Image.Image:
    result = Image.fromarray(indices.astype(np.uint8), "P")
    result.putpalette(palette.flatten().tolist() + [0] * (768 - len(palette) * 3))
    return result


def _resize_source(image: Image.Image, enabled: bool) -> Image.Image:
    if not enabled or image.width == 640:
        return image
    height = max(1, round(image.height * 640 / image.width))
    return image.resize((640, height), Image.Resampling.LANCZOS)


def _pc88_frequent(
    pixels: np.ndarray, ink: np.ndarray, palette: np.ndarray, settings: ConversionOptions,
) -> np.ndarray:
    mode = pc88_base.normalise_pattern_mode(settings.frequent_mode)
    recipes = pc88_frequent.build_recipes(mode, pc88_frequent.load_reference())
    recipes = tuple(replace(recipe, target=tuple(float(value) for value in
                             palette[np.asarray(recipe.tile)].mean(axis=0))) for recipe in recipes)
    chosen = pc88_frequent.choose_recipes(
        pixels, recipes, 0.0, mode, settings.colour_coherence,
    )
    indices = pc88_frequent.render(chosen, ink, recipes)
    return indices


def _pc98_frequent(
    pixels: np.ndarray, ink: np.ndarray, palette: np.ndarray, settings: ConversionOptions,
) -> np.ndarray:
    mode = settings.frequent_mode
    reference = pc98_frequent.load_reference()
    rgb = pc98_frequent.coherent_colours(pixels, settings.colour_coherence)
    a, b, coverage, targets = pc98_base.build_candidates(palette, mode)
    chosen = pc98_base.choose_candidates(rgb, targets)
    chosen = pc98_base.stabilise_fill_regions(chosen, rgb, settings.fill_stability)
    chosen_a, chosen_b, chosen_coverage = a[chosen], b[chosen], coverage[chosen]
    indices = chosen_a.copy()
    yy, xx = np.indices(chosen.shape)
    masks = (
        {density: pc98_base.bayer_mask(density) for density in range(1, 16)}
        if mode == "gradient" else pc98_frequent.masks_by_coverage(reference)
    )
    for density, mask in masks.items():
        selected = chosen_coverage == density
        choose_b = mask[yy % 4, xx % 4]
        indices[selected & choose_b] = chosen_b[selected & choose_b]
    return indices


def _pc88_two_colour(
    pixels: np.ndarray, ink: np.ndarray, palette: np.ndarray, settings: ConversionOptions,
) -> np.ndarray:
    recipes = pc88_two_colour.build_recipes(settings.half_tone, settings.quarter_tone)
    recipes = tuple(replace(recipe, target=tuple(float(value) for value in
                             palette[np.asarray(recipe.tile)].mean(axis=0))) for recipe in recipes)
    chosen = pc88_two_colour.choose_recipes(pixels, ink, recipes, settings.colour_groups)
    return pc88_two_colour.render(chosen, ink, recipes)


def _pc98_two_colour(
    pixels: np.ndarray, ink: np.ndarray, palette: np.ndarray, settings: ConversionOptions,
) -> np.ndarray:
    recipes = pc98_two_colour.build_recipes(palette, settings.half_tone, settings.quarter_tone)
    chosen = pc98_two_colour.choose_recipes(pixels, ink, recipes, settings.colour_groups)
    darkest = int(np.argmin(palette.astype(np.float32) @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)))
    return pc98_two_colour.render(chosen, ink, recipes, darkest)


def convert_image(
    source: Path,
    settings: ConversionOptions = ConversionOptions(),
) -> tuple[Image.Image, Image.Image | None]:
    """Run either adopted method without writing the PNG or alpha mask."""
    if settings.machine not in MACHINES or settings.method not in METHODS:
        raise ValueError("unknown machine or method")
    if settings.pixel_size < 1 or not 0 <= settings.alpha_threshold <= 255:
        raise ValueError("pixel size >= 1; alpha threshold 0..255")
    palette = palette_for(settings.machine, settings.palette_id).array()
    original, alpha = pc98_base.load_source_with_alpha(Path(source))
    original = _resize_source(original, settings.resize_640)
    if alpha is not None:
        alpha = _resize_source(alpha, settings.resize_640)
    output_size = original.size
    if settings.tall_dots:
        original = original.resize((original.width, (original.height + 1) // 2), Image.Resampling.LANCZOS)
    if settings.pixel_size > 1:
        grid = original.resize((max(1, original.width // settings.pixel_size),
                                max(1, original.height // settings.pixel_size)), Image.Resampling.LANCZOS)
    else:
        grid = original
    raw = np.asarray(grid, dtype=np.uint8)
    smoothed = np.asarray(grid.filter(ImageFilter.MedianFilter(size=3)), dtype=np.uint8)
    pixels = pc98_base.adjust_colours(
        smoothed, settings.brightness, settings.saturation,
        settings.red_gain, settings.green_gain, settings.blue_gain, settings.contrast,
    )
    if settings.method == "frequent":
        ink = (
            pc88_base.line_mask(raw, settings.line_threshold, settings.edge_strength)
            if settings.machine == "pc8801" else
            pc98_base.line_mask(raw, settings.line_threshold, settings.edge_strength)
        )
        indices = (
            _pc88_frequent(pixels, ink, palette, settings)
            if settings.machine == "pc8801" else
            _pc98_frequent(pixels, ink, palette, settings)
        )
    else:
        ink = (
            pc88_two_colour.source_ink(raw, settings.line_threshold, settings.edge_strength)
            if settings.machine == "pc8801" else
            pc98_two_colour.source_ink(raw, settings.line_threshold, settings.edge_strength)
        )
        indices = (
            _pc88_two_colour(pixels, ink, palette, settings)
            if settings.machine == "pc8801" else
            _pc98_two_colour(pixels, ink, palette, settings)
        )
    darkest = int(np.argmin(palette.astype(np.float32) @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)))
    indices[ink] = darkest
    result = _paletted(indices, palette)
    if settings.pixel_size > 1:
        result = result.resize(original.size, Image.Resampling.NEAREST)
    if settings.tall_dots:
        result = result.resize((output_size[0], original.height * 2), Image.Resampling.NEAREST)
        if result.height != output_size[1]:
            result = result.crop((0, 0, output_size[0], output_size[1]))
    alpha = pc98_two_colour.prepare_output_alpha(
        alpha, settings.alpha_mode, settings.alpha_threshold, settings.tall_dots, output_size,
    )
    return pc98_base.preserve_alpha(result, alpha), alpha


def convert(
    source: Path,
    destination: Path,
    settings: ConversionOptions = ConversionOptions(),
    alpha_mask: Path | None = None,
    invert_alpha_mask: bool = False,
) -> None:
    result, alpha = convert_image(source, settings)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(destination, optimize=False)
    if alpha_mask is not None:
        alpha_mask = Path(alpha_mask)
        alpha_mask.parent.mkdir(parents=True, exist_ok=True)
        mask_alpha = alpha if alpha is not None else Image.new("L", result.size, 255)
        pc98_base.alpha_mask_image(mask_alpha, invert_alpha_mask).save(alpha_mask)


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified PC-8801/PC-9801 V2/V4 converter")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--preset", choices=("pc8801", "pc9801"), default="pc8801")
    parser.add_argument("--machine", choices=MACHINES)
    parser.add_argument("--method", choices=METHODS)
    parser.add_argument("--palette")
    parser.add_argument("--tall-dots", action=argparse.BooleanOptionalAction)
    parser.add_argument("--frequent-mode")
    parser.add_argument("--half-tone", choices=pc88_two_colour.HALF_TONES)
    parser.add_argument("--quarter-tone", choices=pc88_two_colour.QUARTER_TONES)
    parser.add_argument("--colour-groups", type=int)
    parser.add_argument("--colour-coherence", type=int)
    parser.add_argument("--fill-stability", type=float)
    parser.add_argument("--pixel-size", type=int)
    parser.add_argument("--line-threshold", type=int)
    parser.add_argument("--resize-640", action=argparse.BooleanOptionalAction)
    parser.add_argument("--alpha-mode", choices=pc98_base.ALPHA_MODES)
    parser.add_argument("--alpha-threshold", type=int)
    parser.add_argument("--alpha-mask", type=Path)
    args = parser.parse_args()
    settings = pc8801_standard() if args.preset == "pc8801" else pc9801_standard()
    overrides = {
        key: value for key, value in {
            "machine": args.machine, "method": args.method, "palette_id": args.palette,
            "tall_dots": args.tall_dots, "frequent_mode": args.frequent_mode,
            "half_tone": args.half_tone, "quarter_tone": args.quarter_tone,
            "colour_groups": args.colour_groups, "colour_coherence": args.colour_coherence,
            "fill_stability": args.fill_stability, "pixel_size": args.pixel_size,
            "line_threshold": args.line_threshold, "alpha_mode": args.alpha_mode,
            "alpha_threshold": args.alpha_threshold,
        }.items() if value is not None
    }
    if args.resize_640 is not None:
        overrides["resize_640"] = args.resize_640
    settings = replace(settings, **overrides)
    convert(args.input, args.output, settings, args.alpha_mask)


if __name__ == "__main__":
    main()
