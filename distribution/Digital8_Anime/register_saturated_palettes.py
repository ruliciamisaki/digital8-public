"""Register the user's edited database swatch sheets without changing base RGB."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parent


def extract_preview(path: Path, original: dict, kind: str) -> dict:
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    entries = original["entries"]
    expected = (800, ((len(entries) + 9) // 10) * 48)
    if image.size != expected:
        raise ValueError(f"{kind}: expected database sheet {expected}; got {image.size}")
    pixels = np.asarray(image)
    sampled = []
    for index, entry in enumerate(entries):
        x, y = (index % 10) * 80 + 40, (index // 10) * 48 + 13
        box = [x - 3, y - 3, x + 4, y + 4]
        rgb = np.rint(np.median(pixels[y - 3:y + 4, x - 3:x + 4], axis=(0, 1))).astype(int).tolist()
        sampled.append({"id": entry["id"], "rgb": rgb, "centre": [x, y], "sample_box": box,
                        "swatch_box": [(index % 10) * 80 + 2, (index // 10) * 48,
                                       (index % 10) * 80 + 78, (index // 10) * 48 + 28]})
    return {"schema_version": 1, "name": f"カラー{kind} 彩度上げ", "id": f"{kind.lower()}_saturated",
            "base_palette": original["id"], "source_file": path.name,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "source_size": list(image.size),
            "sampling": "7x7 centre patch median from the user's saturation-edited database preview; not an estimated saturation multiplier",
            "identifiers": "Original chart position IDs retained in the same order",
            "colours": [entry["rgb"] for entry in sampled], "entries": sampled}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("te", type=Path, nargs="?", default=ROOT / "preview/charts/TE_database_彩度上げ.png")
    parser.add_argument("cw", type=Path, nargs="?", default=ROOT / "preview/charts/CW_database_彩度上げ.png")
    args = parser.parse_args()
    for kind, source in (("TE", args.te), ("CW", args.cw)):
        base = ROOT / "palettes" / f"colour_{kind.lower()}.json"
        original = json.loads(base.read_text(encoding="utf-8"))
        data = extract_preview(source, original, kind)
        output = base.with_name(f"colour_{kind.lower()}_saturated.json")
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{kind}: {len(data['colours'])} colours; changed {sum(a != b for a, b in zip(data['colours'], original['colours']))}")


if __name__ == "__main__":
    main()
