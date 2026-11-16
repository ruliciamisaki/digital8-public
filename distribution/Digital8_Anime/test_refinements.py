import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import digital8_gui as gui
import digital8_converter as converter
import line_effects
from preview_window import PreviewWindow


class RefinedLineTests(unittest.TestCase):
    def test_fractional_steps_are_intermediate_and_stay_in_palette(self):
        palette = np.repeat(np.array((0, 51, 102, 153, 204, 255), np.uint8)[:, None], 3, axis=1)
        ink = np.zeros((13, 19), bool)
        ink[:, 9] = True
        original = np.full(ink.shape, 5, np.uint8)
        original[ink] = 0
        values = []
        for amount in (0, .25, .5, .75, 1):
            grown = line_effects.expand_ink(ink, amount)
            indices = original.copy()
            indices[grown] = 0
            result, changed = line_effects.fractional_growth(indices, grown, amount, palette, np.arange(6))
            values.append(int(palette[result[6, 10], 0]))
            self.assertTrue(set(np.unique(result)) <= set(range(6)))
            self.assertTrue(np.all(result[ink] == 0))
        self.assertEqual(values[0], 255)
        self.assertEqual(values[-1], 0)
        self.assertTrue(all(a >= b for a, b in zip(values, values[1:])))
        self.assertGreaterEqual(len(set(values[1:-1])), 2)

    def test_fractional_growth_preserves_transparent_gaps_and_does_not_wrap(self):
        ink = np.zeros((9, 15), bool)
        ink[:, 0] = True
        indices = np.full(ink.shape, 2, np.uint8)
        indices[ink] = 0
        palette = np.array(((0, 0, 0), (128, 128, 128), (255, 255, 255)), np.uint8)
        visible = np.ones(ink.shape, bool)
        visible[:, 1] = False
        result, changed = line_effects.fractional_growth(indices, ink, .75, palette,
                                                       np.arange(3), visible)
        np.testing.assert_array_equal(result[:, 1], indices[:, 1])
        np.testing.assert_array_equal(result[:, -1], indices[:, -1])
        self.assertFalse(changed[:, 1].any())
        for invalid in (-.1, 16.1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                line_effects.expand_ink(ink, invalid)

    def test_fractional_gray_edge_does_not_become_red_in_digital_8(self):
        palette = converter.palette_for("pc8801", "digital8").array()
        white = int(np.argmax(palette.sum(axis=1)))
        black = int(np.argmin(palette.sum(axis=1)))
        ink = np.zeros((11, 17), bool)
        ink[:, 8] = True
        indices = np.full(ink.shape, white, np.uint8)
        indices[ink] = black
        for amount in (.25, .5, .75):
            result, _ = line_effects.fractional_growth(indices, ink, amount, palette, np.arange(len(palette)))
            self.assertTrue(set(np.unique(result)) <= {white, black})

    def test_dark_space_and_edges_around_bright_stars_are_not_coloured(self):
        palette = np.array(((0, 0, 0), (255, 255, 255), (120, 120, 120)), np.uint8)
        indices = np.zeros((70, 90), np.uint8)
        indices[10:13, 10:13] = 1
        indices[55:58, 74:77] = 1
        indices[25:45, 30:55] = 1
        ink = indices == 0
        result = line_effects.trace_indices(indices, ink, palette, permitted=np.arange(3))
        np.testing.assert_array_equal(result, indices)

    def test_thin_lines_are_coloured_but_large_black_shapes_stay_black(self):
        palette = np.array(((0, 0, 0), (200, 140, 100), (100, 60, 40)), np.uint8)
        indices = np.full((24, 32), 1, np.uint8)
        indices[:, 7] = 0
        indices[5:20, 16:31] = 0
        ink = indices == 0
        result = line_effects.trace_indices(indices, ink, palette, permitted=np.arange(3))
        self.assertTrue(np.all(result[:, 7] == 2))
        np.testing.assert_array_equal(result[5:20, 16:31], 0)
        np.testing.assert_array_equal(result[~ink], indices[~ink])

    def test_digital_8_does_not_replace_green_line_with_an_unrelated_hue(self):
        palette = converter.palette_for("pc8801", "digital8").array()
        green = int(np.argmax(palette[:, 1].astype(int) - palette[:, 0] - palette[:, 2]))
        black = int(np.argmin(palette.sum(axis=1)))
        indices = np.full((16, 23), green, np.uint8)
        ink = np.zeros(indices.shape, bool)
        ink[:, 11] = True
        indices[ink] = black
        result = line_effects.trace_indices(indices, ink, palette, permitted=np.arange(len(palette)))
        np.testing.assert_array_equal(result, indices)

    def test_trace_uses_tile_average_independent_of_dot_phase(self):
        palette = np.array(((0, 0, 0), (240, 180, 140), (160, 100, 60), (90, 50, 30)), np.uint8)
        yy, xx = np.indices((20, 29))
        ink = np.zeros((20, 29), bool)
        ink[:, 14] = True
        references = np.array(((200, 140, 100),), np.float32)
        results = []
        for phase in (0, 1):
            indices = np.where((xx + yy + phase) % 2, 1, 2).astype(np.uint8)
            indices[ink] = 0
            result = line_effects.trace_indices(indices, ink, palette, permitted=np.arange(4),
                                                reference_ids=np.zeros(ink.shape, np.int32),
                                                reference_colours=references)
            results.append(result[ink])
            self.assertTrue(np.all(result[ink] != 0))
        np.testing.assert_array_equal(results[0], results[1])
        self.assertEqual(len(np.unique(results[0])), 1)


class DatabaseTests(unittest.TestCase):
    def test_user_saturated_sheets_are_separate_and_keep_position_ids(self):
        palettes = converter.palettes_for("anime")
        self.assertTrue({"te", "cw", "te_saturated", "cw_saturated"} <= {p.id for p in palettes})
        for kind, count in (("te", 100), ("cw", 563)):
            original = json.loads((converter.ROOT / "palettes" / f"colour_{kind}.json").read_text(encoding="utf-8"))
            enhanced = json.loads((converter.ROOT / "palettes" / f"colour_{kind}_saturated.json").read_text(encoding="utf-8"))
            self.assertEqual(len(enhanced["colours"]), count)
            self.assertEqual([e["id"] for e in enhanced["entries"]], [e["id"] for e in original["entries"]])
            self.assertNotEqual(enhanced["colours"], original["colours"])
            self.assertEqual(enhanced["base_palette"], kind)
        self.assertEqual(converter.palette_for("anime", "te_saturated").colours[-1], (255, 255, 255))

    def test_requested_anime_defaults(self):
        options = converter.anime_standard()
        self.assertEqual(options.anime_tolerance, 128)
        self.assertEqual(options.anime_max_colours, 24)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source.png"
        rgb = np.full((30, 47, 4), (210, 150, 100, 255), np.uint8)
        rgb[:, 22:24, :3] = 0
        rgb[:, :3, 3] = 0
        rgb[3:15, 30:43, :3] = (130, 85, 55)
        Image.fromarray(rgb, "RGBA").save(self.source)

    def test_fractional_ink_and_trace_recompose_layers_and_keep_alpha(self):
        for preset in (converter.anime_standard(), converter.pc8801_standard(), converter.pc9801_standard()):
            options = replace(preset, resize_640=False, line_growth=.5, colour_trace=True)
            document = converter.convert_document(self.source, options)
            composite = Image.new("RGBA", document.image.size)
            for index in document.layer_ids():
                layer, _offset = document.layer(index)
                composite = Image.alpha_composite(composite, layer)
            visible = np.asarray(document.image.convert("RGBA"))[:, :, 3] > 0
            np.testing.assert_array_equal(np.asarray(composite)[visible], np.asarray(document.image.convert("RGBA"))[visible])
            np.testing.assert_array_equal(composite.getchannel("A"), document.image.getchannel("A"))
            if preset.machine == "anime":
                self.assertLessEqual(len(document.image.convert("RGB").getcolors(1000000)), 24)

    def test_trace_toggle_changes_only_source_ink_not_the_fill_palette(self):
        options = replace(converter.anime_standard(), palette_id="cw_saturated", line_growth=0)
        plain = converter.convert_document(self.source, options)
        traced = converter.convert_document(self.source, replace(options, colour_trace=True))
        ink_id = next(index for index, name in plain.names.items() if name.startswith("Lines"))
        nonink = plain.labels != ink_id
        np.testing.assert_array_equal(np.asarray(plain.image)[nonink], np.asarray(traced.image)[nonink])


class PreviewTests(unittest.TestCase):
    def test_auto_preview_coalesces_changes_and_preserves_the_same_window_and_position(self):
        root = gui.TkinterDnD.Tk() if gui.HAS_FILE_DROP else gui.tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            Image.new("RGB", (100, 80), "white").save(path)
            app = gui.App(root)
            app.set_input(path)
            started, release = threading.Event(), threading.Event()
            calls = []

            def convert(_path, settings):
                calls.append((settings.machine, settings.palette_id, settings.pixel_size))
                if settings.pixel_size == 2:
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError("Preview test did not release the worker")
                return Image.new("RGB", (settings.pixel_size * 50, 80), "white"), None

            def pump_until(condition, timeout=4):
                deadline = time.monotonic() + timeout
                while not condition() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertTrue(condition())

            with patch.object(converter, "convert_image", side_effect=convert):
                app.start_preview()
                pump_until(lambda: app._preview_window is not None)
                window = app._preview_window
                window.geometry("350x240+150+180")
                root.update()
                geometry = window.winfo_geometry()
                window.zoom.set("50%")
                window.redraw()

                app.auto_preview.set(True)
                app.pixel_size.set(2)
                pump_until(started.is_set)
                app.apply_preset(converter.pc9801_standard())
                app.palette_label.set(next(item.label for item in app.palette_map.values() if item.id == "palette_02"))
                app.pixel_size.set(3)
                app.pixel_size.set(4)
                try:
                    pump_until(lambda: app._preview_due)
                    self.assertEqual(window.original.size, (50, 80))
                finally:
                    release.set()
                pump_until(lambda: not app._busy and window.original.size == (200, 80))
                self.assertEqual(calls, [("anime", "te", 1), ("anime", "te", 2), ("pc9801", "palette_02", 4)])
                self.assertIs(app._preview_window, window)
                self.assertEqual(window.winfo_geometry(), geometry)
                self.assertEqual(window.zoom.get(), "50%")
                self.assertEqual(window.preview_photo.width(), 100)
                self.assertFalse(Path(app.output_path.get()).exists())

                app._hide_full_preview()
                self.assertFalse(app.auto_preview.get())
                self.assertEqual(window.state(), "withdrawn")
                app.pixel_size.set(5)
                self.assertIsNone(app._preview_after)
                app.start_preview()
                pump_until(lambda: not app._busy and window.original.size == (250, 80))
                self.assertIs(app._preview_window, window)
                self.assertEqual(window.winfo_geometry(), geometry)
                self.assertEqual(len([child for child in app.winfo_children() if isinstance(child, PreviewWindow)]), 1)

    def test_default_100_percent_and_explicit_zoom(self):
        root = gui.TkinterDnD.Tk() if gui.HAS_FILE_DROP else gui.tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root)
            app.line_growth.set(.25)
            self.assertEqual(app._settings().line_growth, .25)
            window = PreviewWindow(root, Image.new("RGBA", (160, 120), "white"))
            self.assertEqual((window.preview_photo.width(), window.preview_photo.height()), (160, 120))
            self.assertEqual(window.canvas.bbox(window.image_item), (0, 0, 160, 120))
            window.zoom.set("50%")
            window.redraw()
            self.assertEqual((window.preview_photo.width(), window.preview_photo.height()), (80, 60))
            window.zoom.set("200%")
            window.redraw()
            self.assertEqual((window.preview_photo.width(), window.preview_photo.height()), (320, 240))
            window.zoom.set("画面に収める")
            window.redraw()
            self.assertLessEqual(window.preview_photo.width(), 160)
        finally:
            root.update_idletasks()
            root.destroy()

    @unittest.skipUnless(os.name == "nt", "Windows DPI integration")
    def test_windows_process_is_dpi_aware_before_tk_starts(self):
        script = ("import ctypes; from display_support import enable_native_pixels; "
                  "assert enable_native_pixels(); u=ctypes.windll.user32; "
                  "u.GetThreadDpiAwarenessContext.restype=ctypes.c_void_p; "
                  "u.GetAwarenessFromDpiAwarenessContext.argtypes=[ctypes.c_void_p]; "
                  "assert u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext()) >= 1")
        result = subprocess.run([sys.executable, "-c", script], cwd=converter.ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
