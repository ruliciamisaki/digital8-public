import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import digital8_gui as gui
import digital8_converter as converter
import png_settings as metadata


class PngSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'original 日本語.png'
        yy, xx = np.indices((17, 29))
        rgb = np.stack((80 + xx * 5, 60 + yy * 10, 45 + xx * 4), axis=2).astype(np.uint8)
        alpha = np.where(xx < 6, 32, 255).astype(np.uint8)
        Image.fromarray(np.dstack((rgb, alpha)), 'RGBA').save(self.source)

    def save(self, options):
        destination = self.folder / 'converted.png'
        mask = self.folder / 'alpha.png'
        image = converter.convert(self.source, destination, options, mask, True)
        return destination, image

    def test_roundtrip_and_exact_replay_for_all_machines(self):
        for preset in (converter.anime_standard(), converter.pc8801_standard(), converter.pc9801_standard()):
            options = replace(preset, resize_640=False, smooth_2x=True,
                              line_growth=.5, colour_trace=True)
            path, image = self.save(options)
            recipe = metadata.read_recipe(path)
            restored = metadata.decode_settings(recipe)
            self.assertEqual(restored, options)
            self.assertEqual(metadata.find_original(path, recipe), self.source)
            replay, _alpha = converter.convert_image(self.source, restored)
            with Image.open(path) as stored:
                np.testing.assert_array_equal(stored.convert('RGBA'), image.convert('RGBA'))
                np.testing.assert_array_equal(stored.convert('RGBA'), replay.convert('RGBA'))
            self.assertEqual(recipe['source']['name'], self.source.name)
            self.assertNotIn(str(self.folder), json.dumps(recipe))
            self.assertEqual(recipe['export'], {'alpha_mask': True, 'invert_alpha_mask': True})
            with Image.open(self.folder / 'alpha.png') as mask:
                self.assertNotIn(metadata.KEY, mask.info)

    def test_custom_and_changed_database_palette_snapshots(self):
        colours = ((0,0,0), (190,155,120), (220,205,205), (255,255,255))
        options = replace(converter.anime_standard(), palette_id='custom', anime_palette=colours)
        path, _ = self.save(options)
        self.assertEqual(metadata.decode_settings(metadata.read_recipe(path)), options)
        recipe = metadata.read_recipe(path)
        recipe['settings']['palette_id'] = 'te'
        recipe['settings']['anime_palette'] = None
        restored = metadata.decode_settings(recipe)
        self.assertEqual(restored.palette_id, 'custom')
        self.assertEqual(restored.anime_palette, colours)

    def test_original_relink_requires_unchanged_sibling(self):
        path, _ = self.save(converter.anime_standard())
        recipe = metadata.read_recipe(path)
        Image.new('RGB', (3,3), 'red').save(self.source)
        self.assertIsNone(metadata.find_original(path, recipe))
        for bad in ('../original.png', r'C:\outside.png', r'\\server\share\file.png', '.'):
            recipe['source']['name'] = bad
            self.assertIsNone(metadata.find_original(path, recipe))

    def test_plain_images_and_unrelated_metadata_are_ignored(self):
        self.assertIsNone(metadata.read_recipe(self.source))
        info = PngInfo()
        info.add_text('workflow', '{"unrelated":"ComfyUI"}')
        Image.new('RGB', (2,2)).save(self.source, pnginfo=info)
        self.assertIsNone(metadata.read_recipe(self.source))

    def test_invalid_json_versions_types_and_ranges_are_rejected(self):
        path, _ = self.save(converter.anime_standard())
        good = metadata.read_recipe(path)
        for raw in ('{', 'null', '{"schema_version":99}', '{"schema_version":true}'):
            info = PngInfo()
            info.add_itxt(metadata.KEY, raw)
            Image.new('RGB', (2,2)).save(path, pnginfo=info)
            with self.assertRaises(ValueError):
                metadata.read_recipe(path)
        for field, bad in (('smooth_2x','yes'), ('pixel_size',False), ('brightness',float('nan')),
                           ('brightness',10**400),
                           ('anime_radius',9), ('palette_id','unavailable'), ('line_growth',-1)):
            recipe = json.loads(json.dumps(good))
            recipe['settings'][field] = bad
            # An unknown anime palette ID intentionally restores its snapshot as Custom.
            if field == 'palette_id':
                self.assertEqual(metadata.decode_settings(recipe).palette_id, 'custom')
            else:
                with self.assertRaises(ValueError):
                    metadata.decode_settings(recipe)

    def test_migrate_older_pc98_metadata_to_v4(self):
        options = replace(converter.pc9801_standard(), resize_640=False)
        path, image = self.save(options)
        recipe = metadata.read_recipe(path)
        recipe['renderer'] = 'digital8-anime-global-tones-5'
        recipe['settings'].update(method='frequent', frequent_mode='gradient', fill_method='legacy',
                                  colour_coherence=10, fill_stability=.2, colour_groups=1)
        info = PngInfo()
        info.add_itxt(metadata.KEY, json.dumps(recipe))
        image.save(path, pnginfo=info)
        _root, app = self.make_app()
        app.set_input(path)
        self.assertEqual(app._settings(), replace(options, colour_groups=2))
        self.assertEqual(app.notebook.select(), str(app.two_colour_frame))
        self.assertEqual(Path(app.input_path.get()), self.source)
        self.assertIn('変換ロジックの版が違う', app.status.get())

    def make_app(self):
        root = gui.TkinterDnD.Tk() if gui.HAS_FILE_DROP else gui.tk.Tk()
        root.withdraw()
        app = gui.App(root)
        def cleanup():
            if app._source_after is not None:
                root.after_cancel(app._source_after)
            root.update_idletasks()
            root.destroy()
        self.addCleanup(cleanup)
        return root, app

    def test_drop_restores_controls_custom_palette_and_original(self):
        colours = ((0,0,0), (180,140,100), (220,205,205), (255,255,255))
        options = replace(converter.anime_standard(), palette_id='custom', anime_palette=colours,
                          smooth_2x=True, line_growth=.5, anime_tolerance=64, anime_max_colours=3)
        path, _ = self.save(options)
        root, app = self.make_app()
        app.output_format.set('PSD')
        app.drop_input(SimpleNamespace(data=root.tk.call('list', str(path))))
        self.assertEqual(app._settings(), options)
        self.assertEqual(Path(app.input_path.get()), self.source)
        self.assertTrue(app.export_alpha_mask.get())
        self.assertTrue(app.invert_alpha_mask.get())
        self.assertEqual(app.output_format.get(), 'PNG')
        self.assertIn('元画像を復元', app.status.get())

    def test_gui_restores_machine_palette_and_active_tab(self):
        _root, app = self.make_app()
        for preset in (converter.anime_standard(), converter.pc8801_standard(), converter.pc9801_standard()):
            options = replace(preset, resize_640=False)
            path, _ = self.save(options)
            app.set_input(path)
            expected = (replace(options, anime_palette=converter.palette_for('anime',options.palette_id).colours)
                        if options.machine == 'anime' else options)
            self.assertEqual(app.notebook.select(), str(app.anime_frame if options.machine == 'anime' else app.two_colour_frame))
            self.assertEqual(app._settings(), expected)

    def test_cli_png_also_contains_a_restorable_recipe(self):
        destination = self.folder / 'cli.png'
        result = subprocess.run([sys.executable, str(converter.ROOT/'digital8_converter.py'),
                                 str(self.source), str(destination), '--preset', 'anime',
                                 '--palette', 'te', '--anime-tolerance', '64', '--line-growth', '.5'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        options = metadata.decode_settings(metadata.read_recipe(destination))
        self.assertEqual(options.anime_tolerance,64)
        self.assertEqual(options.line_growth,.5)

    def test_missing_original_restores_settings_and_explains_input(self):
        path, _ = self.save(converter.anime_standard())
        elsewhere = self.folder / 'elsewhere'
        elsewhere.mkdir()
        copied = elsewhere / path.name
        shutil.copyfile(path, copied)
        _root, app = self.make_app()
        app.set_input(copied)
        self.assertEqual(Path(app.input_path.get()), copied)
        self.assertIn('再変換には元画像', app.status.get())

    def test_obsolete_fields_do_not_break_anime_restore(self):
        options = converter.anime_standard()
        path, image = self.save(options)
        recipe = metadata.read_recipe(path)
        recipe['settings'] = dict(asdict(options), frequent_mode='gradient')
        info = PngInfo()
        info.add_itxt(metadata.KEY, json.dumps(recipe))
        image.save(path, pnginfo=info)
        _root, app = self.make_app()
        app.set_input(path)
        self.assertEqual(app._settings().machine, 'anime')
        app.set_input(self.source)
        self.assertIn(self.source.name, app.status.get())
        self.assertNotIn('復元', app.status.get())

    def test_corrupt_recipe_still_opens_image_without_changing_controls(self):
        _root, app = self.make_app()
        previous = app._settings()
        info = PngInfo()
        info.add_itxt(metadata.KEY, '{broken')
        Image.new('RGB', (3,3)).save(self.source, pnginfo=info)
        with patch.object(gui.messagebox, 'showerror') as error:
            app.set_input(self.source)
        error.assert_not_called()
        self.assertEqual(app._settings(), previous)
        self.assertIsNotNone(app.source_image)
        self.assertIn('読み込めなかった', app.status.get())


if __name__ == '__main__':
    unittest.main()
