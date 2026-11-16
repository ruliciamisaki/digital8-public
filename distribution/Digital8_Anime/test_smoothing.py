import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import digital8_gui as gui
import digital8_converter as converter


class SmoothingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "source.png"
        yy, xx = np.indices((17, 29))
        rgb = np.stack((80 + xx * 5, 60 + yy * 10, 45 + xx * 4), axis=2).astype(np.uint8)
        rgb[:, 13:15] = 0
        alpha = np.where(xx < 4, 0, np.where(xx < 9, 96, 255)).astype(np.uint8)
        Image.fromarray(np.dstack((rgb, alpha)), "RGBA").save(self.source)

    def presets(self):
        return (converter.anime_standard(), converter.pc8801_standard(), converter.pc9801_standard())

    def test_without_640_matches_explicit_expand_convert_reduce(self):
        with Image.open(self.source) as opened:
            enlarged = opened.convert("RGBA").resize((58, 34), Image.Resampling.LANCZOS)
        input_2x = self.folder / "input_2x.png"
        enlarged.save(input_2x)
        for preset in self.presets():
            options = replace(preset, resize_640=False, smooth_2x=False)
            with Image.open(self.source) as native:
                if options.machine == 'anime':
                    reference = converter._anime_reference(native, options)
                    converted = converter._anime_image(input_2x, options, reference, spatial_scale=2).image
                else:
                    pixels, coverage, _alpha, _logical, _output, ink = converter._prepare_source(native, options)
                    palette = np.asarray(converter.resolved_palette(options), dtype=np.uint8)
                    reference = converter._retro_reference(pixels, ink, coverage, palette, options)
                    converted = converter._retro_image(input_2x, options, reference, spatial_scale=2).image
            expected = converted.convert("RGBA").resize((29, 17), Image.Resampling.LANCZOS)
            actual, alpha = converter.convert_image(self.source, replace(options, smooth_2x=True))
            np.testing.assert_array_equal(actual, expected)
            np.testing.assert_array_equal(alpha, actual.getchannel("A"))

    def test_with_640_is_a_post_conversion_roundtrip(self):
        # Very wide source keeps the 640px branch small while testing a real
        # width conversion before the colour/line processing.
        Image.open(self.source).resize((801, 17)).save(self.source)
        for preset in self.presets():
            options = replace(preset, resize_640=True, smooth_2x=False)
            plain, _alpha = converter.convert_image(self.source, options)
            expected = plain.convert("RGBA").resize((plain.width * 2, plain.height * 2), Image.Resampling.LANCZOS)
            expected = expected.resize(plain.size, Image.Resampling.LANCZOS)
            with patch.object(converter.image_processing, "adjust_colours", wraps=converter.image_processing.adjust_colours) as adjust:
                actual, alpha = converter.convert_image(self.source, replace(options, smooth_2x=True))
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(actual.width, 640)
            self.assertEqual(adjust.call_args.args[0].shape[1], 640)
            np.testing.assert_array_equal(alpha, actual.getchannel("A"))

    def test_processing_really_sees_double_dimensions_without_640(self):
        options = replace(converter.anime_standard(), smooth_2x=True)
        with patch.object(converter.image_processing, "adjust_colours", wraps=converter.image_processing.adjust_colours) as adjust:
            result, _alpha = converter.convert_image(self.source, options)
        self.assertEqual(adjust.call_args.args[0].shape[:2], (34, 58))
        self.assertEqual(result.size, (29, 17))

    def test_indexed_pc_image_gets_real_interpolation_and_extra_colours(self):
        values = np.full((15, 25, 3), 255, np.uint8)
        values[:, 11:14] = 0
        Image.fromarray(values, "RGB").save(self.source)
        options = replace(converter.pc8801_standard(), tall_dots=False, resize_640=False, smooth_2x=True)
        result, alpha = converter.convert_image(self.source, options)
        self.assertEqual(result.mode, "RGB")
        self.assertIsNone(alpha)
        original_palette = set(converter.palette_for("pc8801", "digital8").colours)
        self.assertTrue(set(result.getdata()) - original_palette)

    def test_hidden_rgb_does_not_leak_through_supersampled_rgba(self):
        options = replace(converter.anime_standard(), smooth_2x=True)
        first, _alpha = converter.convert_image(self.source, options)
        with Image.open(self.source) as image:
            rgba = np.array(image)
        rgba[rgba[:, :, 3] == 0, :3] = (255, 0, 255)
        Image.fromarray(rgba, "RGBA").save(self.source)
        second, _alpha = converter.convert_image(self.source, options)
        np.testing.assert_array_equal(first, second)

    def test_binary_alpha_is_binary_at_final_resolution(self):
        for resized in (False, True):
            if resized:
                with Image.open(self.source) as opened:
                    opened.resize((801, 17)).save(self.source)
            options = replace(converter.anime_standard(), smooth_2x=True, resize_640=resized, alpha_mode="binary")
            image, alpha = converter.convert_image(self.source, options)
            self.assertTrue(set(alpha.getdata()) <= {0, 255})
            np.testing.assert_array_equal(image.getchannel("A"), alpha)

    def test_smooth_layers_recompose_and_zip_uses_the_same_preview(self):
        for preset in self.presets():
            options = replace(preset, resize_640=False, smooth_2x=True, colour_trace=True, line_growth=.5)
            document = converter.convert_document(self.source, options)
            self.assertEqual(document.labels.shape, (17, 29))
            composite = Image.new("RGBA", document.image.size)
            for index in document.layer_ids():
                layer, _offset = document.layer(index)
                composite = Image.alpha_composite(composite, layer)
            expected = np.asarray(document.image.convert("RGBA"))
            visible = expected[:, :, 3] > 0
            np.testing.assert_array_equal(np.asarray(composite)[visible], expected[visible])
            np.testing.assert_array_equal(composite.getchannel("A"), document.image.getchannel("A"))
        output = self.folder / "out.zip"
        converter.convert(self.source, output, options)
        with zipfile.ZipFile(output) as archive:
            with Image.open(io.BytesIO(archive.read("composite.png"))) as loaded:
                np.testing.assert_array_equal(loaded, document.image)
            self.assertEqual(json.loads(archive.read("manifest.json"))["size"], [29, 17])

    def test_psd_and_alpha_mask_keep_smoothed_size(self):
        options = replace(converter.anime_standard(), smooth_2x=True)
        output, mask = self.folder / "out.psd", self.folder / "mask.png"
        result = converter.convert(self.source, output, options, mask)
        with Image.open(output) as image:
            self.assertEqual(image.size, (29, 17))
            np.testing.assert_array_equal(image.getchannel("A"), result.getchannel("A"))
        with Image.open(mask) as image:
            np.testing.assert_array_equal(np.asarray(image)[:, :, 0], result.getchannel("A"))

    def test_cli_flag_is_available(self):
        destination = self.folder / "cli.png"
        command = [sys.executable, str(Path(converter.__file__)), str(self.source), str(destination),
                   "--preset", "anime", "--smooth-2x", "--no-resize-640"]
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with Image.open(destination) as image:
            self.assertEqual(image.size, (29, 17))


class GuiSmoothingTests(unittest.TestCase):
    def test_checkbox_settings_presets_and_processing_size_label(self):
        root = gui.TkinterDnD.Tk() if gui.HAS_FILE_DROP else gui.tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root)
            self.assertFalse(app._settings().smooth_2x)
            app.source_image = Image.new("RGBA", (31, 21), "white")
            app.smooth_2x.set(True)
            app.refresh_source_preview()
            self.assertTrue(app._settings().smooth_2x)
            self.assertIn("62×42", app.size_label.get())
            self.assertIn("31×21", app.size_label.get())
            app.resize_640.set(True)
            app.refresh_source_preview()
            self.assertIn("640×434", app.size_label.get())
            self.assertNotIn("1280", app.size_label.get())
            app.apply_preset(converter.anime_standard())
            self.assertFalse(app._settings().smooth_2x)
        finally:
            root.update_idletasks()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
