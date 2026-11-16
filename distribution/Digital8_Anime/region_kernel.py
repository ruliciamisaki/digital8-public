"""Numeric region-union kernel, independent of image formats and the UI.

Edges arrive in stable colour-distance order. Do not parallelize the unions or
enable fastmath: both ordering and float64 span comparisons affect the image.
"""

import numpy as np

try:
    from numba import njit
except ImportError:
    njit = None

NUMBA_AVAILABLE = njit is not None


def _union_regions(pixels, edges_a, edges_b, valid, tolerance, min_area):
    count = len(pixels)
    parent = np.arange(count, dtype=np.int32)
    sizes = np.ones(count, dtype=np.int32)
    # The reference converts float32 pixels to Python floats before comparing
    # spans. Promote BEFORE subtraction to preserve its threshold decisions.
    low = pixels.astype(np.float64)
    high = low.copy()
    for pass_index in range(2 if min_area > 1 else 1):
        limit = tolerance if pass_index == 0 else tolerance * 1.5
        for edge in range(len(edges_a)):
            first, second = edges_a[edge], edges_b[edge]
            while parent[first] != first:
                parent[first] = parent[parent[first]]
                first = parent[first]
            while parent[second] != second:
                parent[second] = parent[parent[second]]
                second = parent[second]
            if first == second:
                continue
            if pass_index == 1 and min(sizes[first], sizes[second]) >= min_area:
                continue
            permitted = True
            for channel in range(3):
                span = (max(high[first, channel], high[second, channel])
                        - min(low[first, channel], low[second, channel]))
                if span > limit:
                    permitted = False
                    break
            if not permitted:
                continue
            if sizes[first] < sizes[second]:
                first, second = second, first
            parent[second] = first
            sizes[first] += sizes[second]
            for channel in range(3):
                low[first, channel] = min(low[first, channel], low[second, channel])
                high[first, channel] = max(high[first, channel], high[second, channel])
    for pixel in range(count):
        root = pixel
        while parent[root] != root:
            root = parent[root]
        parent[pixel] = root
    # Apply the visibility mask only after flattening all parent pointers.
    parent[~valid] = -1
    return parent


union_regions = njit(cache=True, nogil=True)(_union_regions) if NUMBA_AVAILABLE else None


def _clean_colour_cells(indices, rgb, valid, tolerance, min_area, compatible=None):
    """Four-connected paint components; only small islands may change paint."""
    height, width = valid.shape
    count = height * width
    parent = np.arange(count, dtype=np.int32)
    sizes = np.zeros(count, dtype=np.int32)
    colours = indices.ravel()
    visible = valid.ravel()
    pixels = rgb.reshape(count, 3)
    for y in range(height):
        for x in range(width):
            pixel = y * width + x
            if not visible[pixel]:
                continue
            for direction in range(2):
                if (direction == 0 and x == 0) or (direction == 1 and y == 0):
                    continue
                other = pixel - (1 if direction == 0 else width)
                if not visible[other] or colours[pixel] != colours[other]:
                    continue
                first, second = pixel, other
                while parent[first] != first:
                    parent[first] = parent[parent[first]]
                    first = parent[first]
                while parent[second] != second:
                    parent[second] = parent[parent[second]]
                    second = parent[second]
                if first != second:
                    # Stable root choice; no order-dependent colour averaging.
                    parent[max(first, second)] = min(first, second)
    sums = np.zeros((count, 3), dtype=np.float64)
    for pixel in range(count):
        if not visible[pixel]:
            continue
        root = pixel
        while parent[root] != root:
            root = parent[root]
        parent[pixel] = root
        sizes[root] += 1
        for channel in range(3):
            sums[root, channel] += pixels[pixel, channel]
    for root in range(count):
        if sizes[root]:
            sums[root] /= sizes[root]
    targets = np.arange(count, dtype=np.int32)
    best = np.full(count, np.inf)
    for y in range(height):
        for x in range(width):
            pixel = y * width + x
            if not visible[pixel]:
                continue
            first = parent[pixel]
            if sizes[first] >= min_area:
                continue
            for direction in range(4):
                if ((direction == 0 and x == 0) or (direction == 1 and x == width - 1)
                        or (direction == 2 and y == 0) or (direction == 3 and y == height - 1)):
                    continue
                other = pixel + (-1 if direction == 0 else 1 if direction == 1
                                 else -width if direction == 2 else width)
                if not visible[other]:
                    continue
                second = parent[other]
                if compatible is not None and not compatible[colours[first], colours[second]]:
                    continue
                if second == first or sizes[second] < sizes[first]:
                    continue
                if sizes[second] == sizes[first] and second > first:
                    continue
                allowed = True
                score = 0.
                for channel in range(3):
                    difference = sums[first, channel] - sums[second, channel]
                    if (abs(difference) > tolerance
                            or abs(float(pixels[pixel, channel]) - pixels[other, channel]) > tolerance):
                        allowed = False
                    score += difference * difference
                if allowed and (score < best[first] or (score == best[first] and second < targets[first])):
                    best[first] = score
                    targets[first] = second
    for component in range(count):
        if not sizes[component]:
            continue
        root = component
        while targets[root] != root:
            targets[root] = targets[targets[root]]
            root = targets[root]
        targets[component] = root
    result = colours.copy()
    for pixel in range(count):
        if not visible[pixel]:
            continue
        first = parent[pixel]
        root = targets[first]
        # Bound the entire cleanup chain, not just each individual edge.
        allowed = compatible is None or compatible[colours[first], colours[root]]
        for channel in range(3):
            if abs(sums[first, channel] - sums[root, channel]) > tolerance:
                allowed = False
        if allowed:
            result[pixel] = colours[root]
    return result.reshape(height, width)


clean_colour_cells = njit(cache=True, nogil=True)(_clean_colour_cells) if NUMBA_AVAILABLE else _clean_colour_cells
