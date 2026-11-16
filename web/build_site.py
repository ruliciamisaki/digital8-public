"""Stage the static website and package its shared Python converter."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import argparse
import json
import shutil
import sys


WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
DESKTOP = ROOT / "distribution" / "Digital8_Anime"
MODULES = (
    "anime_paint.py", "digital8_converter.py", "image_processing.py", "layer_export.py",
    "line_effects.py", "pc8801_digital8_v4.py", "pc9801_16_v4.py", "png_settings.py",
    "region_kernel.py", "retro_global.py", "tile_patterns.py", "pc9801_palettes.json",
)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("output", type=Path, nargs="?", default=ROOT / "_site")
    output = cli.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for filename in ("index.html", "style.css", "app.js", "worker.js"):
        shutil.copyfile(WEB / filename, output / filename)
    shutil.copyfile(ROOT / "LICENSE", output / "LICENSE")
    with ZipFile(output / "engine.zip", "w", ZIP_DEFLATED) as archive:
        archive.write(WEB / "engine.py", "engine.py")
        for filename in MODULES:
            archive.write(DESKTOP / filename, filename)
        for path in sorted((DESKTOP / "palettes").glob("*.json")):
            archive.write(path, path.relative_to(DESKTOP).as_posix())
    sys.path.insert(0, str(DESKTOP))
    from engine import catalog
    (output / "catalog.json").write_text(json.dumps(catalog(), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
