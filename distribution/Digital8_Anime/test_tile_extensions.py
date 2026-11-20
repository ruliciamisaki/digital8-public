import tempfile
import subprocess
import sys
import unittest
from collections import Counter
from dataclasses import asdict,replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import digital8_converter as converter
import digital8_gui as gui
import png_settings
import tile_patterns as patterns


class TileDefinitionTests(unittest.TestCase):
    def test_exact_pixels_match_supplied_image_and_doubled_rows(self):
        source=converter.ROOT.parent.parent/'digital8パターン解説'/'追加パターン.png'
        if not source.exists():self.skipTest('Reference PNG is not included in the distribution')
        rgb=np.asarray(Image.open(source).convert('RGB'))
        definitions=(('eighth_staggered',20,30,0),('three_112_stripes',0,40,10),
            ('three_112_alternate',10,40,10),('three_112_diagonal',20,40,10),
            ('three_111_stripes',0,50,20),('three_111_diagonal',10,50,20))
        for name,x,y,upper in definitions:
            rows=patterns.EXTRA_TEMPLATES[name]
            mapping=({'A':(0,0,0),'B':(255,0,0)}if name=='eighth_staggered'else
                     {'A':(255,0,0),'B':(0,0,255),'C':(0,0,0)})
            expected=np.array([[mapping[v]for v in row]for row in rows],np.uint8)
            h,w=expected.shape[:2]
            np.testing.assert_array_equal(rgb[y:y+h,x:x+w],expected)
            np.testing.assert_array_equal(rgb[upper:upper+2*h,x:x+w],np.repeat(expected,2,axis=0))

    def test_four_and_five_colour_example_tiles_and_independent_switches(self):
        colours = np.array([[255 * ((i >> bit) & 1) for bit in (1, 2, 0)]
                            for i in range(8)], dtype=np.uint8)
        examples = {'four_colour': ('1767', '7636'),
                    'five_colour': ('3426', '6361', '2436', '6361')}
        for machine in ('pc8801', 'pc9801'):
            build = (lambda **options: converter.pc88_two_colour.build_recipes(
                         palette_override=colours, **options)) if machine == 'pc8801' else (
                         lambda **options: converter.pc98_two_colour.build_recipes(
                             np.tile(colours, (2, 1)), **options))
            self.assertTrue(set(examples).isdisjoint(recipe.name for recipe in build()))
            for field, rows in examples.items():
                with self.subTest(machine=machine, field=field):
                    expected = tuple(int(dot) for row in rows for dot in row)
                    recipes = build(**{field: True})
                    mixed = [recipe for recipe in recipes if recipe.name in examples]
                    self.assertEqual({recipe.name for recipe in mixed}, {field})
                    recipe = next(recipe for recipe in mixed if recipe.tile == expected)
                    self.assertEqual(recipe.shape, (len(rows), 4))
                    np.testing.assert_array_equal(recipe.target, colours[list(expected)].mean(axis=0))

    def test_counts_shapes_and_three_colour_disable(self):
        for module,palette in ((converter.pc88_two_colour,None),
                               (converter.pc98_two_colour,converter.palette_for('pc9801','palette_14').array())):
            def build(**kwargs):return module.build_recipes(**kwargs)if palette is None else module.build_recipes(palette,**kwargs)
            self.assertTrue(all(r.c is None for r in build()))
            for field,values,ratios in (('three_112',patterns.THREE_112[1:],(1,1,2)),
                                       ('three_111',patterns.THREE_111[1:],(1,1,1))):
                for style in values:
                    recipes=[r for r in build(**{field:style})if r.c is not None]
                    self.assertTrue(recipes)
                    for r in recipes:
                        counts=Counter(r.tile);unit=len(r.tile)/sum(ratios)
                        self.assertEqual(tuple(counts[i]for i in (r.a,r.b,r.c)),tuple(int(v*unit)for v in ratios))
            recipes=build(three_112=patterns.THREE_112[-1],three_111=patterns.THREE_111[-1])
            self.assertLess(len(recipes),65536)


class RetroFillTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.source=self.folder/'source.png'
        yy,xx=np.indices((23,37))
        rgb=np.stack((70+xx*4,65+yy*7,40+xx*3),axis=2).astype(np.uint8)
        rgb[:,18]=0
        alpha=np.where(xx<3,0,np.where(xx<7,110,255)).astype(np.uint8)
        Image.fromarray(np.dstack((rgb,alpha)),'RGBA').save(self.source)

    def test_requested_three_colour_styles_are_actually_used(self):
        for field,styles,rgb in (('three_112',patterns.THREE_112[1:],(128,0,64)),
                                 ('three_111',patterns.THREE_111[1:],(85,85,85))):
            Image.new('RGB',(48,48),rgb).save(self.source)
            for style in styles:
                options=replace(converter.pc8801_standard(),resize_640=False,tall_dots=False,
                                line_threshold=0,edge_strength=0,**{field:style})
                image,_=converter.convert_image(self.source,options)
                self.assertEqual(len(image.convert('RGB').getcolors()),3,style)

    def test_legacy_cli_pattern_and_fill_flags_are_retired(self):
        for args in (('--method', 'frequent'), ('--frequent-mode', 'gradient'), ('--fill-method', 'legacy')):
            output = self.folder / 'old.png'
            run = subprocess.run([sys.executable, str(converter.ROOT/'digital8_converter.py'),
                                  str(self.source), str(output), *args], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn('unrecognized arguments', run.stderr)
            self.assertFalse(output.exists())

    def test_eighth_preference_selects_new_phase(self):
        Image.new('RGB',(16,16),(32,32,32)).save(self.source)
        options=replace(converter.pc8801_standard(),resize_640=False,tall_dots=False,
            line_threshold=0,edge_strength=0,eighth_tone='eighth_staggered')
        image,_=converter.convert_image(self.source,options)
        values=np.asarray(image.convert('RGB'))
        expected=np.array([[(255,255,255)if v=='B'else(0,0,0)
                            for v in row]for row in patterns.EXTRA_TEMPLATES['eighth_staggered']],np.uint8)
        np.testing.assert_array_equal(values[:4,:4],expected)

    def test_cli_new_options_are_saved_in_png(self):
        output=self.folder/'cli.png'
        result=subprocess.run([sys.executable,str(converter.ROOT/'digital8_converter.py'),str(self.source),str(output),
            '--preset','pc8801','--no-resize-640','--eighth-tone','eighth_staggered',
            '--three-112','three_112_diagonal','--three-111','three_111_stripes'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        options=png_settings.decode_settings(png_settings.read_recipe(output))
        self.assertEqual((options.eighth_tone,options.three_112,options.three_111),
                         ('eighth_staggered','three_112_diagonal','three_111_stripes'))

    def test_v4_presets_accept_new_tiles(self):
        for preset in (converter.pc8801_standard(), converter.pc9801_standard()):
            options = replace(preset, resize_640=False, line_threshold=0, three_112='three_112_alternate',
                              three_111='three_111_stripes', eighth_tone='eighth_staggered')
            image, _ = converter.convert_image(self.source, options)
            self.assertEqual(image.size, (37, 23))

    def test_new_choices_and_global_group_limits_are_validated(self):
        for changes in ({'eighth_tone':'none'},
                        {'three_112':'invalid'},{'three_111':'invalid'},
                        {'colour_groups':1},
                        {'colour_groups':65}):
            options=replace(converter.pc8801_standard(),resize_640=False,**changes)
            with self.assertRaises(ValueError):converter.convert_document(self.source,options)
            recipe={'settings':asdict(options),'palette':converter.palette_for('pc8801','digital8').colours}
            with self.assertRaises(ValueError):png_settings.decode_settings(recipe)

    def test_v4_both_machines_layers_alpha_and_metadata(self):
        for preset in (converter.pc8801_standard(),converter.pc9801_standard()):
            options=replace(preset,resize_640=False,
                tall_dots=False,three_112='three_112_diagonal',three_111='three_111_diagonal',
                eighth_tone='eighth_staggered',colour_groups=12,colour_trace=True,line_growth=.5)
            document=converter.convert_document(self.source,options)
            palette=converter.palette_for(options.machine,options.palette_id).array()
            actual=np.asarray(document.image.convert('RGBA'))
            allowed={tuple(v)for v in palette}
            self.assertTrue({tuple(v)for v in actual[:,:,:3][actual[:,:,3]>0]}<=allowed)
            np.testing.assert_array_equal(actual[:,:,3],Image.open(self.source).getchannel('A'))
            combined=Image.new('RGBA',document.image.size)
            for index in document.layer_ids():combined=Image.alpha_composite(combined,document.layer(index)[0])
            visible=actual[:,:,3]>0
            np.testing.assert_array_equal(np.asarray(combined)[visible],actual[visible])
            output=self.folder/'result.png';converter.convert(self.source,output,options)
            self.assertEqual(png_settings.decode_settings(png_settings.read_recipe(output)),options)

    def test_new_global_retro_reference_is_shared_with_supersampling(self):
        options=replace(converter.pc9801_standard(),
                        resize_640=False,three_111='three_111_stripes',colour_groups=8)
        import retro_global
        original=retro_global.build_reference;seen=[]
        def capture(*args,**kwargs):
            result=original(*args,**kwargs);seen.append(result);return result
        with patch.object(retro_global,'build_reference',capture):
            converter.convert_document(self.source,options)
            converter.convert_document(self.source,replace(options,smooth_2x=True))
        self.assertEqual(len(seen),2)
        for field in ('selected','keys','means'):
            np.testing.assert_array_equal(getattr(seen[0].tones,field),getattr(seen[1].tones,field))

    def test_old_pngs_get_missing_v4_defaults_and_new_gui_choices_restore(self):
        options=converter.pc8801_standard()
        values=asdict(options)
        for name in ('eighth_tone','three_112','three_111','four_colour','five_colour'):values.pop(name)
        recipe={'settings':values,'palette':converter.palette_for('pc8801','digital8').colours}
        self.assertEqual(png_settings.decode_settings(recipe),options)
        root=gui.TkinterDnD.Tk()if gui.HAS_FILE_DROP else gui.tk.Tk();root.withdraw();app=gui.App(root)
        try:
            options=replace(options,resize_640=False,eighth_tone='eighth_staggered',
                            three_112='three_112_alternate',three_111='three_111_diagonal',
                            four_colour=True, five_colour=True)
            output=self.folder/'new.png';converter.convert(self.source,output,options)
            app.set_input(output)
            self.assertEqual(app._settings(),options)
            self.assertEqual(app.three_112_label.get(),'交互 4×2')
        finally:
            if app._source_after is not None:root.after_cancel(app._source_after)
            root.update_idletasks();root.destroy()


if __name__=='__main__':unittest.main()
