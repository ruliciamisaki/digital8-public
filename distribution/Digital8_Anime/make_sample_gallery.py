"""Full-resolution TE/CW tests; all generated assets stay in ignored preview/."""

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import digital8_converter as converter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=converter.ROOT / "preview" / "samples")
    parser.add_argument("--colours", type=int, default=24)
    parser.add_argument("--tolerance", type=float, default=48)
    parser.add_argument("--line-growth", type=float, default=.5)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows, records = [], []
    for number, source in enumerate(args.inputs, 1):
        with Image.open(source) as opened:
            original = opened.convert("RGB")
        settings = replace(converter.anime_standard(), anime_tolerance=args.tolerance,
                           anime_max_colours=args.colours, line_growth=args.line_growth)
        start = time.perf_counter()
        te = converter.convert_document(source, settings)
        te_seconds = time.perf_counter() - start
        te.image.save(args.output / f"{number:02d}_TE.png")
        start = time.perf_counter()
        cw = converter.convert_document(source, replace(settings, palette_id="cw", colour_trace=True))
        cw_seconds = time.perf_counter() - start
        cw.image.save(args.output / f"{number:02d}_CW_trace.png")
        # Same fills and same palette subset: replace only the trace layer with
        # the selected chart's darkest ink for a controlled before/after view.
        plain_pixels = np.array(cw.image.convert("RGBA"))
        palette = converter.palette_for("anime", "cw").array()
        darkest = palette[np.argmin(palette @ np.array((.2126, .7152, .0722)))]
        line_id = next(index for index, name in cw.names.items() if name.startswith("Lines"))
        plain_pixels[cw.labels == line_id, :3] = darkest
        plain = Image.fromarray(plain_pixels, "RGBA")
        plain.save(args.output / f"{number:02d}_CW_black.png")
        record = {"source": source.name, "size": list(original.size), "TE_seconds": round(te_seconds, 3),
                  "CW_trace_seconds": round(cw_seconds, 3), "maximum_colours": args.colours,
                  "TE_actual_colours": len(te.image.convert("RGB").getcolors(1000000)),
                  "CW_actual_colours": len(cw.image.convert("RGB").getcolors(1000000)),
                  "CW_layers": len(cw.layer_ids())}
        if number == 1:
            start = time.perf_counter()
            converter.write_psd(args.output / "01_CW_trace.psd", cw)
            record["PSD_seconds"] = round(time.perf_counter() - start, 3)
            start = time.perf_counter()
            converter.write_zip(args.output / "01_CW_trace.zip", cw)
            record["ZIP_seconds"] = round(time.perf_counter() - start, 3)
        record["tolerance"] = args.tolerance
        record["line_growth"] = args.line_growth
        records.append(record)
        row = Image.new("RGB", (1120, 392), "#ddd")
        draw = ImageDraw.Draw(row)
        for column, (image, label) in enumerate(zip((original, te.image, plain, cw.image),
                                                   ("Source", "TE / black lines", "CW / black lines", "CW / colour trace"))):
            thumbnail = image.convert("RGB")
            thumbnail.thumbnail((280, 360))
            row.paste(thumbnail, (column * 280, 32))
            draw.text((column * 280 + 4, 4), label, fill="black")
        rows.append(row)
        row.save(args.output / f"comparison_{number:02d}.png")
        print(json.dumps(record), flush=True)
        (args.output / "timing.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        del te, cw
    contact = Image.new("RGB", (1120, len(rows) * 392), "#ddd")
    for number, row in enumerate(rows):
        contact.paste(row, (0, number * 392))
    contact.save(args.output / "comparison.png")


if __name__ == "__main__":
    main()
