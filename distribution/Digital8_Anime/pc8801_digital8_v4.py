#!/usr/bin/env python3
"""PC-8801 tile recipes, including optional three-colour patterns."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import tile_patterns


# RGB table and observed mix pairs used by the Photoshop V4 renderer.
PALETTE = (
    (0, 0, 0), (0, 0, 255), (255, 0, 0), (255, 0, 255),
    (0, 255, 0), (0, 255, 255), (255, 255, 0), (255, 255, 255),
)
OBSERVED_PAIRS = frozenset((
    (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 6), (0, 7),
    (2, 3), (2, 6), (3, 7), (4, 7), (5, 7), (6, 7),
))

# Row order and phase are fixed.  The display doubles each native row in 200-line mode.
TEMPLATES: dict[str, tuple[str, ...]] = {
    "solid": ("A",),
    "vertical": ("AB",),
    "checker": ("AB", "BA"),
    "quarter_single": ("AB", "AA"),
    "quarter_staggered": ("AABA", "BAAA"),
    "eighth": ("AAAB", "AAAA"),
}
HALF_TONES = ("checker", "vertical")
QUARTER_TONES = ("quarter_staggered", "quarter_single")


@dataclass(frozen=True)
class TileRecipe:
    name: str
    a: int
    b: int
    tile: tuple[int, ...]
    shape: tuple[int, int]
    target: tuple[float, float, float]
    penalty: float
    c: int | None = None


def build_recipes(
    half_tone: str = "checker",
    quarter_tone: str = "quarter_staggered",
    eighth_tone: str = 'eighth',
    three_112: str = 'none',
    three_111: str = 'none',
    palette_override: np.ndarray | None = None,
    *, four_colour: bool = False, five_colour: bool = False,
) -> tuple[TileRecipe, ...]:
    if half_tone not in HALF_TONES or quarter_tone not in QUARTER_TONES:
        raise ValueError("invalid half-tone or quarter-tone preference")
    tile_patterns.validate(eighth_tone, three_112, three_111, four_colour, five_colour)
    palette = np.asarray(PALETTE if palette_override is None else palette_override, dtype=np.float32)
    if palette.shape != (8, 3) or not np.isfinite(palette).all() or np.any((palette < 0) | (palette > 255)):
        raise ValueError("PC-8801 palette must contain eight RGB colours (0..255)")
    brightness = palette @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    recipes: list[TileRecipe] = []
    for a in range(len(palette)):
        for b in range(len(palette)):
            if a == b:
                names = ("solid",)
            else:
                names = tuple(name for name in TEMPLATES if name != "solid")
            for name in names:
                # The half-tone reversed A/B pair has the same average.  Pick
                # one fixed polarity, while 3:1 and 7:1 keep both directions.
                if name in HALF_TONES and brightness[a] < brightness[b]:
                    continue
                rows = TEMPLATES[name]
                tile = tuple(a if char == "A" else b for row in rows for char in row)
                target = tuple(float(value) for value in palette[np.asarray(tile)].mean(axis=0))
                if name == "solid":
                    penalty = 0.0
                else:
                    contrast = float(np.sum((palette[a] - palette[b]) ** 2))
                    penalty = contrast * 0.002
                    if tuple(sorted((a, b))) not in OBSERVED_PAIRS:
                        penalty += 240.0
                    if name in HALF_TONES and name != half_tone:
                        penalty += 500.0
                    if name in QUARTER_TONES and name != quarter_tone:
                        penalty += 500.0
                    if name == 'eighth' and eighth_tone != 'eighth':
                        penalty += 500.0
                    # Prefer black/white for neutral tones over complementary
                    # chromatic pairs with the same RGB average.
                    if a not in (0, 7) and b not in (0, 7) and a ^ b == 7:
                        penalty += 1800.0
                recipes.append(TileRecipe(name, a, b, tile, (len(rows), len(rows[0])), target, penalty))
    return tuple(recipes) + tile_patterns.extras(palette,TileRecipe,eighth_tone,three_112,three_111,OBSERVED_PAIRS,
                                                 four_colour=four_colour, five_colour=five_colour)
