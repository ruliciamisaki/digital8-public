"""Inspect the built Windows resource Photoshop uses to discover the filter."""
import ctypes as ct
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PluginResourceTests(unittest.TestCase):
    def test_photoshop_named_json_manifest_and_entry_point(self):
        path = ROOT/'build/plugin/Digital8.8bf'
        kernel = ct.WinDLL('kernel32', use_last_error=True)
        kernel.LoadLibraryExW.argtypes = [ct.c_wchar_p, ct.c_void_p, ct.c_uint32]
        kernel.LoadLibraryExW.restype = ct.c_void_p
        kernel.FindResourceW.argtypes = [ct.c_void_p, ct.c_wchar_p, ct.c_wchar_p]
        kernel.FindResourceW.restype = ct.c_void_p
        kernel.SizeofResource.argtypes = [ct.c_void_p, ct.c_void_p]
        kernel.SizeofResource.restype = ct.c_uint32
        kernel.LoadResource.argtypes = [ct.c_void_p, ct.c_void_p]
        kernel.LoadResource.restype = ct.c_void_p
        kernel.LockResource.argtypes = [ct.c_void_p]
        kernel.LockResource.restype = ct.c_void_p
        kernel.FreeLibrary.argtypes = [ct.c_void_p]
        # Read resources without running the module's entry point.
        module = kernel.LoadLibraryExW(str(path), None, 2)
        self.assertTrue(module, f'Cannot inspect {path}')
        try:
            resource = kernel.FindResourceW(module, 'JSON_PIPL', 'JSON')
            self.assertTrue(resource, 'Photoshop requires a named JSON_PIPL resource of type JSON')
            size = kernel.SizeofResource(module, resource)
            self.assertGreater(size, 0)
            pointer = kernel.LockResource(kernel.LoadResource(module, resource))
            manifest = json.loads(ct.string_at(pointer, size).decode('utf-8-sig'))
            expected = json.loads((ROOT/'plugin/PiPLs.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest, expected)
            self.assertEqual(len(manifest['PiPLs']), 1)
            plugin = manifest['PiPLs'][0]
            self.assertEqual(plugin['Kind'], 'Filter')
            self.assertEqual(plugin['Category'], 'Digital8')
            self.assertTrue(plugin['SupportedModes']['RGBColor'])
            self.assertEqual(len(plugin['FilterCaseInfo']), 7)
        finally:
            kernel.FreeLibrary(module)
        executable = ct.WinDLL(str(path))
        self.assertTrue(getattr(executable, plugin['CodeEntryPointWin64']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
