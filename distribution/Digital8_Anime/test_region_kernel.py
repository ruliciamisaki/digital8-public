import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import anime_paint as anime
from region_kernel import NUMBA_AVAILABLE, union_regions


@unittest.skipUnless(NUMBA_AVAILABLE, "Numba is not installed")
class RegionKernelTests(unittest.TestCase):
    def assert_parity(self, rgb, valid, tolerance, min_area=12, guide=None):
        expected = anime.find_regions_python(rgb, valid, tolerance, min_area, guide)
        actual = anime.find_regions(rgb, valid, tolerance, min_area, guide)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.dtype, np.int32)

    def test_random_regions_masks_guide_and_cleanup(self):
        rng = np.random.default_rng(421)
        for trial in range(20):
            # Include tied edges and continuous floating-point diffusion values.
            rgb = (rng.integers(0, 9, (13, 17, 3)) * 28).astype(np.float32)
            if trial % 2:
                rgb += rng.random(rgb.shape, dtype=np.float32)
            valid = rng.random(rgb.shape[:2]) > .15
            guide = rng.integers(0, 256, rgb.shape, dtype=np.uint8) if trial % 3 else None
            for tolerance in (0, 32, 64, 128):
                for min_area in (0, 1, 12, 4096):
                    with self.subTest(trial=trial, tolerance=tolerance, area=min_area):
                        self.assert_parity(rgb, valid, tolerance, min_area, guide)

    def test_uniform_ties_and_degenerate_dimensions(self):
        for shape in ((1, 1), (1, 19), (19, 1), (11, 15)):
            rgb = np.full((*shape, 3), 80, np.uint8)
            for visible in (False, True):
                valid = np.full(shape, visible)
                self.assert_parity(rgb, valid, 128)
                self.assert_parity(rgb, valid, 0, 0)

    def test_span_subtraction_retains_python_float_precision(self):
        rgb = np.full((1, 2, 3), 96, np.float32)
        rgb[0, 0] = np.nextafter(np.float32(32), np.float32(0))
        valid = np.ones((1, 2), bool)
        self.assert_parity(rgb, valid, 64, 0)
        self.assertNotEqual(*anime.find_regions(rgb, valid, 64, 0)[0])

    def test_noncontiguous_arrays(self):
        rng = np.random.default_rng(4)
        rgb = rng.random((23, 31, 3), dtype=np.float32) * 255
        self.assert_parity(rgb[::-1, ::2], np.ones((23, 31), bool)[::-1, ::2], 128)

    def test_compiled_in_nopython_mode(self):
        self.assert_parity(np.zeros((2, 2, 3)), np.ones((2, 2), bool), 128)
        self.assertTrue(union_regions.nopython_signatures)


class FallbackTests(unittest.TestCase):
    def test_fallback_keeps_reference_labels(self):
        rgb = np.arange(36, dtype=np.uint8).reshape(3, 4, 3)
        valid = np.ones((3, 4), bool)
        expected = anime.find_regions_python(rgb, valid, 12)
        with patch.object(anime, "NUMBA_AVAILABLE", False):
            np.testing.assert_array_equal(anime.find_regions(rgb, valid, 12), expected)

    def test_import_and_conversion_without_numba(self):
        # A subprocess proves the fallback works when the dependency is absent,
        # rather than just changing a flag after successful imports.
        code = """
import sys
sys.modules['numba'] = None
import numpy as np
import anime_paint as anime
assert not anime.NUMBA_AVAILABLE
rgb = np.zeros((2, 3, 3), np.uint8)
assert np.all(anime.find_regions(rgb, np.ones((2, 3), bool), 128) == 0)
"""
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent, check=True)


if __name__ == "__main__":
    unittest.main()
