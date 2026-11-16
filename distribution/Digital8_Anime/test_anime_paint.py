import json
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import anime_paint as core
import digital8_converter as converter
import digital8_gui as gui
from palette_editor import PaletteEditor


class RegionTests(unittest.TestCase):
    def test_nearby_noise_becomes_one_solid_area(self):
        rng = np.random.default_rng(3)
        rgb = np.clip(np.array((190, 150, 110)) + rng.integers(-5, 6, (28, 36, 3)), 0, 255).astype(np.uint8)
        palette = np.array(((190, 150, 110), (193, 153, 113), (187, 147, 107)), np.uint8)
        result = core.paint(rgb, np.zeros(rgb.shape[:2], bool), palette, tolerance=24)
        self.assertEqual(np.unique(result).tolist(), [0])

    def test_strong_colour_boundary_and_small_highlight_survive(self):
        rgb = np.full((24, 30, 3), (210, 90, 80), np.uint8)
        rgb[:, 15:] = (70, 120, 215)
        rgb[6:8, 6:8] = (255, 255, 255)
        palette = np.array(((210, 90, 80), (70, 120, 215), (255, 255, 255)), np.uint8)
        result = core.paint(rgb, np.zeros(rgb.shape[:2], bool), palette, tolerance=40, min_area=64)
        np.testing.assert_array_equal(result[:, 15:], 1)
        np.testing.assert_array_equal(result[6:8, 6:8], 2)
        self.assertEqual(result[12, 8], 0)

    def test_ink_wall_blocks_smoothing_and_region_merging(self):
        rgb = np.full((15, 21, 3), (180, 120, 80), np.uint8)
        rgb[:, 11:] = (200, 140, 100)
        rgb[:, 10] = 0
        ink = np.zeros((15, 21), bool)
        ink[:, 10] = True
        palette = np.array(((0, 0, 0), (180, 120, 80), (200, 140, 100)), np.uint8)
        result = core.paint(rgb, ink, palette, tolerance=64, radius=8)
        np.testing.assert_array_equal(result[:, :10], 1)
        np.testing.assert_array_equal(result[:, 10], 0)
        np.testing.assert_array_equal(result[:, 11:], 2)

    def test_transparent_hidden_rgb_does_not_change_visible_colour(self):
        first = np.full((14, 20, 3), (180, 120, 80), np.uint8)
        second = first.copy()
        first[:, 10] = (255, 0, 0)
        second[:, 10] = (0, 255, 255)
        coverage = np.ones((14, 20), np.float32)
        coverage[:, 10] = 0
        palette = np.array(((180, 120, 80), (200, 130, 100)), np.uint8)
        a = core.paint(first, np.zeros(coverage.shape, bool), palette, coverage, tolerance=100)
        b = core.paint(second, np.zeros(coverage.shape, bool), palette, coverage, tolerance=100)
        np.testing.assert_array_equal(a, b)

    def test_gradient_does_not_chain_into_one_region(self):
        rgb = np.repeat(np.arange(32, 224, 3, dtype=np.float32)[None, :, None], 3, axis=2)
        labels = core.find_regions(rgb, np.ones(rgb.shape[:2], bool), 24, min_area=0)
        self.assertGreater(len(np.unique(labels)), 4)
        for label in np.unique(labels):
            colours = rgb[labels == label]
            self.assertLessEqual(float((colours.max(axis=0) - colours.min(axis=0)).max()), 24)

    def test_increasing_tolerance_combines_a_nearby_two_tone_area(self):
        rgb = np.full((12, 16, 3), (160, 120, 80), np.uint8)
        rgb[:, 8:] += 20
        valid = np.ones(rgb.shape[:2], bool)
        self.assertEqual(len(np.unique(core.find_regions(rgb, valid, 10, 0))), 2)
        self.assertEqual(len(np.unique(core.find_regions(rgb, valid, 32, 0))), 1)

    def test_weighted_original_area_mean_selects_palette_colour(self):
        rgb = np.array([[(200, 120, 80), (220, 140, 100)]], np.uint8)
        palette = np.array(((202, 122, 82), (210, 130, 90), (220, 140, 100)), np.uint8)
        result = core.paint(rgb, np.zeros((1, 2), bool), palette,
                            np.array(((1, .1),)), tolerance=40, radius=0)
        np.testing.assert_array_equal(result, 0)

    def test_empty_thin_and_single_colour_cases(self):
        palette = np.array(((80, 40, 20),), np.uint8)
        for shape in ((1, 1), (1, 20), (20, 1), (4, 5)):
            rgb = np.full((*shape, 3), 140, np.uint8)
            for ink in (np.zeros(shape, bool), np.ones(shape, bool)):
                result = core.paint(rgb, ink, palette, np.zeros(shape), tolerance=0)
                self.assertEqual(result.shape, shape)
                np.testing.assert_array_equal(result, 0)


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "source.png"
        yy, xx = np.indices((25, 31))
        rgb = np.stack((120 + xx * 3, 90 + yy * 4, 60 + xx * 2), axis=2).astype(np.uint8)
        self.alpha = np.where(xx < 4, 0, np.where(xx < 10, 92, 255)).astype(np.uint8)
        Image.fromarray(np.dstack((rgb, self.alpha)), "RGBA").save(self.source)

    def test_palette_round_trip_validation_and_default(self):
        palette = core.default_palette()
        self.assertEqual(len(palette), 120)
        self.assertEqual(len(set(palette)), 120)
        path = self.folder / "palette.json"
        core.save_palette(path, palette)
        self.assertEqual(core.load_palette(path), palette)
        path.write_text(json.dumps({"colors": [[1, 2, 3]]}), encoding="utf-8")
        self.assertEqual(core.load_palette(path), ((1, 2, 3),))
        for bad in ([], [[1, 2]], [[256, 0, 0]], [[1.5, 0, 0]], [[True, 0, 0]], [[0, 0, 0]] * 4097):
            with self.assertRaises(ValueError):
                core.validate_palette(bad)

    def test_custom_palette_exact_colours_and_preserved_alpha(self):
        palette = ((0, 0, 0), (180, 140, 100), (230, 180, 130))
        result, alpha = converter.convert_image(self.source, replace(converter.anime_standard(), anime_palette=palette))
        np.testing.assert_array_equal(result.getchannel("A"), self.alpha)
        np.testing.assert_array_equal(alpha, self.alpha)
        self.assertTrue(set(result.convert("RGB").getdata()) <= set(palette))

    def test_binary_alpha_odd_height_virtual_pixels_and_mask(self):
        for tall in (False, True):
            options = replace(converter.anime_standard(), alpha_mode="binary", tall_dots=tall, pixel_size=3)
            result, alpha = converter.convert_image(self.source, options)
            self.assertEqual(result.size, (31, 25))
            self.assertTrue(set(alpha.getdata()) <= {0, 255})
            np.testing.assert_array_equal(result.getchannel("A"), alpha)
            if tall:
                pixels = np.asarray(result)
                np.testing.assert_array_equal(pixels[0:24:2], pixels[1:24:2])
        output, mask = self.folder / "out.png", self.folder / "mask.png"
        converter.convert(self.source, output, converter.anime_standard(), mask, True)
        with Image.open(mask) as loaded:
            np.testing.assert_array_equal(np.asarray(loaded)[:, :, 0], 255 - self.alpha)

    def test_rgba_resizing_ignores_hidden_rgb(self):
        values = np.array(Image.open(self.source))
        options = replace(converter.anime_standard(), pixel_size=3)
        first, _ = converter.convert_image(self.source, options)
        values[self.alpha == 0, :3] = (255, 0, 255)
        Image.fromarray(values, "RGBA").save(self.source)
        second, _ = converter.convert_image(self.source, options)
        visible = self.alpha > 0
        np.testing.assert_array_equal(np.asarray(first)[visible], np.asarray(second)[visible])

    def test_invalid_settings_fail_before_work(self):
        for changes in ({"anime_tolerance": -1}, {"anime_tolerance": float("nan")},
                        {"anime_radius": 9}, {"anime_min_area": -1}, {"anime_palette": ()},
                        {"alpha_mode": "wrong"}, {"pixel_size": 0}):
            with self.assertRaises(ValueError):
                converter.convert_image(self.source, replace(converter.anime_standard(), **changes))

    def test_cli_anime_and_custom_palette(self):
        palette_path = self.folder / "custom.json"
        core.save_palette(palette_path, ((0, 0, 0), (180, 140, 100)))
        destination = self.folder / "cli.png"
        completed = subprocess.run([sys.executable, str(Path(converter.__file__)), str(self.source),
                                    str(destination), "--machine", "anime", "--palette-file", str(palette_path),
                                    "--anime-tolerance", "48"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with Image.open(destination) as image:
            self.assertEqual(image.size, (31, 25))
            self.assertTrue(set(image.convert("RGB").getdata()) <= {(0, 0, 0), (180, 140, 100)})

    def test_retro_presets_use_v4_tiles_on_native_grid(self):
        for preset in (converter.pc8801_standard(), converter.pc9801_standard()):
            for tall in (False, True):
                with self.subTest(machine=preset.machine, tall=tall):
                    options = replace(preset, tall_dots=tall, resize_640=False)
                    doc = converter.convert_document(self.source, options)
                    colours = set(converter.resolved_palette(options))
                    self.assertLessEqual(set(doc.image.convert('RGB').getdata()), colours)
                    self.assertEqual(doc.image.size, (31, 25))
                    if tall:
                        rgb = np.asarray(doc.image.convert('RGB'))
                        np.testing.assert_array_equal(rgb[0:-1:2], rgb[1::2])


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.root = gui.TkinterDnD.Tk() if gui.HAS_FILE_DROP else tk.Tk()
        self.root.withdraw()
        self.addCleanup(lambda: (self.root.update_idletasks(), self.root.destroy()))
        self.app = gui.App(self.root)

    def test_switch_machines_and_keep_custom_palette(self):
        self.assertEqual(self.app._settings().machine, "anime")
        self.assertEqual(self.app.notebook.select(), str(self.app.anime_frame))
        self.app.set_anime_palette(((0, 0, 0), (255, 80, 40)))
        self.assertEqual(self.app._settings().anime_palette, ((0, 0, 0), (255, 80, 40)))
        for preset in (converter.pc8801_standard(), converter.pc9801_standard()):
            self.app.apply_preset(preset)
            self.assertEqual(self.app._settings(), preset)
            self.assertEqual(self.app.notebook.tab(self.app.anime_frame, "state"), "hidden")
        self.app.apply_preset(converter.anime_standard())
        self.app.palette_label.set("02  Custom")
        self.assertEqual(self.app._settings().anime_palette, ((0, 0, 0), (255, 80, 40)))
        self.assertLessEqual(self.app.palette_canvas.winfo_reqwidth(), 400)

    def test_editor_changes_only_apply_on_commit(self):
        original = ((0, 0, 0), (255, 255, 255))
        commit = unittest.mock.Mock()
        editor = PaletteEditor(self.root, original, commit)
        editor.hex_value.set("#123456")
        editor.apply_hex()
        commit.assert_not_called()
        editor.add()
        editor.remove()
        editor.apply()
        commit.assert_called_once_with(((18, 52, 86), (255, 255, 255)))

    def test_editor_cancel_does_not_change_palette(self):
        commit = unittest.mock.Mock()
        editor = PaletteEditor(self.root, ((0, 0, 0),), commit)
        editor.add()
        editor.destroy()
        commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
