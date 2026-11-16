"""Compare actual conversions, including alpha and layer membership.

Only the cleanup kernel is replaced. This measures its integration, not a
complete C++ converter or Photoshop's execution time.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'distribution/Digital8_Anime'))
import anime_paint as anime
import digital8_converter as converter
from native_bridge import NativeCore


def digest(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('results.json'))
    args = parser.parse_args()
    if args.width <= 0:
        parser.error('--width must be positive')
    gallery = ROOT / 'distribution/Digital8_Anime/output_sample/anime'
    manifest = json.loads((gallery / 'manifest.json').read_text(encoding='utf-8')) if not args.source else None
    sources = [args.source] if args.source else [gallery / entry['source'] for entry in manifest['samples']]
    if not sources:
        parser.error('No reference images found; pass --source')
    core = NativeCore()
    # Warm the existing Numba implementation before measuring complete calls.
    anime.clean_colour_cells(np.zeros((2, 2), np.uint16), np.zeros((2, 2, 3), np.float32),
                             np.ones((2, 2), bool), 128., 12, np.ones((1, 1), bool))
    records = []
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / 'source.png'
        for original in sources:
            with Image.open(original) as opened:
                image = opened.convert('RGBA')
                if image.width > args.width:
                    image = image.resize((args.width, max(1, round(image.height * args.width / image.width))), Image.Resampling.LANCZOS)
                image.save(source)
            for machine, options in (
                ('anime', converter.anime_standard()),
                ('pc8801', replace(converter.pc8801_standard(), resize_640=False)),
                ('pc9801', replace(converter.pc9801_standard(), resize_640=False)),
            ):
                start = perf_counter()
                expected = converter.convert_document(source, options)
                reference_seconds = perf_counter() - start
                start = perf_counter()
                with patch.object(anime, 'clean_colour_cells', wraps=core.clean_colour_cells) as native:
                    actual = converter.convert_document(source, options)
                native_seconds = perf_counter() - start
                if native.call_count == 0:
                    raise AssertionError('Native cleanup was not exercised')
                np.testing.assert_array_equal(np.asarray(actual.image), np.asarray(expected.image))
                np.testing.assert_array_equal(actual.labels, expected.labels)
                if actual.names != expected.names:
                    raise AssertionError('Layer names differ')
                if actual.alpha is not None or expected.alpha is not None:
                    np.testing.assert_array_equal(np.asarray(actual.alpha), np.asarray(expected.alpha))
                record = dict(source=original.name, source_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
                              machine=machine, size=image.size,
                              rgba_sha256=digest(np.asarray(actual.image)), labels_sha256=digest(actual.labels),
                              reference_seconds=reference_seconds, native_cleanup_seconds=native_seconds,
                              cleanup_calls=native.call_count, identical=True)
                records.append(record)
                print(f'{original.name} {machine}: exact match; {reference_seconds:.3f}s -> {native_seconds:.3f}s', flush=True)
    args.output.write_text(json.dumps(dict(abi_version=1, scope='Python converter with C++ cleanup only',
                                           results=records), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{len(records)} conversions matched: {args.output}')


if __name__ == '__main__':
    main()
