#!/usr/bin/env python3
"""Build a balanced PC-8801 empirical tile reference for the V2 converter.

Unlike the original frequency counter, this extractor gives each source image
roughly equal influence.  A long sequence containing the same large fill area
therefore cannot overwhelm patterns that occur across many different images.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pc8801_digital8_v1 import PALETTE  # noqa: E402


IMAGE_SUFFIXES = {".gif", ".png", ".bmp"}
TILE_SHAPES = ((2, 4), (4, 4))


def image_files(source_directories: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for directory in source_directories:
        files.update(
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
    return sorted(files)


def to_native_indices(path: Path) -> tuple[np.ndarray, str]:
    rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    # Several supplied captures are 642x402: a 640x400 doubled screen with a
    # one-pixel frame.  Remove only this characteristic horizontal frame; the
    # vertical phase is handled after palette decoding below.
    if rgb.shape[1] % 4 == 2 and rgb.shape[1] >= 642:
        rgb = rgb[:, 1:-1]
    peak = int(rgb.max())
    if peak < 96:
        raise ValueError("no digital-8 TRUE level")

    # Accept both 255-level and the common ~187-level captures, with a small
    # tolerance for encoders that altered a channel by one or two values.
    threshold = peak * 0.5
    bits = rgb > threshold
    reconstructed = bits.astype(np.int16) * peak
    # Some otherwise exact captures contain a near-white (255,248,255) slot.
    # A 12-level tolerance treats that encoder/palette drift as logical white
    # without admitting the genuinely blurred companion image.
    if float(np.mean(np.max(np.abs(rgb - reconstructed), axis=2) <= 12)) < 0.99:
        raise ValueError("contains colours outside a digital-8 palette")
    indices = (
        bits[:, :, 2].astype(np.uint8)
        + bits[:, :, 0].astype(np.uint8) * 2
        + bits[:, :, 1].astype(np.uint8) * 4
    )

    if indices.shape[0] % 2 == 0:
        even_agreement = float(np.mean(indices[0::2] == indices[1::2]))
        if even_agreement >= 0.97:
            return indices[0::2], "doubled-200-line-even-phase"
        # Framed captures commonly duplicate rows 1-2, 3-4, ... and leave a
        # one-pixel border at top and bottom.  Detect that phase explicitly.
        if indices.shape[0] >= 4:
            odd_agreement = float(np.mean(indices[1:-1:2] == indices[2::2]))
            if odd_agreement >= 0.97:
                return indices[1:-1:2], "doubled-200-line-odd-phase"
    if indices.shape[0] <= 240:
        return indices, "native"
    raise ValueError("not a native or vertically doubled 200-line capture")


def is_horizontal_stripe(tile: np.ndarray) -> bool:
    rows_are_solid = all(len(np.unique(row)) == 1 for row in tile)
    return rows_are_solid and len(np.unique(tile[:, 0])) > 1


def colour_family(target: np.ndarray) -> str:
    maximum = float(target.max())
    minimum = float(target.min())
    saturation = 0.0 if maximum == 0 else (maximum - minimum) / maximum
    if maximum < 56:
        return "dark"
    if saturation < 0.075:
        return "light" if maximum >= 224 else "neutral"
    r, g, b = map(float, target)
    chroma = maximum - minimum
    if maximum == r:
        hue = ((g - b) / chroma) % 6 * 60
    elif maximum == g:
        hue = ((b - r) / chroma + 2) * 60
    else:
        hue = ((r - g) / chroma + 4) * 60
    if hue < 65 or hue >= 345:
        return "warm"
    if hue < 145:
        return "green"
    if hue < 195:
        return "teal"
    if hue < 265:
        return "cool"
    return "magenta"


def local_patterns(indices: np.ndarray, shape: tuple[int, int]) -> Counter[tuple[int, ...]]:
    tile_height, tile_width = shape
    counts: Counter[tuple[int, ...]] = Counter()
    usable_height = indices.shape[0] // tile_height * tile_height
    usable_width = indices.shape[1] // tile_width * tile_width
    for y in range(0, usable_height, tile_height):
        for x in range(0, usable_width, tile_width):
            matrix = indices[y : y + tile_height, x : x + tile_width]
            colour_count = len(np.unique(matrix))
            if not 2 <= colour_count <= 4 or is_horizontal_stripe(matrix):
                continue
            counts[tuple(int(value) for value in matrix.reshape(-1))] += 1
    return counts


def collect(source_directories: Iterable[Path]) -> tuple[list[dict], list[dict]]:
    total_counts: Counter[tuple[int, int, tuple[int, ...]]] = Counter()
    image_support: Counter[tuple[int, int, tuple[int, ...]]] = Counter()
    balanced_score: defaultdict[tuple[int, int, tuple[int, ...]], float] = defaultdict(float)
    diagnostics: list[dict] = []

    for path in image_files(source_directories):
        try:
            native, capture_type = to_native_indices(path)
        except Exception as error:
            diagnostics.append({"file": str(path), "status": "skipped", "reason": str(error)})
            continue
        found = 0
        for shape in TILE_SHAPES:
            local = local_patterns(native, shape)
            if not local:
                continue
            normaliser = sum(math.log1p(count) for count in local.values())
            for tile, count in local.items():
                key = (shape[0], shape[1], tile)
                total_counts[key] += count
                image_support[key] += 1
                balanced_score[key] += math.log1p(count) / normaliser
                found += count
        diagnostics.append(
            {"file": str(path), "status": "used", "capture": capture_type, "native_size": list(native.shape[::-1]), "tiles": found}
        )

    ranked: list[dict] = []
    for (height, width, tile), count in total_counts.items():
        matrix = np.asarray(tile, dtype=np.uint8).reshape(height, width)
        target = PALETTE[matrix].astype(np.float32).mean(axis=(0, 1))
        support = int(image_support[(height, width, tile)])
        balance = float(balanced_score[(height, width, tile)])
        # Cross-image support dominates, while balanced frequency resolves
        # ties without letting one repeated scene determine the vocabulary.
        score = support + balance * 3.0 + math.log1p(count) * 0.02
        ranked.append(
            {
                "tile_width": width,
                "tile_height": height,
                "tile": list(tile),
                "colours": sorted(int(value) for value in np.unique(matrix)),
                "target": [round(float(value), 3) for value in target],
                "family": colour_family(target),
                "count": int(count),
                "image_support": support,
                "balanced_score": round(balance, 8),
                "score": round(score, 8),
            }
        )
    ranked.sort(key=lambda item: (-item["score"], -item["image_support"], -item["count"], item["tile"]))
    return ranked, diagnostics


def create_sheet(patterns: list[dict], destination: Path) -> None:
    columns, cell_width, cell_height, scale = 6, 128, 116, 16
    rows = max(1, (len(patterns) + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    for number, pattern in enumerate(patterns, start=1):
        column, row = (number - 1) % columns, (number - 1) // columns
        left, top = column * cell_width + 8, row * cell_height + 8
        matrix = np.asarray(pattern["tile"], dtype=np.uint8).reshape(pattern["tile_height"], pattern["tile_width"])
        for y in range(matrix.shape[0]):
            for x in range(matrix.shape[1]):
                colour = tuple(int(value) for value in PALETTE[matrix[y, x]])
                draw.rectangle((left + x * scale, top + y * scale, left + (x + 1) * scale - 1, top + (y + 1) * scale - 1), fill=colour)
        draw.text((left, top + 4 * scale + 3), f"#{number} {matrix.shape[1]}x{matrix.shape[0]}", fill=(235, 235, 235))
        draw.text((left, top + 4 * scale + 19), f"img:{pattern['image_support']} n:{pattern['count']}", fill=(200, 200, 200))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a balanced PC-8801 4x2/4x4 V2 tile reference.")
    parser.add_argument("sources", type=Path, nargs="+")
    parser.add_argument("--limit-per-shape", type=int, default=160)
    parser.add_argument("--json", type=Path, default=Path(__file__).resolve().parents[1] / "pc8801_reference_v2.json")
    parser.add_argument("--sheet", type=Path, default=Path(__file__).resolve().parents[1] / "reference" / "reference_pattern_sheet_v2.png")
    args = parser.parse_args()
    for source in args.sources:
        if not source.is_dir():
            parser.error(f"not a directory: {source}")

    ranked, diagnostics = collect(args.sources)
    selected: list[dict] = []
    for height, width in TILE_SHAPES:
        selected.extend([item for item in ranked if item["tile_height"] == height and item["tile_width"] == width][: args.limit_per_shape])
        # Preserve complementary RGB/W or other multi-colour neutral recipes
        # even when they occur in only one specialised reference image.  These
        # are the important mineral/metal greys described in the supplied note
        # and raw global frequency would otherwise discard them.
        selected.extend(
            [
                item for item in ranked
                if item["tile_height"] == height
                and item["tile_width"] == width
                and item["family"] == "neutral"
                and len(item["colours"]) >= 3
            ][:12]
        )
        selected.extend(
            [
                item for item in ranked
                if item["tile_height"] == height
                and item["tile_width"] == width
                and {1, 2, 4, 7}.issubset(item["colours"])
            ][:4]
        )
    selected = list({(item["tile_height"], item["tile_width"], tuple(item["tile"])): item for item in selected}.values())
    selected.sort(key=lambda item: (-item["score"], item["tile_height"], item["tile_width"]))
    for rank, item in enumerate(selected, start=1):
        item["rank"] = rank

    payload = {
        "version": 2,
        "method": "per-image-balanced-frequency; horizontal-solid-row stripes excluded",
        "source_images": sum(item["status"] == "used" for item in diagnostics),
        "skipped_images": sum(item["status"] == "skipped" for item in diagnostics),
        "patterns": selected,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    create_sheet(selected, args.sheet)
    print(f"used={payload['source_images']} skipped={payload['skipped_images']} patterns={len(selected)}")
    print(args.json)
    print(args.sheet)


if __name__ == "__main__":
    main()
