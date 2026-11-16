"""Sample the user's TE/CW chart layouts, retaining coordinates and hashes."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def extract(path: Path, kind: str) -> dict:
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    rgb = np.asarray(image)
    expected = (640, 504) if kind == "TE" else (1000, 2062)
    if image.size != expected:
        raise ValueError(f"{kind}: expected original chart size {expected}; got {image.size}")
    entries = []
    rows = range(8) if kind == "TE" else range(40)
    for row in rows:
        columns = range(8 if row == 7 else 13) if kind == "TE" else range(18)
        for column in columns:
            if kind == "TE":
                x = round((column + .5) * 640 / 13)
                y = 25 + row * 63
                bounds = [round(column * 640 / 13), row * 63,
                          round((column + 1) * 640 / 13), row * 63 + 51]
            else:
                x = 49 + column * 54
                top = 16 + row * 50 if row < 4 else 237 + (row - 4) * 50
                y = top + 15
                bounds = [x - 24, top, x + 25, top + 30]
            patch = rgb[y - 3:y + 4, x - 3:x + 4]
            colour = np.rint(np.median(patch, axis=(0, 1))).astype(int)
            if kind == "CW" and np.max(np.abs(colour - 206)) <= 4:
                continue  # Empty chart cells use a neutral gray background.
            entries.append({"id": f"{kind}-R{row + 1:02d}C{column + 1:02d}",
                            "row": row + 1, "column": column + 1,
                            "rgb": colour.tolist(), "centre": [x, y],
                            "sample_box": [x - 3, y - 3, x + 4, y + 4], "swatch_box": bounds})
    if kind == "TE":
        entries.append({"id": "TE-WHITE", "rgb": [255, 255, 255],
                        "note": "User-specified white / unpainted; not sampled from chart"})
    return {"schema_version": 1, "name": f"カラー{kind}", "id": kind.lower(),
            "source_file": path.name, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_size": list(image.size), "sampling": "7x7 centre patch, per-channel median in source RGB; no colour calibration",
            "identifiers": "Position IDs, not guessed readings of the small printed paint codes",
            "colours": [entry["rgb"] for entry in entries], "entries": entries}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("te", type=Path)
    parser.add_argument("cw", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("palettes"))
    parser.add_argument("--proof", type=Path, default=Path(__file__).with_name("preview") / "charts")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.proof.mkdir(parents=True, exist_ok=True)
    for kind, path in (("TE", args.te), ("CW", args.cw)):
        data = extract(path, kind)
        (args.output / f"colour_{kind.lower()}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with Image.open(path) as opened:
            proof = opened.convert("RGB")
        draw = ImageDraw.Draw(proof)
        for entry in data["entries"]:
            if "sample_box" in entry:
                box = entry["sample_box"]
                draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), outline="white")
                draw.rectangle((box[0] - 1, box[1] - 1, box[2], box[3]), outline="black")
        proof.save(args.proof / f"{kind}_sampling.png")
        swatches = Image.new("RGB", (800, ((len(data['entries']) + 9) // 10) * 48), "white")
        draw = ImageDraw.Draw(swatches)
        for index, entry in enumerate(data["entries"]):
            x, y = (index % 10) * 80, (index // 10) * 48
            draw.rectangle((x + 2, y, x + 77, y + 27), fill=tuple(entry["rgb"]))
            draw.text((x + 2, y + 29), entry["id"], fill="black")
        swatches.save(args.proof / f"{kind}_database.png")
        print(f"{kind}: {len(data['entries'])} colours")


if __name__ == "__main__":
    main()
