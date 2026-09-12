#!/usr/bin/env python3
"""Extract PC-9801 16-colour palettes and colour-agnostic 4x4 tile masks.

Palette-folder images are reduced to at most sixteen representative RGB
colours.  Very close colours are merged first so compression/capture noise
does not create artificial palette entries.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


IMAGE_SUFFIXES = {".png", ".gif", ".bmp", ".jpg", ".jpeg", ".webp"}

# Eight conventional digital RGB colours followed by their 50% intensity
# counterparts.  Black intentionally occupies one slot in each bank: this is
# a 16-entry hardware-style palette even though it contains 15 unique RGBs.
DIGITAL8_HALF_PALETTE = (
    (0, 0, 0), (0, 0, 255), (255, 0, 0), (255, 0, 255),
    (0, 255, 0), (0, 255, 255), (255, 255, 0), (255, 255, 255),
    (0, 0, 0), (0, 0, 128), (128, 0, 0), (128, 0, 128),
    (0, 128, 0), (0, 128, 128), (128, 128, 0), (128, 128, 128),
)

# These reference cards contain an explicit 16-slot palette strip.  Reading
# its swatch centres is more accurate than quantising the illustration, title
# text, and antialiasing together.
EXPLICIT_PALETTE_STRIPS = {
    "20997561.png": [(484 + 17 * index, 54) for index in range(16)],
    "209975620.png": [(484 + 17 * index, 54) for index in range(16)],
}

PALETTE_SHEET_NAME = "4e6f08b65cad3201b8f53aa4bce14dd9.png"
PALETTE_SHEET_ROWS = (
    ("ED3", 56, 16),
    ("16color_同級生2", 128, 16),
    ("16colorきゃんプルミ", 200, 16),
    ("16colorきゃんプルミ2", 272, 15),
    # The 344 row (16color_dokiv_緑なし) is intentionally not registered.
    ("16color_同級生1", 416, 16),
)


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def merge_near_colours(colours: list[tuple[np.ndarray, int]], distance: float) -> list[tuple[tuple[int, int, int], int]]:
    """Merge weighted RGB entries whose Euclidean distance is negligible."""
    clusters: list[tuple[np.ndarray, int]] = []
    for colour, count in sorted(colours, key=lambda item: item[1], reverse=True):
        if clusters:
            distances = [float(np.linalg.norm(colour - centre)) for centre, _ in clusters]
            nearest = int(np.argmin(distances))
            if distances[nearest] <= distance:
                centre, old_count = clusters[nearest]
                total = old_count + count
                clusters[nearest] = ((centre * old_count + colour * count) / total, total)
                continue
        clusters.append((colour.astype(np.float64), count))
    return [(tuple(map(int, np.rint(centre).clip(0, 255))), int(count)) for centre, count in clusters]


def palette_from_image(path: Path, merge_distance: float) -> tuple[tuple[tuple[int, int, int], ...], dict]:
    image = Image.open(path).convert("RGB")
    rgb = np.asarray(image, dtype=np.uint8)
    exact, exact_counts = np.unique(rgb.reshape(-1, 3), axis=0, return_counts=True)
    if path.name in EXPLICIT_PALETTE_STRIPS:
        colours = tuple(tuple(map(int, image.getpixel(point))) for point in EXPLICIT_PALETTE_STRIPS[path.name])
        return colours, {
            "file": path.name,
            "original_colour_count": int(len(exact)),
            "result_colour_count": len(colours),
            "quantized_to_16": False,
            "extraction": "explicit_palette_strip",
        }
    exact_entries = [(colour.astype(np.float64), int(count)) for colour, count in zip(exact, exact_counts, strict=True)]
    merged = merge_near_colours(exact_entries, merge_distance)
    quantized = len(merged) > 16
    if quantized:
        reduced = image.quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        raw_palette = np.asarray(reduced.getpalette()[:768], dtype=np.uint8).reshape(-1, 3)
        used = reduced.getcolors(maxcolors=256) or []
        entries = [(raw_palette[index].astype(np.float64), int(count)) for count, index in used]
        merged = merge_near_colours(entries, merge_distance)
    colours = tuple(sorted(colour for colour, _ in merged))
    return colours, {
        "file": path.name,
        "original_colour_count": int(len(exact)),
        "result_colour_count": len(colours),
        "quantized_to_16": quantized,
        "extraction": "quantized_image" if quantized else "exact_image_colours",
    }


def palettes_equivalent(a: tuple[tuple[int, int, int], ...], b: tuple[tuple[int, int, int], ...], distance: float) -> bool:
    if len(a) != len(b):
        return False
    remaining = [np.asarray(colour, dtype=np.float64) for colour in b]
    for colour in a:
        distances = [float(np.linalg.norm(np.asarray(colour) - candidate)) for candidate in remaining]
        nearest = int(np.argmin(distances))
        if distances[nearest] > distance:
            return False
        remaining.pop(nearest)
    return True


def extract_palettes(folder: Path, merge_distance: float = 12.0, equivalence_distance: float = 18.0) -> tuple[list[dict], list[dict]]:
    """Reduce every source to <=16 colours and merge near-equivalent sets."""
    groups: list[dict] = []
    diagnostics: list[dict] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        colours, diagnostic = palette_from_image(path, merge_distance)
        diagnostics.append(diagnostic)
        existing = next((group for group in groups if palettes_equivalent(group["colours"], colours, equivalence_distance)), None)
        if existing is None:
            groups.append({"colours": colours, "sources": [path.name]})
        else:
            existing["sources"].append(path.name)
    palettes = [
        {"id": f"palette_{index:02d}", "colours": [list(colour) for colour in group["colours"]], "sources": group["sources"]}
        for index, group in enumerate(sorted(groups, key=lambda item: item["sources"][0]), 1)
    ]
    palettes.append(
        {
            "id": "digital8_half",
            "colours": [list(colour) for colour in DIGITAL8_HALF_PALETTE],
            "sources": ["built-in: digital 8 + 50% intensity 8"],
        }
    )
    return palettes, diagnostics


def extract_palette_sheet(path: Path) -> tuple[list[dict], list[dict]]:
    """Read explicitly drawn horizontal swatches from the supplied palette sheet."""
    if not path.is_file():
        return [], []
    image = Image.open(path).convert("RGB")
    palettes, diagnostics = [], []
    for palette_id, y, slot_count in PALETTE_SHEET_ROWS:
        colours = [list(map(int, image.getpixel((43 + 21 * index, y)))) for index in range(slot_count)]
        palettes.append({"id": palette_id, "colours": colours, "sources": [f"{path.name}#{palette_id}"]})
        diagnostics.append({
            "file": f"{path.name}#{palette_id}",
            "original_colour_count": len({tuple(colour) for colour in colours}),
            "result_colour_count": slot_count,
            "quantized_to_16": False,
            "extraction": "explicit_palette_sheet_row",
        })
    return palettes, diagnostics


def canonical_mask(tile: np.ndarray) -> tuple[int, ...] | None:
    """Encode a two-colour tile as a colour-independent, inversion-free mask."""
    values = np.unique(tile)
    if len(values) != 2:
        return None
    # Colour A/B have no semantic meaning.  Collapse an inverted pattern to
    # the same 16-bit structure by choosing the lexicographically smaller one.
    bits = tuple((tile.reshape(-1) == values[1]).astype(np.uint8).tolist())
    inverted = tuple(1 - bit for bit in bits)
    return min(bits, inverted)


def extract_patterns(sample_root: Path) -> Counter[tuple[int, ...]]:
    counts: Counter[tuple[int, ...]] = Counter()
    weights = (1 << np.arange(16, dtype=np.uint16))
    for path in sorted(sample_root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        rgb = load_rgb(path)
        height, width, _ = rgb.shape
        # Assign a local colour ID once per image.  Comparing IDs makes the
        # tile collection independent of that image's actual palette.
        _, ids = np.unique(rgb.reshape(-1, 3), axis=0, return_inverse=True)
        ids = ids.reshape(height, width)
        # Inspect all sixteen 4x4 grid phases.  This catches tiles even when a
        # capture has a different origin, while retaining true 4x4 structures.
        for y_phase in range(4):
            for x_phase in range(4):
                cropped = ids[y_phase:, x_phase:]
                tile_height = (cropped.shape[0] // 4) * 4
                tile_width = (cropped.shape[1] // 4) * 4
                if not tile_height or not tile_width:
                    continue
                # (rows, 4, cols, 4) -> one 16-cell row per tile.
                blocks = cropped[:tile_height, :tile_width].reshape(tile_height // 4, 4, tile_width // 4, 4)
                blocks = blocks.transpose(0, 2, 1, 3).reshape(-1, 16)
                ordered = np.sort(blocks, axis=1)
                exactly_two = np.count_nonzero(np.diff(ordered, axis=1), axis=1) == 1
                blocks = blocks[exactly_two]
                if not len(blocks):
                    continue
                high = ordered[exactly_two, -1]
                bits = blocks == high[:, None]
                codes = (bits.astype(np.uint16) * weights).sum(axis=1)
                canonical = np.minimum(codes, np.uint16(0xFFFF) ^ codes)
                codes, occurrences = np.unique(canonical, return_counts=True)
                for code, occurrence in zip(codes, occurrences, strict=True):
                    mask = tuple(int((int(code) >> bit) & 1) for bit in range(16))
                    counts[mask] += int(occurrence)
    return counts


def make_sheet(patterns: list[dict], destination: Path) -> None:
    cell, swatch, label = 118, 16, 22
    columns = 5
    rows = max(1, (len(patterns) + columns - 1) // columns)
    image = Image.new("RGB", (columns * cell, rows * cell), "white")
    draw = ImageDraw.Draw(image)
    for index, pattern in enumerate(patterns):
        x0, y0 = (index % columns) * cell, (index // columns) * cell
        bits = np.asarray(pattern["bits"], dtype=np.uint8).reshape(4, 4)
        for y in range(4):
            for x in range(4):
                colour = (30, 30, 30) if bits[y, x] else (235, 235, 235)
                draw.rectangle((x0 + x * swatch, y0 + y * swatch, x0 + (x + 1) * swatch - 1, y0 + (y + 1) * swatch - 1), fill=colour)
        draw.text((x0, y0 + 4 * swatch + 3), f"#{pattern['rank']}  {pattern['count']}x  {pattern['coverage']}/16", fill="black")
    image.save(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PC-9801 16-colour palettes and 4x4 masks.")
    parser.add_argument("sample_root", type=Path)
    parser.add_argument("--palette-folder", default="pallete")
    parser.add_argument("--top-patterns", type=int, default=80)
    parser.add_argument("--merge-distance", type=float, default=12.0)
    parser.add_argument("--palette-equivalence", type=float, default=18.0)
    parser.add_argument("--reuse-patterns", action="store_true", help="reuse patterns already stored in pc9801_reference.json")
    parser.add_argument("--palette-sheet", type=Path, default=Path(__file__).resolve().parents[1] / "reference" / PALETTE_SHEET_NAME)
    args = parser.parse_args()
    palette_folder = args.sample_root / args.palette_folder
    palettes, diagnostics = extract_palettes(palette_folder, args.merge_distance, args.palette_equivalence)
    sheet_palettes, sheet_diagnostics = extract_palette_sheet(args.palette_sheet)
    palettes.extend(sheet_palettes)
    diagnostics.extend(sheet_diagnostics)
    output_json = args.sample_root / "pc9801_reference.json"
    if args.reuse_patterns and output_json.exists():
        previous = json.loads(output_json.read_text(encoding="utf-8"))
        patterns = previous["patterns"]
        sample_tile_count = int(previous["sample_tile_count"])
    else:
        counts = extract_patterns(args.sample_root)
        ranked = counts.most_common(args.top_patterns)
        patterns = [
            {"rank": rank, "count": count, "coverage": int(sum(mask)), "bits": list(mask)}
            for rank, (mask, count) in enumerate(ranked, 1)
        ]
        sample_tile_count = int(sum(counts.values()))
    payload = {
        "palette_folder": str(palette_folder),
        "palettes": palettes,
        "palette_quantization": diagnostics,
        "merge_distance": args.merge_distance,
        "palette_equivalence_distance": args.palette_equivalence,
        "patterns": patterns,
        "sample_tile_count": sample_tile_count,
    }
    output_sheet = args.sample_root / "pc9801_pattern_sheet.png"
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    make_sheet(patterns, output_sheet)
    print(f"palettes={len(palettes)} quantized={sum(item['quantized_to_16'] for item in diagnostics)} patterns={len(patterns)} tiles={sample_tile_count}")
    print(output_json)
    print(output_sheet)


if __name__ == "__main__":
    main()
