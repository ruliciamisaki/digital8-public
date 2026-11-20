"""Spatial, solid-colour cel painting. No tiles, dithering, or file I/O in paint()."""

from __future__ import annotations

import colorsys
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from image_processing import srgb_to_oklab
from region_kernel import NUMBA_AVAILABLE, union_regions, clean_colour_cells


def matching_features(rgb: np.ndarray) -> np.ndarray:
    """Colour matching coordinates for solid cel paints, not spatial merging.

    Plain OKLab distance lets a closer lightness outweigh a visible tint in
    nearly neutral material. Base squared distance is
    dL^2 + 4*(da^2+db^2) + 9*dC^2. matching_costs ranks hue compatibility
    before this distance for chromatic sources.
    Use this SAME metric for palette budgeting and final region assignment.
    """
    lab = srgb_to_oklab(np.asarray(rgb))
    chroma = np.linalg.norm(lab[..., 1:], axis=-1)
    return np.concatenate((lab * np.array((1, 2, 2), dtype=np.float32),
                           (3 * chroma)[..., None]), axis=-1)


def matching_costs(sources: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """Directed source-to-paint costs for 2D matching_features arrays.

    For source chroma >= .02, prefer paints in the same hue half plane OR
    neutral paints over opposing chromatic paints. Keeping neutrals eligible
    avoids forcing a tint into bright near-white material. Neutral sources
    retain the original metric because their hue direction is unstable.

    The finite tier stride 256 exceeds the base distance over valid sRGB,
    making this lexicographic, not another soft hue penalty. It stays finite
    for weighted palette optimization (zero histogram weights times inf
    would produce NaNs), and is invariant to target subsets and batch size.
    """
    # Keep small within-tier differences after adding the rank stride.
    costs = np.sum((sources[:, None] - targets[None]) ** 2, axis=2).astype(np.float64)
    dot = np.einsum('ic,jc->ij', sources[:, 1:3], targets[:, 1:3])
    source_coloured = sources[:, 3] >= .06  # C >= .02; stored coordinate is 3C.
    target_neutral = targets[:, 3] < .06
    tiers = ((dot < 0) & ~target_neutral[None]).astype(np.uint8)
    costs += 256.0 * np.where(source_coloured[:, None], tiers, 0)
    return costs


def validate_palette(colours: object) -> tuple[tuple[int, int, int], ...]:
    if not isinstance(colours, (list, tuple)) or not 1 <= len(colours) <= 4096:
        raise ValueError("パレットは1〜4096色のRGB配列にして。")
    result = []
    for colour in colours:
        if (not isinstance(colour, (list, tuple)) or len(colour) != 3
                or any(type(value) is not int or not 0 <= value <= 255 for value in colour)):
            raise ValueError("各色は0〜255の整数3個 [R, G, B] で指定して。")
        result.append(tuple(colour))
    return tuple(result)


def default_palette() -> tuple[tuple[int, int, int], ...]:
    """120 editable starting colours: neutrals, skin ramps, and hue ramps."""
    colours = [(value, value, value) for value in (0, 36, 72, 112, 152, 192, 224, 255)]
    colours += [
        (66, 35, 34), (97, 51, 43), (130, 69, 52), (163, 91, 66),
        (191, 119, 83), (215, 149, 111), (237, 179, 138), (255, 210, 169),
        (84, 43, 54), (119, 63, 73), (151, 85, 94), (183, 110, 116),
        (211, 140, 141), (235, 170, 167), (249, 201, 188), (255, 230, 212),
    ]
    ramp = ((.65, .22), (.75, .38), (.80, .55), (.75, .70),
            (.65, .83), (.50, .93), (.33, 1.), (.15, 1.))
    for hue in range(12):
        for saturation, value in ramp:
            colours.append(tuple(round(channel * 255) for channel in
                                 colorsys.hsv_to_rgb(hue / 12, saturation, value)))
    return validate_palette(colours)


def load_palette(path: Path) -> tuple[tuple[int, int, int], ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("colours", data.get("colors"))
    return validate_palette(data)


def save_palette(path: Path, colours: object) -> None:
    palette = validate_palette(colours)
    Path(path).write_text(json.dumps({"name": "Anime palette", "colours": palette},
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_settings(tolerance: float, radius: int, min_area: int) -> None:
    if not np.isfinite(tolerance) or not 0 <= tolerance <= 128:
        raise ValueError("面をまとめる色差は0〜128にして。")
    if type(radius) is not int or not 0 <= radius <= 8:
        raise ValueError("周囲をならす半径は0〜8にして。")
    if type(min_area) is not int or not 0 <= min_area <= 4096:
        raise ValueError("小さい面の整理は0〜4096ピクセルにして。")


def smooth_colours(rgb: np.ndarray, ink: np.ndarray, coverage: np.ndarray,
                   tolerance: float, radius: int, scale: int = 1) -> np.ndarray:
    """Local diffusion through four-connected, similar, visible non-ink pixels.

    Every step moves by only one pixel, so it cannot jump a one-pixel ink wall
    or a transparent gap. The original RGB guides all steps; strong edges stay
    closed. Coverage-weighted sums prevent hidden RGB from tinting the result.
    """
    source = rgb.astype(np.float32)
    mass = coverage.astype(np.float32) * ~ink
    total = source * mass[..., None]
    if not radius or tolerance == 0:
        return source
    def links(stride):
        weights = []
        for axis in (0, 1):
            first = (slice(None, -stride), slice(None)) if axis == 0 else (slice(None), slice(None, -stride))
            second = (slice(stride, None), slice(None)) if axis == 0 else (slice(None), slice(stride, None))
            difference = np.max(np.abs(source[first] - source[second]), axis=2)
            weight = np.exp(-2 * (difference / max(tolerance, 1)) ** 2)
            allowed = (difference <= tolerance) & (mass[first] > 0) & (mass[second] > 0)
            for step in range(1, stride):
                middle = ((slice(step, -stride + step), slice(None)) if axis == 0
                          else (slice(None), slice(step, -stride + step)))
                allowed &= (mass[middle] > 0)
                allowed &= np.max(np.abs(source[first] - source[middle]), axis=2) <= tolerance
                allowed &= np.max(np.abs(source[second] - source[middle]), axis=2) <= tolerance
            weights.append((first, second, (weight * allowed).astype(np.float32)))
        return weights
    weights = links(scale)
    # A one-pixel pass connects the even/odd subgrids before stride-2 passes.
    first_weights = links(1) if scale > 1 else weights
    for iteration in range(radius * 2 + (scale > 1)):
        next_total, next_mass = total.copy(), mass.copy()
        for first, second, weight in (first_weights if scale > 1 and iteration == 0 else weights):
            next_total[first] += total[second] * weight[..., None]
            next_total[second] += total[first] * weight[..., None]
            next_mass[first] += mass[second] * weight
            next_mass[second] += mass[first] * weight
        # Divide by five to keep repeated diffusion numerically bounded. The
        # same factor on mass and total leaves the weighted colour unchanged.
        total, mass = next_total / 5, next_mass / 5
    return np.where(mass[..., None] > 0, total / np.maximum(mass[..., None], 1e-20), source)


def find_regions(rgb: np.ndarray, valid: np.ndarray, tolerance: float,
                 min_area: int = 12, guide: np.ndarray | None = None) -> np.ndarray:
    """Accelerated region union; the original implementation is the fallback."""
    if not NUMBA_AVAILABLE:
        return find_regions_python(rgb, valid, tolerance, min_area, guide)
    height, width = valid.shape
    ids = np.arange(height * width, dtype=np.int32).reshape(height, width)
    a = np.concatenate((ids[:, :-1].ravel(), ids[:-1].ravel()))
    b = np.concatenate((ids[:, 1:].ravel(), ids[1:].ravel()))
    pixels = rgb.reshape(-1, 3).astype(np.float32)
    allowed = valid.ravel()[a] & valid.ravel()[b]
    difference = np.max(np.abs(pixels[a] - pixels[b]), axis=1)
    if guide is not None:
        original = guide.reshape(-1, 3).astype(np.float32)
        allowed &= np.max(np.abs(original[a] - original[b]), axis=1) <= tolerance
    selected = np.flatnonzero(allowed & (difference <= tolerance))
    order = selected[np.argsort(difference[selected], kind="stable")]
    return union_regions(pixels, a[order], b[order], valid.ravel(),
                         float(tolerance), int(min_area)).reshape(height, width)


def find_regions_python(rgb: np.ndarray, valid: np.ndarray, tolerance: float,
                        min_area: int = 12, guide: np.ndarray | None = None) -> np.ndarray:
    """Join adjacent pixels in order of colour difference, with bounded span.

    Each region's range in EVERY RGB channel stays <= tolerance. Checking
    whole-region bounds prevents a gradual ramp from chaining from one end of
    the gamut to the other. Ink and alpha=0 pixels have label -1.
    """
    height, width = valid.shape
    count = height * width
    ids = np.arange(count, dtype=np.int32).reshape(height, width)
    a = np.concatenate((ids[:, :-1].ravel(), ids[:-1].ravel()))
    b = np.concatenate((ids[:, 1:].ravel(), ids[1:].ravel()))
    pixels = rgb.reshape(-1, 3).astype(np.float32)
    allowed = valid.ravel()[a] & valid.ravel()[b]
    difference = np.max(np.abs(pixels[a] - pixels[b]), axis=1)
    if guide is not None:
        original = guide.reshape(-1, 3).astype(np.float32)
        allowed &= np.max(np.abs(original[a] - original[b]), axis=1) <= tolerance
    selected = np.flatnonzero(allowed & (difference <= tolerance))
    order = selected[np.argsort(difference[selected], kind="stable")]
    edges_a, edges_b = a[order].tolist(), b[order].tolist()
    parent = list(range(count))
    sizes = [1] * count
    low = pixels.tolist()
    high = [colour.copy() for colour in low]

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(first: int, second: int) -> int:
        if sizes[first] < sizes[second]:
            first, second = second, first
        parent[second] = first
        sizes[first] += sizes[second]
        low[first] = [min(low[first][c], low[second][c]) for c in range(3)]
        high[first] = [max(high[first][c], high[second][c]) for c in range(3)]
        return first

    for first, second in zip(edges_a, edges_b):
        first, second = root(first), root(second)
        if first == second:
            continue
        if all(max(high[first][c], high[second][c]) - min(low[first][c], low[second][c])
               <= tolerance for c in range(3)):
            join(first, second)

    # A second pass removes tiny islands only across already permitted edges,
    # with a looser, still bounded total colour range. Strong small highlights
    # and regions separated by ink are never forced into a neighbour.
    if min_area > 1:
        for first, second in zip(edges_a, edges_b):
            first, second = root(first), root(second)
            if first == second or min(sizes[first], sizes[second]) >= min_area:
                continue
            if all(max(high[first][c], high[second][c]) - min(low[first][c], low[second][c])
                   <= tolerance * 1.5 for c in range(3)):
                join(first, second)
    labels = np.asarray(parent, dtype=np.int32)
    while True:
        updated = labels[labels]
        if np.array_equal(updated, labels):
            break
        labels = updated
    labels[~valid.ravel()] = -1
    return labels.reshape(height, width)


@dataclass(frozen=True)
class ColourReference:
    step: int
    keys: np.ndarray
    means: np.ndarray
    selected: np.ndarray


def colour_keys(rgb: np.ndarray, step: int) -> np.ndarray:
    values = np.clip(rgb, 0, 255).astype(np.uint8).astype(np.int32) // step
    return (values[..., 0] << 16) | (values[..., 1] << 8) | values[..., 2]


def build_colour_reference(rgb, ink, coverage, palette, tolerance, max_colours, trace_support):
    """A shared native-size histogram: no connected-region means."""
    step = max(1, min(8, int(np.ceil(tolerance / 16))))
    valid = (coverage > 0) & ~ink
    keys, inverse = np.unique(colour_keys(rgb, step)[valid], return_inverse=True)
    weights = coverage[valid]
    mass = np.bincount(inverse, weights=weights, minlength=len(keys))
    means = np.stack([np.bincount(inverse, weights=rgb[valid, channel] * weights,
                                 minlength=len(keys)) / np.maximum(mass, 1e-20)
                      for channel in range(3)], axis=1)
    darkest = int(np.argmin(palette.astype(np.float32) @ np.array((.2126,.7152,.0722))))
    selected = select_palette(means, mass, palette, max_colours,
                              darkest if np.any(ink & (coverage > 0)) or not valid.any() else None,
                              trace_support)
    if tolerance > 0 and len(selected) > 1 and len(means):
        usage = np.zeros(len(selected), dtype=np.float64)
        targets = matching_features(palette[selected])
        for start in range(0, len(means), 512):
            nearest = np.argmin(matching_costs(matching_features(means[start:start+512]), targets), axis=1)
            usage += np.bincount(nearest, weights=mass[start:start+512], minlength=len(selected))
        order = np.argsort(-usage, kind='stable').tolist()
        if np.any(ink & (coverage > 0)):
            ink_position = int(np.flatnonzero(selected == darkest)[0])
            order.remove(ink_position)
            order.insert(0, ink_position)
        covered = np.zeros(len(selected), bool)
        kept = []
        for position in order:
            if covered[position]:
                continue
            kept.append(selected[position])
            distance = np.max(np.abs(palette[selected].astype(float) - palette[selected[position]]), axis=1)
            covered |= distance <= tolerance / 8
        selected = np.asarray(kept, dtype=np.int32)
    return ColourReference(step, keys, means, selected)


def classify_colours(rgb, valid, palette, reference, darkest):
    keys, inverse = np.unique(colour_keys(rgb, reference.step)[valid], return_inverse=True)
    means = np.stack(((keys >> 16) & 255, (keys >> 8) & 255, keys & 255), axis=1)
    means = np.minimum(means * reference.step + (reference.step - 1) / 2, 255).astype(np.float32)
    if len(reference.keys):
        positions = np.searchsorted(reference.keys, keys)
        known = positions < len(reference.keys)
        known[known] &= reference.keys[positions[known]] == keys[known]
        means[known] = reference.means[positions[known]]
    mapped = np.empty(len(keys), dtype=np.int32)
    targets = matching_features(palette[reference.selected])
    for start in range(0, len(keys), 512):
        costs = matching_costs(matching_features(means[start:start+512]), targets)
        mapped[start:start+512] = reference.selected[np.argmin(costs, axis=1)]
    result = np.full(valid.shape, darkest, dtype=np.uint16 if len(palette) > 256 else np.uint8)
    result[valid] = mapped[inverse]
    return result


def paint(rgb: np.ndarray, ink: np.ndarray, palette: np.ndarray,
          coverage: np.ndarray | None = None, tolerance: float = 32,
          radius: int = 3, min_area: int = 12, max_colours: int = 0,
          trace_support: bool = False, palette_out: list | None = None,
          reference: ColourReference | None = None, spatial_scale: int = 1) -> np.ndarray:
    """Global tone assignment, edge-aware local smoothing, small-island cleanup."""
    validate_settings(tolerance, radius, min_area)
    if type(spatial_scale) is not int or spatial_scale not in (1, 2):
        raise ValueError("spatial scale must be 1 or 2")
    if rgb.ndim != 3 or rgb.shape[2] != 3 or ink.shape != rgb.shape[:2]:
        raise ValueError("RGB画像と主線マスクのサイズが一致していない。")
    if coverage is None:
        coverage = np.ones(ink.shape, dtype=np.float32)
    coverage = np.asarray(coverage, dtype=np.float32)
    if (coverage.shape != ink.shape or not np.all(np.isfinite(coverage))
            or np.any((coverage < 0) | (coverage > 1))):
        raise ValueError("透明度は画像と同じサイズの0〜1の配列にして。")
    colours = np.asarray(validate_palette(np.asarray(palette).tolist()), dtype=np.uint8)
    if type(max_colours) is not int or not 0 <= max_colours <= 256:
        raise ValueError("使用色数は0（全色）〜256にして。")
    darkest = int(np.argmin(colours.astype(np.float32) @ np.array((.2126, .7152, .0722))))
    index_type = np.uint16 if len(colours) > 256 else np.uint8
    result = np.full(ink.shape, darkest, dtype=index_type)
    valid = (coverage > 0) & ~ink
    if not valid.any():
        if palette_out is not None:
            palette_out.append(np.array([darkest]))
        return result
    if reference is None:
        reference = build_colour_reference(rgb, ink, coverage, colours, tolerance, max_colours, trace_support)
    if palette_out is not None:
        palette_out.append(reference.selected)
    smoothed = smooth_colours(rgb, ink, coverage, tolerance, radius, spatial_scale)
    result = classify_colours(smoothed, valid, colours, reference, darkest)
    if min_area > 1:
        permitted = np.ones((len(colours), len(colours)), dtype=bool)
        features = matching_features(colours[reference.selected])
        permitted[np.ix_(reference.selected, reference.selected)] = matching_costs(features, features) < 256
        result = clean_colour_cells(result, np.ascontiguousarray(rgb), valid,
                                    float(tolerance), int(min_area * spatial_scale**2), permitted)
    return result


def select_palette(means: np.ndarray, weights: np.ndarray, palette: np.ndarray,
                   maximum: int, ink_index: int | None = None,
                   trace_support: bool = False) -> np.ndarray:
    """Choose an image-specific subset from a much larger paint database.

    Weighted greedy facility selection preserves colours which reduce the
    perceptual error over the largest visible areas. Ink and colour tracing
    share this same budget; no extra off-palette trace colours are invented.
    """
    if maximum == 0 or maximum >= len(palette):
        return np.arange(len(palette), dtype=np.int32)
    targets = matching_features(palette)
    histogram = np.zeros(len(palette), dtype=np.float64)
    sources = [(means, weights)]
    if trace_support:
        sources.append((means * .55, weights * .12))
    for colours, masses in sources:
        for start in range(0, len(colours), 2048):
            lab = matching_features(colours[start:start + 2048])
            nearest = np.argmin(matching_costs(lab, targets), axis=1)
            histogram += np.bincount(nearest, weights=masses[start:start + len(lab)], minlength=len(palette))
    occupied = np.flatnonzero(histogram)
    weights = histogram[occupied]
    if not len(occupied):
        return np.asarray([0 if ink_index is None else ink_index], dtype=np.int32)
    distances = matching_costs(targets[occupied], targets)
    if ink_index is None:
        first = int(np.argmin(weights @ distances))
    else:
        first = int(ink_index)
    chosen = [first]
    current = distances[:, first].copy()
    for _ in range(1, maximum):
        scores = weights @ np.minimum(current[:, None], distances)
        scores[chosen] = np.inf
        next_index = int(np.argmin(scores))
        if np.sum(weights * current) - scores[next_index] <= 1e-12:
            break
        chosen.append(next_index)
        current = np.minimum(current, distances[:, next_index])
    return np.asarray(chosen, dtype=np.int32)
