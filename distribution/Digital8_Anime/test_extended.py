import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

import anime_paint
import digital8_gui as gui
import digital8_converter as converter
import layer_export
import line_effects


def assert_visible_equal(test, actual, expected):
    actual, expected = np.asarray(actual.convert("RGBA")), np.asarray(expected.convert("RGBA"))
    np.testing.assert_array_equal(actual[:, :, 3], expected[:, :, 3])
    visible = expected[:, :, 3] > 0
    np.testing.assert_array_equal(actual[:, :, :3][visible], expected[:, :, :3][visible])


class PaletteAndLineTests(unittest.TestCase):
    def test_sampled_chart_databases_and_provenance(self):
        for kind, count in (("te", 100), ("cw", 563)):
            path = converter.ROOT / "palettes" / f"colour_{kind}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(anime_paint.load_palette(path)), count)
            self.assertEqual(len(data["entries"]), count)
            self.assertEqual(len(data["source_sha256"]), 64)
            self.assertEqual(len({entry["id"] for entry in data["entries"]}), count)
            for entry in data["entries"]:
                if "sample_box" not in entry:
                    continue
                x0, y0, x1, y1 = entry["sample_box"]
                a, b, c, d = entry["swatch_box"]
                self.assertTrue(a <= x0 < x1 <= c and b <= y0 < y1 <= d)
                self.assertEqual((x1 - x0, y1 - y0), (7, 7))
        self.assertIn((255, 255, 255), converter.palette_for("anime", "te").colours)

    def test_more_than_256_database_colours_with_small_usage_budget(self):
        palette = converter.palette_for("anime", "cw").array()
        yy, xx = np.indices((24, 32))
        rgb = np.stack((xx * 8, yy * 10, (xx * 5 + yy * 7) % 256), axis=2).astype(np.uint8)
        ink = np.zeros((24, 32), bool)
        ink[:, 16] = True
        for limit in (1, 8, 24):
            pool = []
            indices = anime_paint.paint(rgb, ink, palette, max_colours=limit,
                                        trace_support=True, palette_out=pool)
            traced = line_effects.trace_indices(indices, ink, palette, permitted=pool[0])
            self.assertLessEqual(len(np.unique(traced)), limit)
            self.assertEqual(indices.dtype, np.uint16)

    def test_expansion_is_independent_of_extraction_and_clips_transparency(self):
        ink = np.zeros((11, 11), bool)
        ink[5, 5] = True
        grown = line_effects.expand_ink(ink, 2)
        self.assertEqual(int(grown.sum()), 25)
        visible = np.ones(ink.shape, bool)
        visible[:, :5] = False
        self.assertFalse(line_effects.expand_ink(ink, 2, visible)[:, :5].any())
        np.testing.assert_array_equal(line_effects.expand_ink(ink, 0), ink)

    def test_trace_is_darker_palette_colour_and_preserves_black_interior(self):
        palette = np.array(((0, 0, 0), (200, 140, 100), (100, 60, 40)), np.uint8)
        indices = np.zeros((30, 30), np.uint8)
        ink = np.ones((30, 30), bool)
        ink[:3] = False
        ink[5:8] = False
        indices[~ink] = 1
        traced = line_effects.trace_indices(indices, ink, palette, reach=4, permitted=np.arange(3))
        self.assertEqual(int(traced[3, 15]), 2)
        self.assertEqual(int(traced[20, 15]), 0)
        np.testing.assert_array_equal(traced[~ink], indices[~ink])

    def test_trace_cannot_propagate_through_transparent_gap(self):
        palette = np.array(((0, 0, 0), (200, 140, 100), (100, 60, 40)), np.uint8)
        indices = np.zeros((5, 9), np.uint8)
        indices[:, 0] = 1
        ink = indices == 0
        visible = np.ones(ink.shape, bool)
        visible[:, 2] = False
        traced = line_effects.trace_indices(indices, ink, palette, visible=visible, permitted=np.arange(3))
        self.assertTrue(np.all(traced[:, 3:] == 0))

    def test_presets_use_requested_edge_default(self):
        for preset in (converter.pc8801_standard(), converter.pc9801_standard(), converter.anime_standard()):
            self.assertEqual(preset.edge_strength, 1.0)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        yy, xx = np.indices((25, 31))
        rgb = np.stack((100 + xx * 4, 80 + yy * 5, 60 + xx * 3), axis=2).astype(np.uint8)
        alpha = np.where(xx < 4, 0, np.where(xx < 8, 92, 255)).astype(np.uint8)
        self.source = self.folder / "source.png"
        Image.fromarray(np.dstack((rgb, alpha)), "RGBA").save(self.source)

    def test_layers_recompose_all_machines_dots_and_alpha(self):
        for preset in (converter.anime_standard(), converter.pc8801_standard(), converter.pc9801_standard()):
            options = replace(preset, resize_640=False, line_growth=1, colour_trace=True, pixel_size=2)
            document = converter.convert_document(self.source, options)
            composite = Image.new("RGBA", document.image.size)
            for index in document.layer_ids():
                layer, _offset = document.layer(index)
                composite = Image.alpha_composite(composite, layer)
            assert_visible_equal(self, composite, document.image)
            if preset.machine == "anime":
                self.assertLessEqual(len(document.image.convert("RGB").getcolors(100000)), 24)

    def test_zip_manifest_png_alpha_and_exact_recomposition(self):
        document = converter.convert_document(self.source, converter.anime_standard())
        output = self.folder / "layers.zip"
        layer_export.write_zip(output, document)
        composite = Image.new("RGBA", document.image.size)
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["size"], [31, 25])
            for entry in manifest["layers"]:
                with Image.open(io.BytesIO(archive.read(entry["file"]))) as layer:
                    composite = Image.alpha_composite(composite, layer.convert("RGBA"))
            assert_visible_equal(self, composite, document.image)

    def test_psd_header_pillow_and_packbits_roundtrip(self):
        document = converter.convert_document(self.source, converter.anime_standard())
        output = self.folder / "layers.psd"
        layer_export.write_psd(output, document)
        self.assertEqual(output.read_bytes()[:4], b"8BPS")
        with Image.open(output) as merged:
            self.assertEqual(merged.size, document.image.size)
            np.testing.assert_array_equal(merged.getchannel("A"), document.image.getchannel("A"))
        rng = np.random.default_rng(5)
        for row in (bytes([0]) * 300, bytes(range(256)), rng.integers(0, 256, 509, np.uint8).tobytes()):
            encoded = layer_export._packbits(row)
            decoded = bytearray()
            i = 0
            while i < len(encoded):
                command = encoded[i]
                i += 1
                if command <= 127:
                    decoded.extend(encoded[i:i + command + 1]); i += command + 1
                elif command >= 129:
                    decoded.extend([encoded[i]] * (257 - command)); i += 1
            self.assertEqual(bytes(decoded), row)

    def read_psd(self, path):
        # Optional independent reader; installed only in the local test target.
        dependency = converter.ROOT / ".test-deps"
        if importlib.util.find_spec("psd_tools") is None:
            if not (dependency / "psd_tools").exists():
                self.skipTest("Optional psd-tools decoder is not installed")
            sys.path.append(str(dependency))
        from psd_tools import PSDImage
        return PSDImage.open(path)

    def test_independent_psd_layer_decoder_exact_recomposition(self):
        document = converter.convert_document(self.source, converter.anime_standard())
        output = self.folder / "layers.psd"
        layer_export.write_psd(output, document)
        loaded = self.read_psd(output)
        self.assertEqual(len(loaded), len(document.layer_ids()))
        assert_visible_equal(self, loaded.composite(force=True), document.image)

    def test_white_is_optional_transparency(self):
        Image.new("RGB", (10, 10), "white").save(self.source)
        settings = replace(converter.anime_standard(), white_transparent=True)
        image, alpha = converter.convert_image(self.source, settings)
        self.assertEqual(alpha.getextrema(), (0, 0))
        self.assertEqual(image.mode, "RGBA")

    def test_conversion_saves_psd_and_zip_and_returns_same_preview(self):
        options = converter.anime_standard()
        expected, _ = converter.convert_image(self.source, options)
        for extension in ("psd", "zip"):
            destination = self.folder / f"out.{extension}"
            result = converter.convert(self.source, destination, options)
            self.assertGreater(destination.stat().st_size, 50)
            assert_visible_equal(self, result, expected)

    def test_psd_fill_flattens_mixed_tile_only_on_either_machine(self):
        yellow, blue = (255, 255, 0), (0, 0, 255)
        pixels = np.full((17, 31, 4), (128, 128, 128, 120), dtype=np.uint8)
        pixels[:, 16:] = (*yellow, 255)
        pixels[:3, :, 3] = 0
        Image.fromarray(pixels, "RGBA").save(self.source)
        for preset, palette_size in ((converter.pc8801_standard(), 8), (converter.pc9801_standard(), 16)):
            with self.subTest(machine=preset.machine):
                options = replace(preset, tall_dots=False, resize_640=False, line_threshold=0, edge_strength=0,
                                  palette_id="custom", retro_palette=(yellow, blue) * (palette_size // 2))
                document = converter.convert_document(self.source, options)
                self.assertEqual(set(document.image.convert("RGB").getdata()), {yellow, blue})
                output = self.folder / "fill.psd"
                default = self.folder / "default.psd"
                converter.convert(self.source, default, options)
                for extras in (("fill",), ("colour",), ("colour", "fill")):
                    saved = converter.convert(self.source, output, options, psd_layers=extras)
                    assert_visible_equal(self, saved, document.image)
                    with Image.open(output) as merged, Image.open(default) as baseline:
                        np.testing.assert_array_equal(merged, baseline)
                    exported = self.read_psd(output)
                    groups = {layer.name: layer for layer in exported}
                    expected = {"タイル模様を保持", *(layer_export.PSD_EXTRA_GROUPS[mode] for mode in extras)}
                    self.assertEqual(set(groups), expected)
                    self.assertTrue(all(group.is_group() for group in groups.values()))
                    self.assertEqual([name for name, group in groups.items() if group.visible], ["タイル模様を保持"])
                    assert_visible_equal(self, exported.composite(force=True), document.image)
                    for mode in extras:
                        group = groups[layer_export.PSD_EXTRA_GROUPS[mode]]
                        groups["タイル模様を保持"].visible = False
                        group.visible = True
                        comparison = Image.fromarray(pixels, "RGBA") if mode == "fill" else document.image
                        assert_visible_equal(self, exported.composite(force=True), comparison)
                        group.visible = False
                for extension in ("png", "zip"):
                    ordinary = converter.convert(self.source, self.folder / f"out.{extension}", options,
                                                 psd_layers="fill")
                    assert_visible_equal(self, ordinary, document.image)

    def test_psd_colour_groups_rendered_rgb_and_anime_fill_keeps_lines(self):
        for settings in (converter.pc8801_standard(), converter.anime_standard()):
            document = converter.convert_document(self.source, replace(settings, resize_640=False, colour_trace=True))
            split = document.for_psd_layers("colour")
            actual = np.asarray(document.image.convert("RGBA"))
            rgb = {tuple(colour) for colour in actual[actual[:, :, 3] > 0, :3]}
            expected_names = {f"#{r:02X}{g:02X}{b:02X}" for r, g, b in rgb}
            self.assertEqual(set(split.names.values()), expected_names)
            combined = Image.new("RGBA", split.image.size)
            for index in split.layer_ids():
                layer, _ = split.layer(index)
                combined = Image.alpha_composite(combined, layer)
            assert_visible_equal(self, combined, document.image)
            if settings.machine == "anime":
                self.assertIs(document.for_psd_layers("fill"), document)


class GuiExtendedTests(unittest.TestCase):
    def test_chart_switches_output_format_and_full_database_editor(self):
        root = gui.TkinterDnD.Tk() if gui.HAS_FILE_DROP else gui.tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root)
            self.assertEqual(app._settings().palette_id, "te")
            app.palette_label.set("カラーCW")
            app.update_palette_swatch()
            self.assertEqual(len(app._settings().anime_palette), 563)
            app.output_path.set("test.png")
            self.assertTrue(all(checkbox.instate(["disabled"]) for checkbox in app.psd_layer_checkboxes))
            app.output_format.set("PSD")
            app.change_output_format()
            self.assertEqual(app.output_path.get(), "test.psd")
            self.assertTrue(all(checkbox.instate(["!disabled"]) for checkbox in app.psd_layer_checkboxes))
            revision = app._preview_revision
            for checkbox in app.psd_layer_checkboxes:
                checkbox.invoke()
            self.assertTrue(all(option.get() for option in app.psd_extra_layers.values()))
            self.assertEqual(app._preview_revision, revision)
            app.output_path.set("test.zip")
            self.assertTrue(all(checkbox.instate(["disabled"]) for checkbox in app.psd_layer_checkboxes))
            app.edit_palette()
            root.update_idletasks()
            self.assertLess(app.palette_canvas.winfo_reqheight(), 100)
        finally:
            root.update_idletasks()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
