"""Compare old/new trace on identical fills, and fractional ink on fixed paint."""

import argparse
import json
import subprocess
import time
import types
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import digital8_converter as converter
import line_effects


def panel(images: list[Image.Image], labels: list[str], output: Path) -> None:
    canvas = Image.new("RGB", (360 * len(images), 328), "#ddd")
    draw = ImageDraw.Draw(canvas)
    for column, (image, label) in enumerate(zip(images, labels)):
        thumbnail = image.convert("RGB")
        thumbnail.thumbnail((360, 300))
        canvas.paste(thumbnail, (column * 360, 28))
        draw.text((column * 360 + 5, 6), label, fill="black")
    canvas.save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=converter.ROOT / "preview/refinements")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    repository = converter.ROOT.parent.parent
    code = subprocess.run(["git", "show", "198e886:distribution/Digital8_Anime/line_effects.py"],
                          cwd=repository, capture_output=True, check=True).stdout.decode("utf-8")
    previous = types.ModuleType("old_line_effects")
    exec(compile(code, "old_line_effects.py", "exec"), previous.__dict__)
    current = line_effects.trace_indices

    def old_trace(indices, ink, palette, strength=.55, **kwargs):
        return previous.trace_indices(indices, ink, palette, strength,
                                      visible=kwargs.get("visible"), permitted=kwargs.get("permitted"))

    records = []
    presets = (replace(converter.anime_standard(), palette_id="cw_saturated"),
               converter.pc8801_standard(), converter.pc9801_standard())
    for preset in presets:
        start = time.perf_counter()
        plain = converter.convert_document(args.input, preset)
        try:
            line_effects.trace_indices = old_trace
            old = converter.convert_document(args.input, replace(preset, colour_trace=True))
        finally:
            line_effects.trace_indices = current
        new = converter.convert_document(args.input, replace(preset, colour_trace=True))
        for suffix, document in (("black", plain), ("old_trace", old), ("new_trace", new)):
            document.image.save(args.output / f"{preset.machine}_{suffix}.png")
        panel([plain.image, old.image, new.image], [f"{preset.machine} black", "Previous trace", "New selective trace"],
              args.output / f"{preset.machine}_trace_comparison.png")
        baseline = np.asarray(plain.image.convert("RGB"))
        record = {"machine": preset.machine, "size": plain.image.size,
                  "old_trace_changed_pixels": int(np.any(baseline != np.asarray(old.image.convert("RGB")), axis=2).sum()),
                  "new_trace_changed_pixels": int(np.any(baseline != np.asarray(new.image.convert("RGB")), axis=2).sum()),
                  "seconds_three_conversions": round(time.perf_counter() - start, 3)}
        records.append(record)
        print(json.dumps(record), flush=True)
        if preset.machine == "anime":
            palette = converter.palette_for("anime", preset.palette_id).array()
            ink_id = next(index for index, name in plain.names.items() if name.startswith("Lines"))
            ink = plain.labels == ink_id
            indices = plain.labels.copy()
            darkest = int(np.argmin(palette @ np.array((.2126, .7152, .0722))))
            indices[ink] = darkest
            pool = np.unique(indices)
            opacity = plain.image.convert("RGBA").getchannel("A")
            visible = np.asarray(opacity) > 0
            images, labels = [], []
            for growth in (0, .25, .5, .75, 1):
                hard = line_effects.expand_ink(ink, growth, visible)
                expanded = indices.copy()
                expanded[hard] = darkest
                painted, _ = line_effects.fractional_growth(expanded, hard, growth, palette, pool, visible)
                image = Image.fromarray(palette[painted], "RGB")
                image.putalpha(opacity)
                image.save(args.output / f"ink_growth_{growth:g}.png")
                # A 1:1 crop exposes individual line edge pixels in the comparison.
                width, height = image.size
                crop = image.crop((int(width * .25), int(height * .18),
                                   int(width * .62), int(height * .55)))
                images.append(crop)
                labels.append(f"Growth {growth:g} / same paint")
            panel(images, labels, args.output / "ink_growth_comparison.png")
    (args.output / "trace_validation.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
