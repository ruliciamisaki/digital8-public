"""Create local-only source/Anime comparisons and a timing report."""

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

import digital8_converter as converter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("preview"))
    parser.add_argument("--tolerances", type=float, nargs="+", default=(24, 48))
    parser.add_argument("--palette-file", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    settings = converter.anime_standard()
    if args.palette_file:
        settings = replace(settings, anime_palette=converter.anime_paint.load_palette(args.palette_file))
    rows, records = [], []
    for number, source in enumerate(args.inputs):
        with Image.open(source) as opened:
            original = opened.convert("RGB")
        size = original.size
        images, labels, timings = [original], ["Source"], []
        for tolerance in args.tolerances:
            started = time.perf_counter()
            result, _alpha = converter.convert_image(source, replace(settings, anime_tolerance=tolerance))
            timings.append(round(time.perf_counter() - started, 3))
            images.append(result.convert("RGB"))
            labels.append(f"Tolerance {tolerance:g}")
            result.save(args.output / f"sample_{number:02d}_tol{tolerance:g}.png")
        row = Image.new("RGB", (320 * len(images), 224), "#ddd")
        draw = ImageDraw.Draw(row)
        for column, (image, label) in enumerate(zip(images, labels)):
            image.thumbnail((320, 200))
            row.paste(image, (column * 320, 24))
            draw.text((column * 320 + 4, 5), label, fill="black")
        rows.append(row)
        records.append({"source": str(source), "size": size, "tolerances": args.tolerances, "seconds": timings})
    contact = Image.new("RGB", (rows[0].width, 224 * len(rows)), "#ddd")
    for index, row in enumerate(rows):
        contact.paste(row, (0, index * 224))
    contact.save(args.output / "comparison.png")
    (args.output / "timing.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(f"{Path(record['source']).name}: {record['size']} / {record['seconds']} s")


if __name__ == "__main__":
    main()
