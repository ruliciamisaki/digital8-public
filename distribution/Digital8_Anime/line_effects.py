"""Palette-preserving fractional ink growth and selective colour tracing."""

import math

import numpy as np
from PIL import Image, ImageFilter

from image_processing import srgb_to_oklab


def _shift(values: np.ndarray, dy: int, dx: int, outside=0) -> np.ndarray:
    shifted = np.roll(values, (dy, dx), axis=(0, 1))
    if dy > 0:
        shifted[:dy] = outside
    elif dy < 0:
        shifted[dy:] = outside
    if dx > 0:
        shifted[:, :dx] = outside
    elif dx < 0:
        shifted[:, dx:] = outside
    return shifted


def validate_growth(radius: float) -> None:
    if (isinstance(radius, bool) or not isinstance(radius, (float, int))
            or not np.isfinite(radius) or not 0 <= radius <= 16):
        raise ValueError("線を太くする値は0〜16にして。小数で微調整できます。")


def expand_ink(ink: np.ndarray, radius: float, visible: np.ndarray | None = None) -> np.ndarray:
    """Return the fully covered integer part; fractional growth is drawn later."""
    validate_growth(radius)
    whole = math.floor(radius)
    if whole:
        ink = np.asarray(Image.fromarray(ink.astype(np.uint8) * 255).filter(
            ImageFilter.MaxFilter(whole * 2 + 1))) > 0
    if visible is not None:
        ink = ink & visible
    return ink


