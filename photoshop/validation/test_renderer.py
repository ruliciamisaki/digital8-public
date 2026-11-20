from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import numpy as np

EXE = Path(__file__).resolve().parents[1] / 'build/validation/native_cli.exe'


class RendererTests(unittest.TestCase):
    def run_native(self, rgba, machine=2, options=(), snapshot=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output, settings = root/'input.rgba', root/'output.rgba', root/'settings.txt'
            source.write_bytes(struct.pack('<III', 0x31493844, rgba.shape[1], rgba.shape[0]) + rgba.tobytes())
            arguments = [str(EXE), str(source), str(output), str(machine), *map(str, options)]
            if snapshot is not None:
                settings.write_text(snapshot, encoding='ascii')
                arguments += ['--settings', str(settings)]
            arguments += ['--save-settings', str(settings)]
            result = subprocess.run(arguments, capture_output=True, text=True)
            if result.returncode:
                raise ValueError(result.stderr)
            self.assertEqual(output.read_bytes()[:12], source.read_bytes()[:12])
            actual = np.frombuffer(output.read_bytes()[12:], np.uint8).reshape(rgba.shape).copy()
            return actual, settings.read_text(encoding='ascii')

    def test_exact_solid_colours_and_alpha_all_machines(self):
        rgba = np.zeros((17, 41, 4), np.uint8)
        rgba[..., 3] = 255
        rgba[:, :13, :3] = (255, 0, 0)
        rgba[:, 13:26, :3] = (0, 255, 255)
        rgba[:, 26:, :3] = (255, 255, 255)
        rgba[::2, :, 3] = 77
        for machine, palette in ((0,'digital8'), (1,'digital8_half')):
            # PC98 half-bright palette includes full primaries as well as dark ones.
            actual, _ = self.run_native(rgba, machine, ['--palette',palette,'--tall',0,'--radius',0,'--area',0,'--tolerance',0,'--threshold',0,'--edge',0,'--colours',0,'--groups',64])
            np.testing.assert_array_equal(actual[...,3], rgba[...,3])
            colours = set(map(tuple, actual[...,:3].reshape(-1,3)))
            self.assertLessEqual(len(colours),16)

    def test_settings_roundtrip_and_snapshot_custom_colours(self):
        rgba = np.full((9, 19, 4), 255, np.uint8)
        rgba[...,:3] = (255,0,0)
        first, saved = self.run_native(rgba, 0, ['--tall',0,'--radius',0,'--area',0,'--threshold',0,'--edge',0])
        second, saved_again = self.run_native(rgba, 2, snapshot=saved)
        np.testing.assert_array_equal(first,second)
        self.assertEqual(saved,saved_again)
        legacy = bytearray.fromhex(saved[5:])[:1924]
        struct.pack_into('<I', legacy, 4, 1)
        restored, upgraded = self.run_native(rgba, 2, snapshot='D8N1:' + legacy.hex())
        np.testing.assert_array_equal(first, restored)
        self.assertEqual(saved, upgraded)
        raw = bytearray.fromhex(saved[5:])
        palette = np.frombuffer(raw, np.uint8, count=1800, offset=124).reshape(600,3)
        red = np.flatnonzero(np.all(palette == (255,0,0),axis=1))[0]
        palette[red] = (230,0,0)
        edited, _ = self.run_native(rgba, 2, snapshot=saved[:5]+raw.hex())
        self.assertTrue(np.any(np.all(edited[...,:3] == (230,0,0),axis=2)))
        self.assertFalse(np.array_equal(edited,first))

    def test_four_and_five_colour_patterns_repeat_at_logical_pixel_scale(self):
        colours = np.array([[255 * ((i >> bit) & 1) for bit in (1, 2, 0)]
                            for i in range(8)], dtype=np.uint8)
        for machine in (0, 1):
            for option, rows in (('four-colour', ('1767', '7636')),
                                 ('five-colour', ('3526', '6360', '2536', '6360'))):
                with self.subTest(machine=machine, option=option):
                    # The second five-colour assignment has the same average
                    # and a lower contrast penalty in the full Digital8 palette.
                    tile = colours[[[int(dot) for dot in row] for row in rows]]
                    target = np.rint(tile.mean(axis=(0, 1))).astype(np.uint8)
                    rgba = np.full((16, 8, 4), 255, np.uint8)
                    rgba[..., :3] = target
                    palette = 'digital8' if machine == 0 else 'digital8_half'
                    options = ['--palette', palette, '--tall', 1, '--edge', 0,
                               '--threshold', 0, '--radius', 0, '--area', 0, '--tolerance', 0]
                    disabled, _ = self.run_native(rgba, machine, options)
                    enabled, snapshot = self.run_native(rgba, machine, [*options, '--' + option, 1])
                    self.assertFalse(np.array_equal(disabled, enabled))
                    self.assertEqual(len(np.unique(enabled[..., :3].reshape(-1, 3), axis=0)),
                                     4 if option == 'four-colour' else 5)
                    np.testing.assert_array_equal(enabled[::2], enabled[1::2])
                    np.testing.assert_array_equal(enabled[:, :4], enabled[:, 4:])
                    period = 2 * len(rows)
                    np.testing.assert_array_equal(enabled[period:], enabled[:-period])
                    restored, _ = self.run_native(rgba, snapshot=snapshot)
                    np.testing.assert_array_equal(enabled, restored)

    def test_alpha_binary_and_white_transparency(self):
        rgba = np.full((13,21,4),255,np.uint8)
        rgba[...,:3] = (255,255,255)
        rgba[...,3] = np.arange(21,dtype=np.uint8)*12
        actual,_=self.run_native(rgba,2,['--alpha',1,'--alpha-threshold',128,'--tall',0])
        np.testing.assert_array_equal(actual[...,3],np.where(rgba[...,3]>=128,255,0))
        actual,_=self.run_native(rgba,2,['--white',1,'--tall',0])
        np.testing.assert_array_equal(actual[...,3],0)

    def test_odd_sizes_pixel_grid_and_pattern_palette_contract(self):
        rng=np.random.default_rng(9)
        rgba=rng.integers(0,256,(23,37,4),dtype=np.uint8)
        for machine in range(3):
            actual,saved=self.run_native(rgba,machine,['--tall',1,'--pixel',3,'--growth',.5,'--trace',1,'--three112',2,'--three111',2])
            np.testing.assert_array_equal(actual[...,3],rgba[...,3])
            raw=bytes.fromhex(saved[5:]);count=struct.unpack_from('<i',raw,16)[0]
            colours=set(map(tuple,np.frombuffer(raw,np.uint8,count=count*3,offset=124).reshape(-1,3)))
            self.assertTrue(all(tuple(c) in colours for c in actual[...,:3].reshape(-1,3)))

    def test_corrupt_settings_and_unsupported_values_rejected(self):
        rgba=np.full((2,2,4),255,np.uint8)
        for options in (['--pixel',0],['--tolerance',129],['--growth','nan'],['--radius',9]):
            with self.assertRaises(ValueError):self.run_native(rgba,2,options)
        with self.assertRaises(ValueError):self.run_native(rgba,2,snapshot='D8N9:00')


if __name__ == '__main__':
    unittest.main(verbosity=2)
