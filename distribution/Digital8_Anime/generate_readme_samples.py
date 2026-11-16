"""Generate the curated private-repository gallery at the documented settings."""

import argparse
import hashlib
import json
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from PIL import Image

import digital8_converter as converter


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_result(source: Path, target: Path, options: converter.ConversionOptions) -> dict:
    started = time.perf_counter()
    result, _alpha = converter.convert_image(source, options)
    elapsed = time.perf_counter() - started
    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target, optimize=True)
    rgba = np.asarray(result.convert("RGBA"))
    colour_count = len(np.unique(rgba[:, :, :3][rgba[:, :, 3] > 0], axis=0))
    if colour_count > options.anime_max_colours:
        raise AssertionError(f"Colour budget exceeded: {colour_count}")
    return {"size": list(result.size), "visible_colours": colour_count,
            "conversion_seconds": round(elapsed, 3), "sha256": digest(target)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=converter.ROOT / "output_sample/anime")
    args = parser.parse_args()
    if len({path.stem for path in args.inputs}) != len(args.inputs):
        parser.error("Input names must be unique")
    args.output.mkdir(parents=True, exist_ok=True)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=converter.ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    base = replace(converter.anime_standard(), palette_id="te_saturated", line_growth=.5,
                   anime_max_colours=24, resize_640=False, colour_trace=False)
    manifest = {"schema_version": 1, "converter_revision": revision,
                "settings": asdict(base), "tolerances": [128, 64], "samples": []}
    for number, source in enumerate(args.inputs, 1):
        with Image.open(source) as opened:
            original = opened.convert("RGBA" if "A" in opened.getbands() or "transparency" in opened.info else "RGB")
        # Gallery inputs preserve pixels and alpha without copying generation
        # prompts/EXIF into the repository's image files.
        original.info.clear()
        source_file = args.output / "source" / f"{source.stem}.png"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        original.save(source_file, optimize=True)
        item = {"name": source.name, "source": source_file.relative_to(args.output).as_posix(),
                "provided_sha256": digest(source), "source_sha256": digest(source_file),
                "size": list(original.size), "outputs": {}}
        for tolerance in (128, 64):
            output = args.output / f"tol{tolerance}" / f"{source.stem}.png"
            report = save_result(source, output, replace(base, anime_tolerance=tolerance))
            report["file"] = output.relative_to(args.output).as_posix()
            item["outputs"][str(tolerance)] = report
            print(f"{number}/{len(args.inputs)} {source.name} tolerance={tolerance}: "
                  f"{report['conversion_seconds']} s / {report['visible_colours']} colours", flush=True)
        manifest["samples"].append(item)
        (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source = args.inputs[0]
    output = args.output / "trace" / f"{source.stem}_tol128_trace.png"
    report = save_result(source, output, replace(base, anime_tolerance=128, colour_trace=True))
    manifest["trace_comparison"] = {
        "sample": source.name, "tolerance": 128, "trace_strength": base.trace_strength,
        "without_trace": manifest["samples"][0]["outputs"]["128"]["file"],
        "with_trace": output.relative_to(args.output).as_posix(), **report,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Trace comparison: {report['conversion_seconds']} s / {report['visible_colours']} colours", flush=True)


if __name__ == "__main__":
    main()
