#!/usr/bin/env python3
"""Extract 4 x 2 native-dot PC-8801 tiles from vertically doubled GIFs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw

from pc8801_digital8_v1 import PALETTE


TILE_WIDTH = 4
TILE_HEIGHT = 2


def to_palette_indices(image: Image.Image) -> np.ndarray:
    # int16 overflows at 255**2; use int32 so exact digital-8 matches stay exact.
    rgb = np.asarray(image.convert("RGB"), dtype=np.int32)
    # Some sources use 187 rather than 255 as the logical TRUE level.  Reading
    # channel on/off bits is robust to either level and preserves the 8-colour
    # index convention: B=1, R=2, M=3, G=4, C=5, Y=6, W=7.
    peak = int(rgb.max())
    if peak == 0:
        raise ValueError("reference image contains only black")
    bits = rgb > peak // 2
    indices = (bits[:, :, 2].astype(np.uint8) + bits[:, :, 0].astype(np.uint8) * 2 + bits[:, :, 1].astype(np.uint8) * 4)
    valid_levels = np.isin(rgb, (0, peak)).all(axis=2)
    if not valid_levels.all():
        raise ValueError("reference image contains a colour outside a binary digital-8 palette")
    # The supplied GIFs are 640x400 previews of 640x200 raw pixels.
    pair_agreement = float(np.mean(np.all(indices[0::2] == indices[1::2], axis=1))) if not indices.shape[0] % 2 else 0.0
    # A few GIFs have isolated encoder artefacts, but >=99% matching pairs is
    # still a vertically doubled 200-line capture.  Keep the first line.
    if pair_agreement < 0.99:
        raise ValueError("reference image is not a vertically doubled 200-line image")
    return indices[0::2]


def image_files(source_directories: Iterable[Path]) -> list[Path]:
    extensions = {".gif", ".png", ".bmp"}
    return sorted(path for directory in source_directories for path in directory.iterdir() if path.is_file() and path.suffix.lower() in extensions)


def collect(source_directories: Iterable[Path]) -> Counter[tuple[int, ...]]:
    patterns: Counter[tuple[int, ...]] = Counter()
    for path in image_files(source_directories):
        raw = to_palette_indices(Image.open(path))
        # Tiles in this artwork share the screen origin, so use the native
        # (0, 0) phase rather than mixing shifted edge fragments into the set.
        for y in range(0, raw.shape[0] - 1, TILE_HEIGHT):
            for x in range(0, raw.shape[1] - 3, TILE_WIDTH):
                tile = tuple(int(value) for value in raw[y : y + TILE_HEIGHT, x : x + TILE_WIDTH].reshape(-1))
                colours = len(set(tile))
                if 2 <= colours <= 4:
                    patterns[tile] += 1
    return patterns


def create_sheet(patterns: list[tuple[tuple[int, ...], int]], destination: Path) -> None:
    columns, cell_width, cell_height, scale = 5, 150, 105, 20
    rows = (len(patterns) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    for number, (tile, count) in enumerate(patterns, start=1):
        column, row = (number - 1) % columns, (number - 1) // columns
        left, top = column * cell_width + 10, row * cell_height + 10
        matrix = np.asarray(tile, dtype=np.uint8).reshape(TILE_HEIGHT, TILE_WIDTH)
        for y in range(TILE_HEIGHT):
            for x in range(TILE_WIDTH):
                colour = tuple(int(value) for value in PALETTE[matrix[y, x]])
                draw.rectangle((left + x * scale, top + y * scale, left + (x + 1) * scale - 1, top + (y + 1) * scale - 1), fill=colour)
        draw.text((left, top + TILE_HEIGHT * scale + 5), f"{number:02d}: {count}", fill=(230, 230, 230))
    sheet.save(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a ranked PC-8801 4x2 tile reference sheet.")
    parser.add_argument("sources", type=Path, nargs="+", help="directories containing vertically doubled digital-8 images")
    parser.add_argument("--limit", type=int, default=80, help="number of frequent multi-colour tiles to export")
    parser.add_argument("--json", type=Path, default=Path("reference_pattern_stats.json"))
    parser.add_argument("--sheet", type=Path, default=Path("reference_pattern_sheet.png"))
    args = parser.parse_args()
    for source in args.sources:
        if not source.is_dir():
            parser.error(f"not a directory: {source}")
    all_patterns = collect(args.sources)
    ranked = all_patterns.most_common(args.limit)
    payload = [
        {"rank": rank, "count": count, "tile": list(tile), "rows": [list(tile[:4]), list(tile[4:])]}
        for rank, (tile, count) in enumerate(ranked, start=1)
    ]
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    create_sheet(ranked, args.sheet)
    print(f"source images: {len(image_files(args.sources))}")
    print(f"unique multi-colour tiles: {len(all_patterns)}")
    print(f"wrote {args.json} and {args.sheet}")


if __name__ == "__main__":
    main()
