"""In-browser adapter for the desktop converter, using the worker's virtual filesystem."""
from dataclasses import asdict
from pathlib import Path
import json
import os
import tempfile

from PIL import Image

import digital8_converter as converter
import image_processing
import png_settings
from layer_export import write_psd, write_zip


PRESETS = {
    "pc8801": converter.pc8801_standard(),
    "pc9801": converter.pc9801_standard(),
    "anime": converter.anime_standard(),
}


def catalog():
    return {
        "presets": {name: asdict(options) for name, options in PRESETS.items()},
        "palettes": {name: [asdict(palette) for palette in converter.palettes_for(name)]
                     for name in PRESETS},
    }


class Job:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.source = None
        self.document = None
        self.options = None

    def open(self, incoming, name, original=None):
        name = Path(name.replace("\\", "/")).name
        with Image.open(incoming) as image:
            width, height = image.size
            if width * height > 24_000_000:
                raise ValueError("入力は2400万画素以下にしてください。")
            image.verify()
        if original and (name != original["name"] or png_settings.file_digest(incoming) != original["sha256"]):
            raise ValueError("PNGに記録された元画像と一致しません。")
        recipe = None if original else png_settings.read_recipe(incoming)
        settings = png_settings.decode_settings(recipe) if recipe else None
        new_folder = tempfile.TemporaryDirectory(dir=self.folder)
        target = Path(new_folder.name) / name
        os.replace(incoming, target)
        if self.source:
            self.source_folder.cleanup()
        self.source_folder = new_folder
        self.source = target
        self.document = self.options = None
        return {
            "width": width, "height": height,
            "settings": asdict(settings) if settings else None,
            "export": recipe.get("export", {}) if recipe else {},
            "original": recipe.get("source") if recipe else None,
        }

    def render(self, values):
        if self.source is None:
            raise ValueError("入力画像を選んでください。")
        options = converter.ConversionOptions(**values)
        if options != self.options:
            self.document = converter.convert_document(self.source, options)
            self.options = options
        return self.document

    def save(self, values, kind, output, mask=False, invert=False):
        document = self.render(values)
        if kind == "psd":
            write_psd(output, document)
        elif kind == "zip":
            write_zip(output, document)
        elif kind in ("preview", "png"):
            info = None
            if kind == "png":
                info = png_settings.png_info(self.source, self.options,
                                             converter.resolved_palette(self.options), mask, invert)
            document.image.save(output, format="PNG", pnginfo=info)
        elif kind == "mask":
            alpha = document.alpha
            if alpha is None:
                alpha = Image.new("L", document.image.size, 255)
            image_processing.alpha_mask_image(alpha, invert).save(output, format="PNG")
        else:
            raise ValueError("出力形式を選んでください。")
        return {"width": document.image.width, "height": document.image.height}


def dispatch(request, job):
    values = json.loads(request)
    if values.pop("action") == "open":
        result = job.open(**values)
    else:
        result = job.save(**values)
    return json.dumps(result, ensure_ascii=False)
