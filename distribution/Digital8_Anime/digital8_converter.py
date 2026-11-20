#!/usr/bin/env python3
"""Digital8 Anime V4 converter for PC-8801, PC-9801 and anime painting."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image


import anime_paint
import image_processing
import line_effects
import pc8801_digital8_v4 as pc88_two_colour
import pc9801_16_v4 as pc98_two_colour
import png_settings
import retro_global
import tile_patterns
from layer_export import PSD_LAYER_MODES, ConversionDocument, write_psd, write_zip

ROOT = Path(__file__).resolve().parent
MACHINES = ("pc8801", "pc9801", "anime")
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
        pc88_two_colour.PALETTE,
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
    if machine == "anime":
        return (
            PaletteSpec("anime", "te", "カラーTE", anime_paint.load_palette(ROOT / "palettes" / "colour_te.json")),
            PaletteSpec("anime", "te_skin_plus", "カラーTE肌+", anime_paint.load_palette(ROOT / "palettes" / "colour_te_skin_plus.json")),
            PaletteSpec("anime", "cw", "カラーCW", anime_paint.load_palette(ROOT / "palettes" / "colour_cw.json")),
            PaletteSpec("anime", "te_saturated", "カラーTE 彩度上げ", anime_paint.load_palette(ROOT / "palettes" / "colour_te_saturated.json")),
            PaletteSpec("anime", "te_skin_plus_saturated", "カラーTE肌+ 彩度上げ", anime_paint.load_palette(ROOT / "palettes" / "colour_te_skin_plus_saturated.json")),
            PaletteSpec("anime", "cw_saturated", "カラーCW 彩度上げ", anime_paint.load_palette(ROOT / "palettes" / "colour_cw_saturated.json")),
            PaletteSpec("anime", "cel120", "01  Cel120", anime_paint.default_palette()),
        )
    if machine == "pc8801":
        return PC88_PALETTES
    if machine != "pc9801":
        raise ValueError(f"unknown machine: {machine}")
    palettes = json.loads((ROOT / "pc9801_palettes.json").read_text(encoding="utf-8"))
    regular = [item for item in palettes if item["id"] != "digital8_half"]
    if len(regular) != len(PC98_CODES):
        raise ValueError("PC-9801 palette names do not match the palette data")
    specs = [
        PaletteSpec(
            machine, item["id"], f"{index:02d}  {PC98_CODES[index - 1]}",
            tuple(tuple(int(value) for value in colour) for colour in item["colours"]),
        )
        for index, item in enumerate(regular, 1)
    ]
    digital = next(item for item in palettes if item["id"] == "digital8_half")
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
    half_tone: str = "checker"
    quarter_tone: str = "quarter_staggered"
    colour_groups: int = 48
    anime_tolerance: float = 128.0
    anime_radius: int = 3
    anime_min_area: int = 12
    anime_palette: tuple[tuple[int, int, int], ...] | None = None
    anime_max_colours: int = 24
    line_growth: float = 0.0
    colour_trace: bool = False
    trace_strength: float = 0.55
    white_transparent: bool = False
    smooth_2x: bool = False
    eighth_tone: str = "eighth"
    three_112: str = "none"
    three_111: str = "none"
    retro_palette: tuple[tuple[int, int, int], ...] | None = None
    four_colour: bool = False
    five_colour: bool = False


def validate_retro_palette(machine: str, colours: object) -> tuple:
    counts = {"pc8801": 8, "pc9801": 16}
    if machine not in counts:
        raise ValueError("PC用パレットの機種が不正。")
    result = anime_paint.validate_palette(colours)
    if len(result) != counts[machine]:
        raise ValueError(f"{machine}のパレットは{counts[machine]}色で指定して。")
    return result


def resolved_palette(settings: ConversionOptions) -> tuple:
    if settings.machine == "anime" and settings.anime_palette is not None:
        return anime_paint.validate_palette(settings.anime_palette)
    if settings.machine != "anime" and settings.retro_palette is not None:
        return validate_retro_palette(settings.machine, settings.retro_palette)
    return palette_for(settings.machine, settings.palette_id).colours


def pc8801_standard() -> ConversionOptions:
    return ConversionOptions(resize_640=True, edge_strength=1.0)


def pc9801_standard() -> ConversionOptions:
    return ConversionOptions(
        machine="pc9801", palette_id="palette_01", tall_dots=False,
        resize_640=True, line_threshold=58, edge_strength=1.0,
    )


def anime_standard() -> ConversionOptions:
    return ConversionOptions(
        machine="anime", palette_id="te", tall_dots=False,
        resize_640=False, line_threshold=58, edge_strength=1.0,
        anime_tolerance=128.0,
    )


def _prepare_source(source: Path | Image.Image, settings: ConversionOptions):
    with (nullcontext(source) if isinstance(source, Image.Image) else Image.open(source)) as opened:
        has_alpha = "A" in opened.getbands() or "transparency" in opened.info
        rgba = _resize_source(opened.convert("RGBA"), settings.resize_640)
    output_size = rgba.size
    alpha = rgba.getchannel("A") if has_alpha else None
    alpha = image_processing.prepare_output_alpha(alpha, settings.alpha_mode, settings.alpha_threshold)
    if alpha is not None:
        rgba.putalpha(alpha)
    logical_size = (rgba.width, (rgba.height + 1) // 2) if settings.tall_dots else rgba.size
    if rgba.size != logical_size:
        rgba = rgba.resize(logical_size, Image.Resampling.LANCZOS)
    grid_size = (max(1, rgba.width // settings.pixel_size), max(1, rgba.height // settings.pixel_size))
    if grid_size != rgba.size:
        rgba = rgba.resize(grid_size, Image.Resampling.LANCZOS)
    # Pillow's RGBA resizing premultiplies alpha, avoiding hidden-RGB fringes.
    raw = np.asarray(rgba.convert("RGB"), dtype=np.uint8)
    coverage = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255
    pixels = image_processing.adjust_colours(
        raw, settings.brightness, settings.saturation,
        settings.red_gain, settings.green_gain, settings.blue_gain, settings.contrast,
    )
    ink = image_processing.source_ink(raw, settings.line_threshold, settings.edge_strength)
    return pixels, coverage, alpha, logical_size, output_size, ink


def _anime_reference(source, settings):
    pixels, coverage, _alpha, _logical, _output, ink = _prepare_source(source, settings)
    colours = resolved_palette(settings)
    return anime_paint.build_colour_reference(
        pixels, ink, coverage, np.asarray(colours, dtype=np.uint8), settings.anime_tolerance,
        settings.anime_max_colours, True,
    )


def _anime_image(source: Path | Image.Image, settings: ConversionOptions,
                 reference=None, spatial_scale: int = 1) -> ConversionDocument:
    colours = resolved_palette(settings)
    palette = np.asarray(colours, dtype=np.uint8)
    pixels, coverage, alpha, logical_size, output_size, ink = _prepare_source(source, settings)
    if reference is None:
        reference = anime_paint.build_colour_reference(
            pixels, ink, coverage, palette, settings.anime_tolerance, settings.anime_max_colours, True,
        )
    ink = line_effects.expand_ink(ink, settings.line_growth, coverage > 0)
    selected = []
    indices = anime_paint.paint(
        pixels, ink, palette, coverage, settings.anime_tolerance,
        settings.anime_radius, settings.anime_min_area, settings.anime_max_colours,
        True, selected, reference=reference, spatial_scale=spatial_scale,
    )
    layer_ids = indices.astype(np.int32).copy()
    ink_id = len(palette)
    layer_ids[ink] = ink_id
    names = {int(index): f"Fill {int(index) + 1:04d} #{palette[index, 0]:02X}{palette[index, 1]:02X}{palette[index, 2]:02X}"
             for index in np.unique(indices[~ink])}
    names[ink_id] = "Lines - colour trace" if settings.colour_trace else "Lines"
    if settings.colour_trace:
        indices = line_effects.trace_indices(indices, ink, palette, settings.trace_strength,
                                            visible=coverage > 0, permitted=selected[0],
                                            max_width=3 + 2 * int(settings.line_growth))
    indices, fringe = line_effects.fractional_growth(
        indices, ink, settings.line_growth, palette, selected[0], coverage > 0,
    )
    layer_ids[fringe] = ink_id
    image = Image.fromarray(palette[indices], "RGB")
    return _finish_document(image, layer_ids, names, alpha, logical_size, output_size, settings)


def _finish_document(image, ids, names, alpha, logical_size, output_size, settings, tile_colours=None):
    image = image.resize(logical_size, Image.Resampling.NEAREST)
    labels = Image.fromarray(ids, "I").resize(logical_size, Image.Resampling.NEAREST)
    if settings.tall_dots:
        repeated = (logical_size[0], logical_size[1] * 2)
        image = image.resize(repeated, Image.Resampling.NEAREST).crop((0, 0, *output_size))
        labels = labels.resize(repeated, Image.Resampling.NEAREST).crop((0, 0, *output_size))
    if settings.white_transparent:
        white = np.all(np.asarray(image.convert("RGB")) == 255, axis=2)
        values = np.full((image.height, image.width), 255, dtype=np.uint8) if alpha is None else np.array(alpha)
        values[white] = 0
        alpha = Image.fromarray(values, "L")
    return ConversionDocument(image_processing.preserve_alpha(image, alpha), alpha, np.asarray(labels),
                              names, tile_colours or {})


def _paletted(indices: np.ndarray, palette: np.ndarray) -> Image.Image:
    result = Image.fromarray(indices.astype(np.uint8), "P")
    result.putpalette(palette.flatten().tolist() + [0] * (768 - len(palette) * 3))
    return result


def _resize_source(image: Image.Image, enabled: bool) -> Image.Image:
    if not enabled or image.width == 640:
        return image
    height = max(1, round(image.height * 640 / image.width))
    return image.resize((640, height), Image.Resampling.LANCZOS)


def _retro_reference(pixels, ink, coverage, palette, settings):
    patterns = {
        "half_tone": settings.half_tone,
        "quarter_tone": settings.quarter_tone,
        "eighth_tone": settings.eighth_tone,
        "three_112": settings.three_112,
        "three_111": settings.three_111,
        "four_colour": settings.four_colour,
        "five_colour": settings.five_colour,
    }
    if settings.machine == "pc8801":
        recipes = pc88_two_colour.build_recipes(palette_override=palette, **patterns)
    else:
        recipes = pc98_two_colour.build_recipes(palette, **patterns)
    targets = np.asarray([recipe.target for recipe in recipes], dtype=np.float32)
    penalties = np.asarray([recipe.penalty for recipe in recipes])
    reference = retro_global.build_reference(pixels, ink, coverage, targets, penalties, settings)
    return recipes, targets, reference


def _retro_image(source, settings, reference=None, spatial_scale=1):
    palette = np.asarray(resolved_palette(settings), dtype=np.uint8)
    pixels, coverage, alpha, logical_size, output_size, ink = _prepare_source(source, settings)
    if reference is None:
        reference = _retro_reference(pixels, ink, coverage, palette, settings)
    recipes, targets, tones = reference
    visible = coverage > 0
    ink = line_effects.expand_ink(ink, settings.line_growth, visible)
    chosen = retro_global.choose(pixels, ink, coverage, tones, settings, spatial_scale)
    darkest = int(np.argmin(palette.astype(np.float32) @ np.asarray(image_processing.LUMA_WEIGHTS, dtype=np.float32)))
    indices = tile_patterns.render(chosen, ink, recipes, darkest)
    ids = chosen.astype(np.int32)
    names = {int(index): f"{settings.machine} {recipes[index].name} recipe {index}"
             if recipes[index].name in ('four_colour', 'five_colour')
             else f"{settings.machine} two_colour recipe {index}"
             for index in np.unique(ids[~ink])}
    ink_id = len(recipes)
    ids[ink] = ink_id
    names[ink_id] = "Lines - colour trace" if settings.colour_trace else "Lines"
    permitted = np.arange(len(palette))
    if settings.colour_trace:
        indices = line_effects.trace_indices(
            indices, ink, palette, settings.trace_strength, visible=visible, permitted=permitted,
            reference_ids=chosen, reference_colours=targets, max_width=3 + 2 * int(settings.line_growth),
        )
    indices, fringe = line_effects.fractional_growth(indices, ink, settings.line_growth, palette, permitted, visible)
    ids[fringe] = ink_id
    tile_colours = {}
    for index in names:
        if index == ink_id:
            continue
        recipe = recipes[index]
        dots = palette[list(recipe.tile)]
        if np.any(dots[1:] != dots[0]):
            tile_colours[index] = tuple(int(channel + 0.5) for channel in recipe.target)
    return _finish_document(_paletted(indices, palette), ids, names, alpha, logical_size, output_size,
                            settings, tile_colours)


def _validate_options(settings: ConversionOptions) -> None:
    if settings.machine not in MACHINES:
        raise ValueError("機種を選んでください。")
    for label, value, low, high in (
        ("仮想ピクセル", settings.pixel_size, 1, 4096),
        ("主線しきい値", settings.line_threshold, 0, 120),
        ("透明度のしきい値", settings.alpha_threshold, 0, 255),
        ("使用色数", settings.anime_max_colours, 0, 256),
    ):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{label}は{low}〜{high}の整数で指定してください。")
    if settings.machine != "anime" and (type(settings.colour_groups) is not int or not 2 <= settings.colour_groups <= 64):
        raise ValueError("共通色区分数は2〜64の整数で指定してください。")
    if not 0 <= settings.edge_strength <= 1 or not .1 <= settings.trace_strength <= .95:
        raise ValueError("輪郭抽出は0〜1、色トレス明度は0.1〜0.95で指定してください。")
    for value in (settings.brightness, settings.saturation, settings.red_gain,
                  settings.green_gain, settings.blue_gain, settings.contrast):
        if not 0 <= value <= 10:
            raise ValueError("色補正は0〜10で指定してください。")
    tile_patterns.validate(settings.eighth_tone, settings.three_112, settings.three_111,
                           settings.four_colour, settings.five_colour)
    if (settings.alpha_mode not in image_processing.ALPHA_MODES
            or settings.half_tone not in pc88_two_colour.HALF_TONES
            or settings.quarter_tone not in pc88_two_colour.QUARTER_TONES):
        raise ValueError("透明度またはタイルの設定が不正です。")
    line_effects.validate_growth(settings.line_growth)
    anime_paint.validate_settings(settings.anime_tolerance, settings.anime_radius, settings.anime_min_area)


def _smooth_document(document: ConversionDocument, size: tuple[int, int],
                     settings: ConversionOptions, expand_first: bool) -> ConversionDocument:
    """Filter the final composite; keep disjoint layer ownership for exports.

    Convert indexed images to true colour so Pillow actually uses LANCZOS.
    RGBA resize internally uses premultiplied alpha, avoiding hidden-RGB halos.
    """
    mode = "RGBA" if document.alpha is not None else "RGB"
    image = document.image.convert(mode)
    if expand_first:
        image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    image = image.resize(size, Image.Resampling.LANCZOS)
    alpha = image.getchannel("A") if mode == "RGBA" else None
    if alpha is not None and settings.alpha_mode == "binary":
        # Filtering creates fractional coverage; respect an explicitly chosen
        # binary-alpha mode at the final output resolution as well.
        alpha = alpha.point(lambda value: 255 if value >= settings.alpha_threshold else 0)
        image.putalpha(alpha)
    labels = document.labels
    if labels.shape != (size[1], size[0]):
        labels = np.asarray(Image.fromarray(labels.astype(np.int32), "I").resize(size, Image.Resampling.NEAREST))
    return ConversionDocument(image, alpha, labels, document.names, document.tile_colours)


def convert_document(source: Path | Image.Image, settings: ConversionOptions = ConversionOptions()) -> ConversionDocument:
    _validate_options(settings)
    render = _anime_image if settings.machine == "anime" else _retro_image
    if not settings.smooth_2x:
        return render(source, settings)
    if settings.resize_640:
        # The 640px option deliberately chooses a post-conversion round trip.
        document = render(source, settings)
        return _smooth_document(document, document.image.size, settings, expand_first=True)
    with (nullcontext(source) if isinstance(source, Image.Image) else Image.open(source)) as opened:
        has_alpha = "A" in opened.getbands() or "transparency" in opened.info
        original_size = opened.size
        native = opened.convert("RGBA" if has_alpha else "RGB")
        working = native.resize(
            (opened.width * 2, opened.height * 2), Image.Resampling.LANCZOS,
        )
    if settings.machine == "anime":
        reference = _anime_reference(native, settings)
    else:
        palette = np.asarray(resolved_palette(settings), dtype=np.uint8)
        pixels, coverage, _alpha, _logical, _output, ink = _prepare_source(native, settings)
        reference = _retro_reference(pixels, ink, coverage, palette, settings)
    document = render(working, settings, reference=reference, spatial_scale=2)
    return _smooth_document(document, original_size, settings, expand_first=False)


def convert_image(source: Path | Image.Image, settings: ConversionOptions = ConversionOptions()) -> tuple[Image.Image, Image.Image | None]:
    document = convert_document(source, settings)
    return document.image, document.alpha


def convert(
    source: Path,
    destination: Path,
    settings: ConversionOptions = ConversionOptions(),
    alpha_mask: Path | None = None,
    invert_alpha_mask: bool = False,
    *,
    psd_layers: str | Iterable[str] = (),
) -> Image.Image:
    document = convert_document(source, settings)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.lower() == ".psd":
        write_psd(destination, document, extra_layers=psd_layers)
    elif destination.suffix.lower() == ".zip":
        write_zip(destination, document)
    else:
        save_options = {}
        if destination.suffix.lower() == ".png":
            palette = resolved_palette(settings)
            save_options["pnginfo"] = png_settings.png_info(
                source, settings, palette, alpha_mask is not None, invert_alpha_mask,
            )
        document.image.save(destination, optimize=False, **save_options)
    if alpha_mask is not None:
        alpha_mask = Path(alpha_mask)
        alpha_mask.parent.mkdir(parents=True, exist_ok=True)
        mask_alpha = document.alpha if document.alpha is not None else Image.new("L", document.image.size, 255)
        image_processing.alpha_mask_image(mask_alpha, invert_alpha_mask).save(alpha_mask)
    return document.image


def main() -> None:
    parser = argparse.ArgumentParser(description="Digital8 Anime V4 converter")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--psd-layers", choices=PSD_LAYER_MODES, action="append", default=[],
                        help="repeat to add colour or fill groups to the patterned PSD")
    parser.add_argument("--preset", choices=MACHINES, default="pc8801")
    parser.add_argument("--machine", choices=MACHINES)
    parser.add_argument("--palette")
    parser.add_argument("--tall-dots", action=argparse.BooleanOptionalAction)
    parser.add_argument("--half-tone", choices=pc88_two_colour.HALF_TONES)
    parser.add_argument("--quarter-tone", choices=pc88_two_colour.QUARTER_TONES)
    parser.add_argument('--eighth-tone',choices=tile_patterns.EIGHTH_TONES)
    parser.add_argument('--three-112',choices=tile_patterns.THREE_112)
    parser.add_argument('--three-111',choices=tile_patterns.THREE_111)
    parser.add_argument('--four-colour', action=argparse.BooleanOptionalAction)
    parser.add_argument('--five-colour', action=argparse.BooleanOptionalAction)
    parser.add_argument("--colour-groups", type=int)
    parser.add_argument("--pixel-size", type=int)
    parser.add_argument("--line-threshold", type=int)
    parser.add_argument("--resize-640", action=argparse.BooleanOptionalAction)
    parser.add_argument("--smooth-2x", action=argparse.BooleanOptionalAction,
                        help="LANCZOS 2x/half: before conversion without 640px, after conversion with 640px")
    parser.add_argument("--alpha-mode", choices=image_processing.ALPHA_MODES)
    parser.add_argument("--alpha-threshold", type=int)
    parser.add_argument("--alpha-mask", type=Path)
    parser.add_argument("--anime-tolerance", type=float)
    parser.add_argument("--anime-radius", type=int)
    parser.add_argument("--anime-min-area", type=int)
    parser.add_argument("--palette-file", type=Path, help="RGB palette JSON (PC88: 8, PC98: 16, Anime: 1..4096 colours)")
    parser.add_argument("--brightness", type=float)
    parser.add_argument("--saturation", type=float)
    parser.add_argument("--contrast", type=float)
    parser.add_argument("--edge-strength", type=float)
    parser.add_argument("--anime-max-colours", type=int)
    parser.add_argument("--line-growth", type=float)
    parser.add_argument("--colour-trace", action=argparse.BooleanOptionalAction)
    parser.add_argument("--trace-strength", type=float)
    parser.add_argument("--white-transparent", action=argparse.BooleanOptionalAction)
    args = parser.parse_args()
    presets = {"pc8801": pc8801_standard, "pc9801": pc9801_standard, "anime": anime_standard}
    settings = presets[args.machine or args.preset]()
    overrides = {
        key: value for key, value in {
            "palette_id": args.palette,
            "tall_dots": args.tall_dots,
            "half_tone": args.half_tone, "quarter_tone": args.quarter_tone,
            'eighth_tone':args.eighth_tone,'three_112':args.three_112,'three_111':args.three_111,
            'four_colour':args.four_colour,'five_colour':args.five_colour,
            "colour_groups": args.colour_groups, "pixel_size": args.pixel_size,
            "line_threshold": args.line_threshold, "alpha_mode": args.alpha_mode,
            "alpha_threshold": args.alpha_threshold,
            "anime_tolerance": args.anime_tolerance, "anime_radius": args.anime_radius,
            "anime_min_area": args.anime_min_area, "brightness": args.brightness,
            "saturation": args.saturation, "contrast": args.contrast, "edge_strength": args.edge_strength,
            "anime_max_colours": args.anime_max_colours, "line_growth": args.line_growth,
            "colour_trace": args.colour_trace, "trace_strength": args.trace_strength,
            "white_transparent": args.white_transparent,
            "smooth_2x": args.smooth_2x,
        }.items() if value is not None
    }
    if args.resize_640 is not None:
        overrides["resize_640"] = args.resize_640
    settings = replace(settings, **overrides)
    if args.palette_file:
        try:
            colours = anime_paint.load_palette(args.palette_file)
            if settings.machine == "anime":
                settings = replace(settings, palette_id="custom", anime_palette=colours)
            else:
                colours = validate_retro_palette(settings.machine, colours)
                settings = replace(settings, palette_id="custom", retro_palette=colours)
        except (ValueError, OSError) as error:
            parser.error(str(error))
    convert(args.input, args.output, settings, args.alpha_mask, psd_layers=args.psd_layers)


if __name__ == "__main__":
    main()
