#!/usr/bin/env python3
"""Build an image-balanced 4x4 two-colour pattern reference for PC-9801 V2."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


IMAGE_SUFFIXES = {".png", ".gif", ".bmp", ".jpg", ".jpeg", ".webp"}
WEIGHTS = 1 << np.arange(16, dtype=np.uint16)


def image_files(root: Path) -> list[Path]:
    ignored_parts = {"pallete", "palette", "reference"}
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and not ignored_parts.intersection(part.lower() for part in path.relative_to(root).parts[:-1])
        and "pattern_sheet" not in path.stem.lower()
    )


def masks_in_image(path: Path) -> Counter[int]:
    rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
    height, width, _ = rgb.shape
    colours, ids = np.unique(rgb.reshape(-1, 3), axis=0, return_inverse=True)
    # True-colour illustrations and compressed previews are not PC-9801 tile
    # references.  The generous limit still admits captures with a few altered
    # palette entries.
    if len(colours) > 64:
        raise ValueError(f"too many source colours: {len(colours)}")
    ids = ids.reshape(height, width)
    counts: Counter[int] = Counter()
    for y_phase in range(4):
        for x_phase in range(4):
            cropped = ids[y_phase:, x_phase:]
            tile_height = cropped.shape[0] // 4 * 4
            tile_width = cropped.shape[1] // 4 * 4
            if not tile_height or not tile_width:
                continue
            blocks = cropped[:tile_height, :tile_width].reshape(tile_height // 4, 4, tile_width // 4, 4)
            blocks = blocks.transpose(0, 2, 1, 3).reshape(-1, 16)
            ordered = np.sort(blocks, axis=1)
            exactly_two = np.count_nonzero(np.diff(ordered, axis=1), axis=1) == 1
            blocks = blocks[exactly_two]
            if not len(blocks):
                continue
            high = ordered[exactly_two, -1]
            bits = blocks == high[:, None]
            codes = (bits.astype(np.uint16) * WEIGHTS).sum(axis=1)
            canonical = np.minimum(codes, np.uint16(0xFFFF) ^ codes)
            values, occurrences = np.unique(canonical, return_counts=True)
            counts.update({int(value): int(count) for value, count in zip(values, occurrences, strict=True)})
    return counts


def collect(root: Path) -> tuple[list[dict], list[dict]]:
    total: Counter[int] = Counter()
    support: Counter[int] = Counter()
    balanced: defaultdict[int, float] = defaultdict(float)
    diagnostics: list[dict] = []
    for path in image_files(root):
        try:
            local = masks_in_image(path)
        except Exception as error:
            diagnostics.append({"file": str(path.relative_to(root)), "status": "skipped", "reason": str(error)})
            continue
        normaliser = sum(math.log1p(count) for count in local.values()) or 1.0
        for code, count in local.items():
            total[code] += count
            support[code] += 1
            balanced[code] += math.log1p(count) / normaliser
        diagnostics.append({"file": str(path.relative_to(root)), "status": "used", "unique_masks": len(local), "tiles": sum(local.values())})

    candidates: list[dict] = []
    for code, count in total.items():
        bits = [int((code >> bit) & 1) for bit in range(16)]
        coverage = sum(bits)
        if not 0 < coverage < 16:
            continue
        item_support = int(support[code])
        item_balance = float(balanced[code])
        score = item_support + item_balance * 3.0 + math.log1p(count) * 0.02
        candidates.append(
            {
                "coverage": coverage,
                "bits": bits,
                "count": int(count),
                "image_support": item_support,
                "balanced_score": round(item_balance, 8),
                "score": round(score, 8),
            }
        )
    candidates.sort(key=lambda item: (-item["score"], -item["image_support"], -item["count"], item["bits"]))
    return candidates, diagnostics


def create_sheet(patterns: list[dict], destination: Path) -> None:
    cell, swatch, columns = 120, 16, 5
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
        draw.text((x0, y0 + 68), f"{pattern['coverage']}/16 img:{pattern['image_support']}", fill="black")
        draw.text((x0, y0 + 84), f"n:{pattern['count']}", fill="black")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create image-balanced PC-9801 V2 4x4 masks.")
    parser.add_argument("sample_root", type=Path)
    parser.add_argument("--base-reference", type=Path, default=Path(__file__).resolve().parents[1] / "pc9801_reference.json")
    parser.add_argument("--json", type=Path, default=Path(__file__).resolve().parents[1] / "pc9801_reference_v2.json")
    parser.add_argument("--sheet", type=Path, default=Path(__file__).resolve().parents[1] / "reference" / "pc9801_pattern_sheet_v2.png")
    parser.add_argument("--alternatives-per-coverage", type=int, default=4)
    args = parser.parse_args()

    base = json.loads(args.base_reference.read_text(encoding="utf-8"))
    candidates, diagnostics = collect(args.sample_root)
    selected: list[dict] = []
    for coverage in range(1, 16):
        selected.extend([item for item in candidates if item["coverage"] == coverage][: args.alternatives_per_coverage])
    selected.sort(key=lambda item: (item["coverage"], -item["score"]))
    for rank, item in enumerate(selected, start=1):
        item["rank"] = rank

    payload = {
        "version": 2,
        "method": "per-image-balanced-frequency; four masks retained per coverage",
        "palettes": base["palettes"],
        "palette_quantization": base.get("palette_quantization", []),
        "patterns": selected,
        "source_images": sum(item["status"] == "used" for item in diagnostics),
        "skipped_images": sum(item["status"] == "skipped" for item in diagnostics),
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    create_sheet(selected, args.sheet)
    print(f"used={payload['source_images']} skipped={payload['skipped_images']} patterns={len(selected)}")
    print(args.json)
    print(args.sheet)


if __name__ == "__main__":
    main()
