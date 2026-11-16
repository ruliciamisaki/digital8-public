#!/usr/bin/env python3
"""PC-9801 tile recipes, including optional three-colour patterns."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import tile_patterns


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
    palette: np.ndarray,
    half_tone: str = "checker",
    quarter_tone: str = "quarter_staggered",
    eighth_tone: str = 'eighth',
    three_112: str = 'none',
    three_111: str = 'none',
) -> tuple[TileRecipe, ...]:
    if half_tone not in HALF_TONES or quarter_tone not in QUARTER_TONES:
        raise ValueError("invalid half-tone or quarter-tone preference")
    tile_patterns.validate(eighth_tone,three_112,three_111)
    if palette.ndim != 2 or palette.shape[1] != 3 or len(palette) > 256:
        raise ValueError("palette must be Nx3 RGB")
    colours = palette.astype(np.float32)
    brightness = colours @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    unique_indices: list[int] = []
    seen: set[tuple[int, int, int]] = set()
    for index, colour in enumerate(palette):
        key = tuple(int(value) for value in colour)
        if key not in seen:
            seen.add(key)
            unique_indices.append(index)
    recipes: list[TileRecipe] = []
    for a in unique_indices:
        for b in unique_indices:
            names = ("solid",) if a == b else tuple(name for name in TEMPLATES if name != "solid")
            for name in names:
                if name in HALF_TONES and brightness[a] < brightness[b]:
                    continue
                rows = TEMPLATES[name]
                tile = tuple(a if char == "A" else b for row in rows for char in row)
                target = tuple(float(value) for value in colours[np.asarray(tile)].mean(axis=0))
                penalty = 0.0 if a == b else float(np.sum((colours[a] - colours[b]) ** 2)) * 0.002
                if name in HALF_TONES and name != half_tone:
                    penalty += 500.0
                if name in QUARTER_TONES and name != quarter_tone:
                    penalty += 500.0
                if name == 'eighth' and eighth_tone != 'eighth':
                    penalty += 500.0
                recipes.append(TileRecipe(name, a, b, tile, (len(rows), len(rows[0])), target, penalty))
    return tuple(recipes) + tile_patterns.extras(palette,TileRecipe,eighth_tone,three_112,three_111)
