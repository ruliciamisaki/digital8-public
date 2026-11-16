import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import anime_paint as anime
import digital8_converter as converter
import region_kernel as kernel


class GlobalToneTests(unittest.TestCase):
    def test_same_tone_in_different_closed_shapes_keeps_same_paint_and_shadow(self):
        palette = np.array(((0,0,0),(240,230,225),(210,200,200),(170,155,160)),np.uint8)
        rgb = np.zeros((45,100,3),np.uint8)
        ink = np.ones((45,100),bool)
        rgb[4:40,4:40] = palette[1];ink[4:40,4:40] = False
        rgb[4:40,55:96] = palette[2];ink[4:40,55:96] = False
        rgb[20:35,15:30] = palette[3]
        rgb[10:35,65:90] = palette[3]
        rgb[8:16,15:30] = palette[2]
        result = anime.paint(rgb,ink,palette,tolerance=128,radius=3,min_area=12)
        self.assertEqual(result[26,22],result[22,77])
        self.assertEqual(result[26,22],3)
        self.assertEqual(result[11,22],result[7,75])
        self.assertNotEqual(result[26,22],result[7,12])

    def test_supersampling_uses_identical_native_colour_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)/'source.png'
            yy,xx = np.indices((25,41))
            rgb = np.stack((80+xx*4,60+yy*6,90+xx*3),axis=2).astype(np.uint8)
            rgb[:,20] = 0
            Image.fromarray(rgb,'RGB').save(source)
            options = replace(converter.anime_standard(),palette_id='te_skin_plus_saturated')
            references = []
            original = anime.build_colour_reference
            def capture(*args,**kwargs):
                result = original(*args,**kwargs);references.append(result);return result
            with patch.object(anime,'build_colour_reference',capture):
                converter.convert_document(source,options)
                converter.convert_document(source,replace(options,smooth_2x=True))
            self.assertEqual(len(references),2)
            for field in ('keys','means','selected'):
                np.testing.assert_array_equal(getattr(references[0],field),getattr(references[1],field))

    def test_stride_two_smoothing_cannot_jump_a_one_pixel_ink_or_alpha_wall(self):
        rgb = np.full((15,21,3),(190,130,90),np.uint8)
        rgb[:,11:] = (210,150,110)
        for wall in ('ink','alpha'):
            ink = np.zeros((15,21),bool);coverage = np.ones((15,21),np.float32)
            if wall == 'ink':ink[:,10] = True
            else:coverage[:,10] = 0
            result = anime.smooth_colours(rgb,ink,coverage,128,8,scale=2)
            np.testing.assert_allclose(result[:,:10],rgb[:,:10],atol=.001)
            np.testing.assert_allclose(result[:,11:],rgb[:,11:],atol=.001)

    def test_small_islands_cleanup_respects_walls_and_strong_highlights(self):
        classes = np.zeros((13,19),np.uint8)
        classes[5,5] = 1;classes[5,13] = 2
        rgb = np.full((13,19,3),120,np.uint8)
        rgb[5,5] = 125;rgb[5,13] = 255
        valid = np.ones((13,19),bool)
        cleaned = kernel.clean_colour_cells(classes,rgb,valid,40.,12)
        self.assertEqual(cleaned[5,5],0)
        self.assertEqual(cleaned[5,13],2)
        valid[4:7,4:7] = False;valid[5,5] = True
        cleaned = kernel.clean_colour_cells(classes,rgb,valid,40.,12)
        self.assertEqual(cleaned[5,5],1)

    def test_cleanup_area_scales_with_pixel_area(self):
        classes = np.zeros((9,9),np.uint8);classes[4,4:6] = 1
        rgb = np.full((9,9,3),120,np.uint8);rgb[4,4:6] = 125
        valid = np.ones((9,9),bool)
        native = kernel.clean_colour_cells(classes,rgb,valid,32.,3)
        enlarged = kernel.clean_colour_cells(np.repeat(np.repeat(classes,2,0),2,1),
                      np.repeat(np.repeat(rgb,2,0),2,1),np.repeat(np.repeat(valid,2,0),2,1),32.,12)
        np.testing.assert_array_equal(enlarged[::2,::2],native)

    def test_small_warm_area_is_not_cleaned_into_an_opposing_blue(self):
        palette = np.array(((0,0,0),(225,229,252),(230,200,180),(210,210,210)),np.uint8)
        rgb = np.full((13,19,3),palette[1],np.uint8)
        rgb[6,9] = palette[2]
        result = anime.paint(rgb,np.zeros((13,19),bool),palette,tolerance=128,radius=0,min_area=12)
        self.assertEqual(result[6,9],2)
        # A neutral intermediary must not let a cleanup chain bypass hue protection.
        rgb[6:8,10] = palette[3]
        result = anime.paint(rgb,np.zeros((13,19),bool),palette,tolerance=128,radius=0,min_area=12)
        self.assertEqual(result[6,9],2)

    def test_numba_cleanup_matches_python_fallback(self):
        rng = np.random.default_rng(11)
        for shape in ((1,1),(1,15),(15,1),(13,17)):
            for _ in range(4):
                classes = rng.integers(0,5,shape,dtype=np.uint8)
                rgb = rng.integers(70,140,(*shape,3),dtype=np.uint8)
                valid = rng.random(shape)>.15
                allowed = rng.random((5,5))>.3
                np.fill_diagonal(allowed,True)
                for compatible in (None,allowed):
                    expected = kernel._clean_colour_cells(classes,rgb,valid,40.,12,compatible)
                    actual = kernel.clean_colour_cells(classes,rgb,valid,40.,12,compatible)
                    np.testing.assert_array_equal(actual,expected)


if __name__ == '__main__':
    unittest.main()
