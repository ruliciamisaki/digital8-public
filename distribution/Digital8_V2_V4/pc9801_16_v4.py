#!/usr/bin/env python3
"""PC-9801 V4: coherent fills with six fixed two-colour tile patterns."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import pc9801_16_v1 as legacy
import pc9801_16_v2 as v2


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


def extra_palette_path() -> Path:
    return Path(__file__).with_name("pc9801_palette_v4.json")


def load_reference(path: Path | None = None) -> dict:
    """Retain V2 palette data, add V4 colours, and put half-bright digital 8 last."""
    data = v2.load_reference(path)
    added = json.loads(extra_palette_path().read_text(encoding="utf-8"))
    colours = added["colours"]
    if len(colours) != 16 or any(
        len(rgb) != 3 or any(not 0 <= value <= 255 or value % 17 for value in rgb)
        for rgb in colours
    ):
        raise ValueError("V4 palette must contain sixteen 4-bit/channel RGB colours")
    existing = data["palettes"]
    if any(item["id"] == added["id"] for item in existing):
        raise ValueError(f"duplicate palette id: {added['id']}")
    digital = [item for item in existing if item["id"] == "digital8_half"]
    if len(digital) != 1:
        raise ValueError("V2 reference must contain digital8_half exactly once")
    ordered = [item for item in existing if item["id"] != "digital8_half"]
    ordered.append({"id": added["id"], "colours": colours, "sources": ["user-supplied V4 palette"]})
    ordered.extend(digital)
    return {**data, "version": 4, "palettes": ordered}


def build_recipes(
    palette: np.ndarray,
    half_tone: str = "checker",
    quarter_tone: str = "quarter_staggered",
) -> tuple[TileRecipe, ...]:
    if half_tone not in HALF_TONES or quarter_tone not in QUARTER_TONES:
        raise ValueError("invalid half-tone or quarter-tone preference")
    if palette.ndim != 2 or palette.shape[1] != 3 or len(palette) > 256:
        raise ValueError("palette must be Nx3 RGB")
    colours = palette.astype(np.float32)
    brightness = colours @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    unique_indices: list[int] = []
    seen: set[tuple[int, int, int]] = set()
    for index, colour in enumerate(palette):
        key = tuple(int(value) for value in colour)
        if key not in seen:
            seen.add(key)
            unique_indices.append(index)
    recipes: list[TileRecipe] = []
    for a in unique_indices:
        for b in unique_indices:
            names = ("solid",) if a == b else tuple(name for name in TEMPLATES if name != "solid")
            for name in names:
                if name in HALF_TONES and brightness[a] < brightness[b]:
                    continue
                rows = TEMPLATES[name]
                tile = tuple(a if char == "A" else b for row in rows for char in row)
                target = tuple(float(value) for value in colours[np.asarray(tile)].mean(axis=0))
                penalty = 0.0 if a == b else float(np.sum((colours[a] - colours[b]) ** 2)) * 0.002
                if name in HALF_TONES and name != half_tone:
                    penalty += 500.0
                if name in QUARTER_TONES and name != quarter_tone:
                    penalty += 500.0
                recipes.append(TileRecipe(name, a, b, tile, (len(rows), len(rows[0])), target, penalty))
    return tuple(recipes)


def source_ink(image: np.ndarray, threshold: int, edge_strength: float = 0.0) -> np.ndarray:
    if not 0 <= threshold <= 120:
        raise ValueError("black threshold must be 0..120")
    raw = image.astype(np.float32)
    maximum = raw.max(axis=2)
    luma = raw @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    near_black = (maximum <= threshold) | ((luma <= threshold * 0.65) & (maximum <= threshold * 1.5))
    return near_black | legacy.line_mask(image, threshold, edge_strength)


def prepare_output_alpha(
    alpha: Image.Image | None,
    mode: str,
    threshold: int,
    vertical_double: bool,
    output_size: tuple[int, int],
) -> Image.Image | None:
    """Match PC-8801 V4: preserve alpha or binarise at the logical height."""
    if alpha is None:
        return None
    if mode == "preserve":
        return alpha
    if mode != "binary":
        raise ValueError(f"unknown alpha mode: {mode}")
    if vertical_double:
        alpha = alpha.resize((output_size[0], (output_size[1] + 1) // 2), Image.Resampling.LANCZOS)
    values = np.where(np.asarray(alpha, dtype=np.uint8) >= threshold, 255, 0).astype(np.uint8)
    converted = Image.fromarray(values, mode="L")
    if vertical_double:
        converted = converted.resize((output_size[0], converted.height * 2), Image.Resampling.NEAREST)
        if converted.height != output_size[1]:
            converted = converted.crop((0, 0, output_size[0], output_size[1]))
    return converted


def choose_recipes(
    image: np.ndarray,
    ink: np.ndarray,
    recipes: tuple[TileRecipe, ...],
    colour_groups: int = 48,
) -> np.ndarray:
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
        for primary in range(3):
            others = [channel for channel in range(3) if channel != primary]
            if all(colour[primary] - colour[channel] >= 35 for channel in others):
                wrong_hue = np.any(targets[:, primary, None] <= targets[:, others] + 5, axis=1)
                scores[wrong_hue] += 9000.0
                break
        result[selected] = int(np.argmin(scores))
    return result


def render(
    recipe_ids: np.ndarray,
    ink: np.ndarray,
    recipes: tuple[TileRecipe, ...],
    ink_index: int,
) -> np.ndarray:
    height, width = recipe_ids.shape
    yy, xx = np.indices((height, width))
    output = np.zeros((height, width), dtype=np.uint8)
    for index in np.unique(recipe_ids[~ink]):
        selected = (recipe_ids == index) & ~ink
        recipe = recipes[int(index)]
        tile = np.asarray(recipe.tile, dtype=np.uint8).reshape(recipe.shape)
        output[selected] = tile[yy[selected] % recipe.shape[0], xx[selected] % recipe.shape[1]]
    output[ink] = ink_index
    return output


def convert_image(
    source: Path,
    palette_id: str = "palette_14",
    pixel_size: int = 1,
    line_threshold: int = 72,
    brightness: float = 1.0,
    saturation: float = 1.0,
    edge_strength: float = 0.0,
    vertical_double: bool = False,
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
    """Keep output size; vertical mode converts at half-height then duplicates rows."""
    if pixel_size < 1 or not 0 <= alpha_threshold <= 255:
        raise ValueError("pixel size >= 1; alpha threshold 0..255")
    palette = legacy.palette_by_id(load_reference(reference), palette_id)
    original, alpha = legacy.load_source_with_alpha(source)
    original = legacy.resize_input(original, resize_640)
    if alpha is not None:
        alpha = legacy.resize_input(alpha, resize_640)
    output_size = original.size
    if vertical_double:
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
    recipes = build_recipes(palette, half_tone, quarter_tone)
    recipe_ids = choose_recipes(pixels, ink, recipes, colour_groups)
    darkest = int(np.argmin(palette.astype(np.float32) @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)))
    indices = render(recipe_ids, ink, recipes, darkest)
    result = Image.fromarray(indices, "P")
    result.putpalette(palette.flatten().tolist() + [0] * (768 - len(palette) * 3))
    if pixel_size > 1:
        result = result.resize(original.size, Image.Resampling.NEAREST)
    if vertical_double:
        result = result.resize((output_size[0], original.height * 2), Image.Resampling.NEAREST)
        if result.height != output_size[1]:
            result = result.crop((0, 0, output_size[0], output_size[1]))
    alpha = prepare_output_alpha(alpha, alpha_mode, alpha_threshold, vertical_double, output_size)
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
    parser = argparse.ArgumentParser(description="PC-9801 V4 restricted two-colour tile converter")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--palette", choices=[item["id"] for item in load_reference()["palettes"]], default="palette_14")
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
    parser.add_argument("--vertical-double", action="store_true")
    parser.add_argument("--half-tone", choices=HALF_TONES, default="checker")
    parser.add_argument("--quarter-tone", choices=QUARTER_TONES, default="quarter_staggered")
    parser.add_argument("--colour-groups", type=int, default=48)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--alpha-mask", type=Path)
    parser.add_argument("--invert-alpha-mask", action="store_true")
    parser.add_argument("--alpha-mode", choices=legacy.ALPHA_MODES, default="preserve")
    parser.add_argument("--alpha-threshold", type=int, default=128)
    args = parser.parse_args()
    convert(args.input, args.output, palette_id=args.palette, pixel_size=args.pixel_size,
            line_threshold=args.line_threshold, brightness=args.brightness,
            saturation=args.saturation, red_gain=args.red, green_gain=args.green,
            blue_gain=args.blue, contrast=args.contrast, resize_640=args.resize_640,
            edge_strength=args.edge_ink, vertical_double=args.vertical_double,
            half_tone=args.half_tone, quarter_tone=args.quarter_tone,
            colour_groups=args.colour_groups, reference=args.reference,
            alpha_mask=args.alpha_mask, invert_alpha_mask=args.invert_alpha_mask,
            alpha_mode=args.alpha_mode, alpha_threshold=args.alpha_threshold)


if __name__ == "__main__":
    main()
