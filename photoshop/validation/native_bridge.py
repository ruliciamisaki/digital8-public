"""Explicit validation bridge; the shipped Python application is not patched."""
import ctypes as ct
from pathlib import Path

import numpy as np


class NativeCore:
    def __init__(self, path=None):
        self.lib = ct.CDLL(str(path or Path(__file__).parents[1] / 'build/digital8_core.dll'))
        pointer = ct.c_void_p
        integer = ct.c_int32
        self.lib.d8_abi_version.restype = integer
        if self.lib.d8_abi_version() != 1:
            raise RuntimeError('Unsupported native ABI')
        for name in ('d8_union_scratch_bytes', 'd8_cleanup_scratch_bytes'):
            function = getattr(self.lib, name)
            function.argtypes = [integer]
            function.restype = ct.c_size_t
        self.lib.d8_union_regions.argtypes = [
            pointer, pointer, pointer, integer, pointer, integer, ct.c_double,
            integer, pointer, ct.c_size_t, pointer]
        self.lib.d8_clean_colour_cells.argtypes = [
            pointer, pointer, pointer, integer, integer, ct.c_double, integer,
            pointer, integer, pointer, ct.c_size_t, pointer]
        self.lib.d8_union_regions.restype = integer
        self.lib.d8_clean_colour_cells.restype = integer

    @staticmethod
    def _check(status):
        if status:
            raise ValueError(f'Native core status {status}')

    @staticmethod
    def _image(rgb, valid):
        rgb = np.ascontiguousarray(rgb, dtype=np.float32)
        valid = np.ascontiguousarray(valid, dtype=np.uint8)
        if valid.ndim != 2 or rgb.shape != (*valid.shape, 3) or not valid.size:
            raise ValueError('Expected nonempty HxWx3 RGB and HxW visibility')
        if valid.size > np.iinfo(np.int32).max:
            raise ValueError('Too many pixels')
        return rgb, valid

    def find_regions(self, rgb, valid, tolerance, min_area=12, guide=None):
        rgb, valid = self._image(rgb, valid)
        ids = np.arange(valid.size, dtype=np.int32).reshape(valid.shape)
        a = np.concatenate((ids[:, :-1].ravel(), ids[:-1].ravel()))
        b = np.concatenate((ids[:, 1:].ravel(), ids[1:].ravel()))
        pixels = rgb.reshape(-1, 3)
        difference = np.max(np.abs(pixels[a] - pixels[b]), axis=1)
        allowed = (valid.ravel()[a] != 0) & (valid.ravel()[b] != 0)
        if guide is not None:
            original, _ = self._image(guide, valid)
            original = original.reshape(-1, 3)
            allowed &= np.max(np.abs(original[a] - original[b]), axis=1) <= tolerance
        selected = np.flatnonzero(allowed & (difference <= tolerance))
        order = selected[np.argsort(difference[selected], kind='stable')]
        a, b = np.ascontiguousarray(a[order]), np.ascontiguousarray(b[order])
        scratch = np.empty((self.lib.d8_union_scratch_bytes(valid.size) + 7) // 8, np.float64)
        result = np.empty(valid.shape, np.int32)
        self._check(self.lib.d8_union_regions(
            rgb.ctypes.data, a.ctypes.data, b.ctypes.data, len(a), valid.ctypes.data,
            valid.size, tolerance, min_area, scratch.ctypes.data, scratch.nbytes, result.ctypes.data))
        return result

    def clean_colour_cells(self, indices, rgb, valid, tolerance, min_area, compatible=None):
        rgb, valid = self._image(rgb, valid)
        original = np.asarray(indices)
        if original.shape != valid.shape or original.dtype not in (np.dtype('uint8'), np.dtype('uint16')):
            raise ValueError('Expected uint8/uint16 indices matching visibility')
        indices = np.ascontiguousarray(original, dtype=np.uint16)
        count = int(indices.max()) + 1
        if compatible is not None:
            compatible = np.ascontiguousarray(compatible, dtype=np.uint8)
            if compatible.ndim != 2 or compatible.shape[0] != compatible.shape[1] or compatible.shape[0] < count:
                raise ValueError('Compatibility matrix does not cover palette indices')
            count = compatible.shape[0]
        scratch = np.empty((self.lib.d8_cleanup_scratch_bytes(valid.size) + 7) // 8, np.float64)
        result = np.empty(valid.shape, np.uint16)
        self._check(self.lib.d8_clean_colour_cells(
            indices.ctypes.data, rgb.ctypes.data, valid.ctypes.data, valid.shape[1], valid.shape[0],
            tolerance, min_area, None if compatible is None else compatible.ctypes.data,
            count, scratch.ctypes.data, scratch.nbytes, result.ctypes.data))
        return result.astype(original.dtype, copy=False)
