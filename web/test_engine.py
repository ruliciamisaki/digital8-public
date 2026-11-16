"""Check the web adapter against the shared converter and export readers."""
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "distribution" / "Digital8_Anime"))
import engine
import digital8_converter as converter
import png_settings


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.job = engine.Job()
        self.addCleanup(self.job.directory.cleanup)
        self.addCleanup(lambda: getattr(self.job, "source_folder", self.job.directory).cleanup())
        self.folder = Path(self.directory.name)
        self.source = self.folder / "test.png"
        image = Image.new("RGBA", (40, 25))
        image.putdata([((x * 13) % 256, (y * 17) % 256, ((x + y) * 11) % 256, (x * 27) % 256)
                       for y in range(25) for x in range(40)])
        image.save(self.source)
        self.original = self.source.read_bytes()
        self.job.open(self.source, "test.png")

    def test_modes_equal_desktop_and_png_retains_settings(self):
        output = self.folder / "output.png"
        for machine, settings in engine.PRESETS.items():
            with self.subTest(machine=machine):
                desktop = converter.convert_document(self.job.source, settings)
                self.job.save(asdict(settings), "png", output, True, True)
                with Image.open(output) as image:
                    self.assertEqual(image.convert("RGBA").tobytes(), desktop.image.convert("RGBA").tobytes())
                recipe = png_settings.read_recipe(output)
                self.assertEqual(png_settings.decode_settings(recipe), settings)
                self.assertEqual(recipe["export"], {"alpha_mask": True, "invert_alpha_mask": True})
        restored = self.job.open(output, "output.png")
        self.assertEqual(restored["settings"]["machine"], "anime")
        self.source.write_bytes(self.original)
        self.job.open(self.source, "test.png", restored["original"])

    def test_layered_exports_and_mask(self):
        options = asdict(engine.PRESETS["anime"])
        for kind in ("psd", "zip", "mask"):
            output = self.folder / kind
            self.job.save(options, kind, output, invert=True)
            self.assertGreater(output.stat().st_size, 0)
            if kind == "psd":
                self.assertTrue(output.read_bytes().startswith(b"8BPS"))
            elif kind == "zip":
                with ZipFile(output) as archive:
                    self.assertIsNone(archive.testzip())
            elif kind == "mask":
                with Image.open(output) as image:
                    self.assertEqual(image.size, (40, 25))


if __name__ == "__main__":
    unittest.main()
