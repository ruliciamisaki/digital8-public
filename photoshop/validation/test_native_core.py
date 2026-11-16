from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'distribution/Digital8_Anime'))
import anime_paint as anime
import region_kernel as reference
import digital8_converter as converter
from native_bridge import NativeCore


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = NativeCore()

    def test_union_random_ties_guides_and_precision(self):
        rng = np.random.default_rng(421)
        for trial in range(12):
            rgb = (rng.integers(0, 9, (9, 13, 3)) * 28).astype(np.float32)
            if trial % 2:
                rgb += rng.random(rgb.shape, dtype=np.float32)
            valid = rng.random(rgb.shape[:2]) > .15
            guide = rng.integers(0, 256, rgb.shape, dtype=np.uint8) if trial % 3 else None
            for tolerance in (0, 32, 64, 128):
                for area in (0, 1, 12, 4096):
                    np.testing.assert_array_equal(
                        self.core.find_regions(rgb, valid, tolerance, area, guide),
                        anime.find_regions_python(rgb, valid, tolerance, area, guide))
        rgb = np.full((1, 2, 3), 96, np.float32)
        rgb[0, 0] = np.nextafter(np.float32(32), np.float32(0))
        valid = np.ones((1, 2), bool)
        np.testing.assert_array_equal(self.core.find_regions(rgb, valid, 64, 0),
                                      anime.find_regions_python(rgb, valid, 64, 0))

    def test_cleanup_random_masks_compatibility_and_large_palettes(self):
        rng = np.random.default_rng(217)
        for trial in range(15):
            indices = rng.integers(0, 8, (11, 17), dtype=np.uint16)
            if trial % 2:
                indices *= 80  # Exercise CW indices beyond 255.
            rgb = rng.random((11, 17, 3), dtype=np.float32) * 255
            valid = rng.random((11, 17)) > .15
            count = int(indices.max()) + 1
            compatible = rng.random((count, count)) > .2 if trial % 3 else None
            for tolerance in (0, 32, 128, 256):
                for area in (0, 1, 12, 4096):
                    np.testing.assert_array_equal(
                        self.core.clean_colour_cells(indices, rgb, valid, tolerance, area, compatible),
                        reference._clean_colour_cells(indices, rgb, valid, tolerance, area, compatible))

    def test_single_rows_columns_invisible_and_strided_inputs(self):
        for shape in ((1, 1), (1, 19), (19, 1), (11, 15)):
            for visible in (False, True):
                rgb = np.full((*shape, 3), 80, np.float32)[::-1, ::2]
                valid = np.full(shape, visible)[::-1, ::2]
                indices = np.zeros(shape, np.uint8)[::-1, ::2]
                np.testing.assert_array_equal(self.core.find_regions(rgb, valid, 128),
                                              anime.find_regions_python(rgb, valid, 128))
                np.testing.assert_array_equal(self.core.clean_colour_cells(indices, rgb, valid, 128, 12),
                                              reference._clean_colour_cells(indices, rgb, valid, 128, 12))

    def test_bad_arguments_leave_output_untouched(self):
        rgb = np.zeros((2, 3), np.float32)
        valid = np.ones(2, np.uint8)
        labels = np.full(2, 123, np.int32)
        edges = np.array([0, 2], np.int32)
        scratch = np.empty(64, np.float64)
        args = [rgb.ctypes.data, edges.ctypes.data, edges.ctypes.data, 2,
                valid.ctypes.data, 2, 128, 12, scratch.ctypes.data, scratch.nbytes, labels.ctypes.data]
        self.assertEqual(self.core.lib.d8_union_regions(*args), 1)
        args[3] = 0
        args[9] = 1
        self.assertEqual(self.core.lib.d8_union_regions(*args), 2)
        args[9] = scratch.nbytes
        args[6] = float('nan')
        self.assertEqual(self.core.lib.d8_union_regions(*args), 1)
        np.testing.assert_array_equal(labels, 123)
        indices = np.zeros(2, np.uint16)
        output = np.full(2, 123, np.uint16)
        args = [indices.ctypes.data, rgb.ctypes.data, valid.ctypes.data, 2, 1,
                128, 12, None, 1, scratch.ctypes.data, 1, output.ctypes.data]
        self.assertEqual(self.core.lib.d8_clean_colour_cells(*args), 2)
        args[10] = scratch.nbytes
        indices[0] = 1
        self.assertEqual(self.core.lib.d8_clean_colour_cells(*args), 1)
        np.testing.assert_array_equal(output, 123)

    def test_reentrant_parallel_calls(self):
        def run(seed):
            rng = np.random.default_rng(seed)
            rgb = rng.integers(0, 256, (19, 21, 3), dtype=np.uint8)
            valid = rng.random((19, 21)) > .2
            return np.array_equal(self.core.find_regions(rgb, valid, 64),
                                  anime.find_regions_python(rgb, valid, 64))
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(run, range(16))))

    def test_full_anime_conversion_with_native_cleanup(self):
        from dataclasses import replace
        from PIL import Image
        rng = np.random.default_rng(9)
        rgba = rng.integers(0, 256, (24, 32, 4), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.png'
            Image.fromarray(rgba).save(source)
            for palette in ('te', 'cw', 'te_skin_plus_saturated'):
                for smooth in (False, True):
                    options = replace(converter.anime_standard(), palette_id=palette, smooth_2x=smooth)
                    expected = converter.convert_document(source, options)
                    with patch.object(anime, 'clean_colour_cells', self.core.clean_colour_cells):
                        actual = converter.convert_document(source, options)
                    np.testing.assert_array_equal(np.asarray(actual.image), np.asarray(expected.image))
                    np.testing.assert_array_equal(actual.labels, expected.labels)
                    self.assertEqual(actual.names, expected.names)


if __name__ == '__main__':
    unittest.main(verbosity=2)
