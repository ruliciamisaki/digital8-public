"""Compare full conversions with Python versus Numba island cleanup."""

import argparse
import hashlib
import json
import platform
import time
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import numba

import anime_paint as anime
import digital8_converter as converter
import region_kernel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("preview/performance/numba.json"))
    args = parser.parse_args()
    if not anime.NUMBA_AVAILABLE:
        parser.error("Numba is required for this comparison")
    options = replace(converter.anime_standard(), palette_id="te_saturated", line_growth=.5)
    report = {"python": platform.python_version(), "numpy": np.__version__,
              "numba": numba.__version__, "processor": platform.processor(),
              "source": args.source.name,
              "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
              "settings": asdict(options), "results": {}}
    # Run separately from conversion timing. Use a new NUMBA_CACHE_DIR to
    # measure compilation, or keep the default to measure disk-cache loading.
    start = time.perf_counter()
    anime.clean_colour_cells(np.zeros((2,2), np.uint8), np.zeros((2,2,3), np.uint8),
                             np.ones((2,2), bool), 128., 12, np.ones((1,1), bool))
    report["numba_initialization_seconds"] = time.perf_counter() - start
    accelerated = anime.clean_colour_cells
    for mode, settings in (("normal", options), ("process_2x", replace(options, smooth_2x=True))):
        documents = []
        measurements = {}
        for backend, function in (("python", region_kernel._clean_colour_cells), ("numba", accelerated)):
            stage_seconds = 0.

            def timed(*values, **kwargs):
                nonlocal stage_seconds
                start = time.perf_counter()
                result = function(*values, **kwargs)
                stage_seconds += time.perf_counter() - start
                return result

            start = time.perf_counter()
            with patch.object(anime, "clean_colour_cells", timed):
                document = converter.convert_document(args.source, settings)
            measurements[backend] = {"conversion_seconds": time.perf_counter() - start,
                                     "cleanup_seconds": stage_seconds}
            documents.append(document)
            print(mode, backend, measurements[backend], flush=True)
        reference, actual = documents
        np.testing.assert_array_equal(actual.image.convert("RGBA"), reference.image.convert("RGBA"))
        np.testing.assert_array_equal(actual.alpha, reference.alpha)
        np.testing.assert_array_equal(actual.labels, reference.labels)
        assert actual.names == reference.names
        measurements["identical_image_alpha_labels_names"] = True
        measurements["speedup"] = (measurements["python"]["conversion_seconds"]
                                   / measurements["numba"]["conversion_seconds"])
        report["results"][mode] = measurements
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(mode, "exact parity; speedup", measurements["speedup"], flush=True)


if __name__ == "__main__":
    main()
