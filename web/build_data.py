"""Export the adopted Python converter's fixed tables for the browser runtime.

Run from the repository root. This is a development tool; Pages never runs Python.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "distribution" / "Digital8_V2_V4"))
import digital8_unified as converter  # noqa: E402


def main() -> None:
    pc88 = converter.pc88_frequent
    reference88 = pc88.load_reference()
    reference98 = converter.pc98_two_colour.load_reference()
    data = {
        "pc88_palettes": [
            {"id": p.id, "label": p.label, "colours": p.colours}
            for p in converter.palettes_for("pc8801")
        ],
        "pc98_palettes": [
            {"id": p.id, "label": p.label, "colours": p.colours}
            for p in converter.palettes_for("pc9801")
        ],
        "pc88_v2": {},
        "pc98_masks": {},
        "pc88_observed_pairs": sorted({
            tuple(sorted(int(c) for c in pattern["colours"]))
            for pattern in reference88["patterns"] if len(pattern["colours"]) == 2
        }),
    }
    for mode in converter.pc88_base.PATTERN_MODES:
        recipes = pc88.build_recipes(mode, reference88)
        data["pc88_v2"][mode] = [
            {"family": r.family, "tile": r.tile, "height": r.shape[0],
             "width": r.shape[1], "penalty": r.penalty}
            for r in recipes
        ]
    for pattern in reference98["patterns"]:
        density = str(pattern["coverage"])
        if density not in data["pc98_masks"]:
            data["pc98_masks"][density] = pattern["bits"]
    target = ROOT / "web" / "converter-data.json"
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{target.name}: {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
