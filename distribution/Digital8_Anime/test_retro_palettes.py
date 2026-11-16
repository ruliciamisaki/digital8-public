import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import anime_paint
import digital8_gui as gui
import digital8_converter as converter
import png_settings
from palette_editor import PaletteEditor


def colours_for(machine):
    count = 8 if machine == 'pc8801' else 16
    # Deliberately use RGB values outside either machine's hardware steps.
    return tuple((11 + i * 13, 7 + i * 11, 19 + i * 9) for i in range(count))


class RetroPaletteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'original.png'
        xx, yy = np.meshgrid(np.arange(32), np.arange(20))
        rgb = np.stack((xx * 7, yy * 11, (xx + yy) * 4), axis=2).astype('uint8')
        Image.fromarray(np.dstack((rgb, np.where(xx < 4, 64, 255).astype('uint8'))), 'RGBA').save(self.source)

    def root(self):
        root = gui.TkinterDnD.Tk() if gui.HAS_FILE_DROP else gui.tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        return root

    def test_v4_uses_custom_rgb_for_both_machines(self):
        for preset in (converter.pc8801_standard(), converter.pc9801_standard()):
            colours = colours_for(preset.machine)
            with self.subTest(machine=preset.machine):
                options = replace(preset, palette_id='custom', retro_palette=colours,
                    resize_640=False, tall_dots=False,
                    three_112='three_112_diagonal', three_111='three_111_stripes',
                    colour_groups=8, line_growth=.5, colour_trace=True)
                doc = converter.convert_document(self.source, options)
                actual = np.asarray(doc.image.convert('RGBA'))
                self.assertTrue({tuple(v) for v in actual[:, :, :3].reshape(-1, 3)} <= set(colours))
                with Image.open(self.source) as source:
                    np.testing.assert_array_equal(actual[:, :, 3], source.getchannel('A'))
                combined = Image.new('RGBA', doc.image.size)
                for layer in doc.layer_ids():
                    combined = Image.alpha_composite(combined, doc.layer(layer)[0])
                np.testing.assert_array_equal(combined, doc.image.convert('RGBA'))
                # Matching must use the edited palette, not just recolour old indices.
                solid = Image.new('RGB', (12, 12), colours[-1])
                exact, _ = converter.convert_image(solid, replace(options, line_threshold=0,
                    edge_strength=0, line_growth=0, colour_trace=False, colour_groups=16))
                self.assertEqual(set(exact.convert('RGB').getdata()), {colours[-1]})

    def test_png_and_gui_restore_both_machine_custom_palettes(self):
        root = self.root()
        app = gui.App(root)
        for preset in (converter.pc8801_standard(), converter.pc9801_standard()):
            options = replace(preset, palette_id='custom', retro_palette=colours_for(preset.machine),
                              resize_640=False, colour_groups=8)
            output = self.folder / 'converted.png'
            converter.convert(self.source, output, options)
            restored = png_settings.decode_settings(png_settings.read_recipe(output))
            self.assertEqual(restored, options)
            app.set_input(output)
            self.assertEqual(app._settings(), options)
            replay, _ = converter.convert_image(self.source, restored)
            with Image.open(output) as stored:
                np.testing.assert_array_equal(replay.convert('RGBA'), stored.convert('RGBA'))
        if app._source_after is not None:
            root.after_cancel(app._source_after)

    def test_old_png_and_changed_builtin_snapshot(self):
        for preset in (converter.pc8801_standard(), converter.pc9801_standard()):
            values = asdict(preset)
            values.pop('retro_palette')
            palette = converter.resolved_palette(preset)
            recipe = {'settings': values, 'palette': palette}
            self.assertEqual(png_settings.decode_settings(recipe), preset)
            recipe['palette'] = colours_for(preset.machine)
            restored = png_settings.decode_settings(recipe)
            self.assertEqual(restored.palette_id, 'custom')
            self.assertEqual(restored.retro_palette, recipe['palette'])

    def test_invalid_counts_and_values_rejected(self):
        for machine in ('pc8801', 'pc9801'):
            for palette in (colours_for(machine)[:-1], ((-1, 3, 4),) * (8 if machine == 'pc8801' else 16)):
                with self.assertRaises(ValueError):
                    converter.validate_retro_palette(machine, palette)

    def test_outline_uses_darkest_edited_slot_and_supersampling_restores(self):
        for preset in (converter.pc8801_standard(), converter.pc9801_standard()):
            colours = list(colours_for(preset.machine))
            colours[0], colours[-1] = (240, 230, 220), (3, 5, 7)
            options = replace(preset, palette_id='custom', retro_palette=tuple(colours),
                              resize_640=False, tall_dots=False, edge_strength=0)
            image, _ = converter.convert_image(Image.new('RGB', (8, 8), 'black'), options)
            self.assertEqual(set(image.convert('RGB').getdata()), {(3, 5, 7)})
            options = replace(options, smooth_2x=True)
            output = self.folder / 'supersampled.png'
            converter.convert(self.source, output, options)
            restored = png_settings.decode_settings(png_settings.read_recipe(output))
            replay, _ = converter.convert_image(self.source, restored)
            with Image.open(output) as stored:
                np.testing.assert_array_equal(replay.convert('RGBA'), stored.convert('RGBA'))

    def test_gui_separate_custom_caches_and_fixed_count_editors(self):
        root = self.root()
        app = gui.App(root)
        originals = {m: converter.palettes_for(m) for m in ('pc8801', 'pc9801')}
        for preset in (converter.pc8801_standard(), converter.pc9801_standard()):
            app.apply_preset(preset)
            app.edit_palette()
            editor = next(w for w in root.winfo_children() if isinstance(w, PaletteEditor))
            count = 8 if preset.machine == 'pc8801' else 16
            self.assertEqual(len(editor.colours), count)
            self.assertEqual(editor.fixed_count, count)
            editor.add(); editor.remove()
            self.assertEqual(len(editor.colours), count)
            # Editing and cancelling never mutates a preset or the current custom cache.
            cached = app.retro_colours[preset.machine]
            editor.hex_value.set('#123456'); editor.apply_hex(); editor.destroy()
            self.assertEqual(app.retro_colours[preset.machine], cached)
            app.set_retro_palette(preset.machine, colours_for(preset.machine))
            self.assertEqual(app._settings().retro_palette, colours_for(preset.machine))
            self.assertEqual(app.notebook.select(), str(app.two_colour_frame))
        for machine in ('pc8801', 'pc9801'):
            self.assertEqual(app.retro_colours[machine], colours_for(machine))
            self.assertEqual(converter.palettes_for(machine), originals[machine])
        if app._source_after is not None:
            root.after_cancel(app._source_after)

    def test_editor_json_load_validation_and_apply(self):
        root = self.root()
        committed = []
        palette = colours_for('pc9801')
        editor = PaletteEditor(root, palette, committed.append, fixed_count=16, palette_name='PC-9801')
        self.addCleanup(lambda: editor.destroy() if editor.winfo_exists() else None)
        file = self.folder / 'palette.json'
        anime_paint.save_palette(file, palette[:-1])
        with patch('palette_editor.filedialog.askopenfilename', return_value=str(file)), \
                patch('palette_editor.messagebox.showerror') as error:
            editor.load()
            error.assert_called_once()
        self.assertEqual(tuple(editor.colours), palette)
        editor.select(15); editor.hex_value.set('#e2af83')
        with patch('palette_editor.filedialog.asksaveasfilename', return_value=str(file)):
            editor.save()
        expected = palette[:-1] + ((226, 175, 131),)
        self.assertEqual(anime_paint.load_palette(file), expected)
        editor.hex_value.set('#000000'); editor.apply_hex()
        with patch('palette_editor.filedialog.askopenfilename', return_value=str(file)):
            editor.load()
        editor.apply()
        self.assertEqual(committed, [expected])

    def test_cli_palette_file_both_machines(self):
        for machine in ('pc8801', 'pc9801'):
            file = self.folder / 'palette.json'
            anime_paint.save_palette(file, colours_for(machine))
            output = self.folder / 'cli.png'
            result = subprocess.run([sys.executable, str(converter.ROOT / 'digital8_converter.py'),
                str(self.source), str(output), '--preset', machine, '--palette-file', str(file),
                '--no-resize-640'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            restored = png_settings.decode_settings(png_settings.read_recipe(output))
            self.assertEqual(restored.retro_palette, colours_for(machine))


if __name__ == '__main__':
    unittest.main()