def fractional_growth(indices: np.ndarray, ink: np.ndarray, radius: float,
                      palette: np.ndarray, permitted: np.ndarray | None = None,
                      visible: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Darken the next pixel ring proportionally, then snap to allowed colours.

    Coverage is f on a cardinal edge and f*f at a square corner. Neither new
    RGB colours nor additional transparency are introduced. A sparse palette
    can quantise adjacent fractional settings to the same visible result.
    """
    validate_growth(radius)
    fraction = radius - math.floor(radius)
    changed = np.zeros(ink.shape, bool)
    if fraction <= 1e-8 or not ink.any():
        return indices, changed
    if visible is None:
        visible = np.ones(ink.shape, bool)
    permitted = np.unique(indices[visible]) if permitted is None else np.asarray(permitted)
    if not len(permitted):
        return indices, changed
    rgb = palette.astype(np.float32)
    luma = rgb @ np.array((.2126, .7152, .0722))
    cardinal = np.zeros(ink.shape, bool)
    neighbours = np.full(ink.shape, -1, dtype=np.int32)
    best_luma = np.full(ink.shape, np.inf, dtype=np.float32)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
        near_ink = _shift(ink, dy, dx, False)
        if not dy or not dx:
            cardinal |= near_ink
        near_index = _shift(indices, dy, dx, 0)
        take = near_ink & (luma[near_index] < best_luma)
        neighbours[take] = near_index[take]
        best_luma[take] = luma[near_index[take]]
    fringe = (neighbours >= 0) & ~ink & visible
    if not fringe.any():
        return indices, changed
    opacity = np.where(cardinal[fringe], fraction, fraction * fraction)[:, None]
    original_ids = indices[fringe]
    blended = rgb[original_ids] * (1 - opacity) + rgb[neighbours[fringe]] * opacity
    targets = srgb_to_oklab(palette[permitted])
    target_chroma = np.linalg.norm(targets[:, 1:], axis=1)
    mapped = np.empty(len(blended), dtype=indices.dtype)
    for start in range(0, len(blended), 4096):
        part = srgb_to_oklab(blended[start:start + 4096])
        scores = np.sum((part[:, None] - targets[None]) ** 2, axis=2)
        chroma = np.linalg.norm(part[:, 1:], axis=1)
        cosine = (part[:, 1:] @ targets[:, 1:].T) / np.maximum(chroma[:, None] * target_chroma[None], 1e-8)
        same_hue = (cosine >= .80) | (target_chroma[None] < .02)
        same_hue[chroma <= .025] = target_chroma <= .035
        not_lighter = luma[permitted][None] <= luma[original_ids[start:start + len(part)]][:, None] + 1
        scores[~(same_hue & not_lighter)] = np.inf
        best = np.argmin(scores, axis=1)
        mapped[start:start + len(part)] = np.where(
            np.isfinite(scores[np.arange(len(part)), best]), permitted[best],
            original_ids[start:start + len(part)],
        )
    output = indices.copy()
    output[fringe] = mapped
    changed = fringe & (output != indices)
    return output, changed


def thin_ink(ink: np.ndarray, visible: np.ndarray, reach: int = 8,
             max_width: int = 3) -> np.ndarray:
    """Only narrow ink bands bracketed by visible fills on opposite sides.

    Large black masses, space backgrounds, and one-sided silhouettes do not
    become trace targets merely because their edge is near a bright fill.
    """
    eligible = np.zeros(ink.shape, bool)
    if not ink.any():
        return eligible
    # Opposite diagonal rays alone would mistake the convex corners of a
    # large black shape for short strokes. Protect masses which survive an
    # opening wider than an allowed line, including their reconstructed rim.
    size = (max_width + 2) | 1
    mask = Image.fromarray((ink & visible).astype(np.uint8) * 255)
    broad = np.asarray(mask.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))) > 0
    candidates = ink & visible & ~broad
    if not candidates.any():
        return eligible
    for dy, dx in ((1, 0), (0, 1), (1, 1), (1, -1)):
        distances = []
        for sign in (-1, 1):
            waiting = candidates.copy()
            distance = np.full(ink.shape, reach + max_width + 2, dtype=np.int16)
            for step in range(1, min(reach, max_width + 1) + 1):
                near_visible = _shift(visible, sign * dy * step, sign * dx * step, False)
                waiting &= near_visible
                near_ink = _shift(ink, sign * dy * step, sign * dx * step, True)
                found = waiting & ~near_ink
                distance[found] = step
                waiting[found] = False
                if not waiting.any():
                    break
            distances.append(distance)
        eligible |= (distances[0] + distances[1] <= max_width + 1)
    return eligible & candidates


def trace_indices(indices: np.ndarray, ink: np.ndarray, palette: np.ndarray,
                  strength: float = .55, reach: int = 8,
                  visible: np.ndarray | None = None,
                  permitted: np.ndarray | None = None,
                  reference_ids: np.ndarray | None = None,
                  reference_colours: np.ndarray | None = None,
                  max_width: int = 3) -> np.ndarray:
    """Trace thin source ink using solid fills or virtual tile-average colours.

    The RGB output stays within the selected palette. If there is no suitably
    darker colour with a similar hue (common in digital 8-colour palettes),
    retain the original ink instead of introducing a different hue.
    """
    if not np.isfinite(strength) or not .1 <= strength <= .95:
        raise ValueError("色トレスの濃さは0.10〜0.95にして。小さいほど濃くなります。")
    if visible is None:
        visible = np.ones(ink.shape, bool)
    eligible = thin_ink(ink, visible, reach, max_width)
    if not eligible.any():
        return indices.copy()
    rgb = palette.astype(np.float32)
    permitted = np.unique(indices[visible]) if permitted is None else np.asarray(permitted)
    references = rgb if reference_colours is None else np.asarray(reference_colours, dtype=np.float32)
    nearest = indices.astype(np.int32).copy() if reference_ids is None else reference_ids.astype(np.int32).copy()
    if nearest.shape != ink.shape:
        raise ValueError("色トレス用の面IDのサイズが一致していない。")
    luma = rgb @ np.array((.2126, .7152, .0722))
    ref_luma = references @ np.array((.2126, .7152, .0722))
    lab = srgb_to_oklab(rgb)
    ref_lab = srgb_to_oklab(references)
    chroma = np.linalg.norm(lab[:, 1:], axis=1)
    mapping = np.full(len(references), -1, dtype=np.int32)
    for index in np.unique(nearest[~ink & visible]):
        darker = permitted[luma[permitted] < min(ref_luma[index] - 1, ref_luma[index] * .92)]
        source_chroma = float(np.linalg.norm(ref_lab[index, 1:]))
        if source_chroma > .025:
            cosine = (lab[darker, 1:] @ ref_lab[index, 1:]) / np.maximum(chroma[darker] * source_chroma, 1e-8)
            darker = darker[(chroma[darker] > .012) & (cosine >= .80)]
        else:
            darker = darker[chroma[darker] <= .035]
        if len(darker):
            target = srgb_to_oklab((references[index] * strength)[None])[0]
            mapping[index] = darker[np.argmin(np.sum((lab[darker] - target) ** 2, axis=1))]
    known = ~ink & visible
    traversable = (~ink | eligible) & visible
    output = indices.copy()
    for _ in range(reach):
        best = np.full(ink.shape, -1, dtype=np.int32)
        best_luma = np.full(ink.shape, np.inf, dtype=np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbour = _shift(nearest, dy, dx, 0)
            allowed = _shift(known, dy, dx, False) & ~known & traversable
            take = allowed & (ref_luma[neighbour] < best_luma)
            best[take] = neighbour[take]
            best_luma[take] = ref_luma[neighbour[take]]
        discovered = best >= 0
        if not discovered.any():
            break
        nearest[discovered] = best[discovered]
        known |= discovered
        change = discovered & eligible
        mapped = mapping[best[change]]
        ys, xs = np.nonzero(change)
        keep = mapped >= 0
        output[ys[keep], xs[keep]] = mapped[keep]
    return output
