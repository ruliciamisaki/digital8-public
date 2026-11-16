"""Versioned JSON recipes in PNG iTXt; no executable workflow or absolute paths."""

import hashlib
import json
import math
from dataclasses import asdict, fields, replace
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

KEY = "Digital8"
SCHEMA_VERSION = 1
RENDERER = "digital8-anime-v4"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def png_info(source, settings, palette, export_alpha_mask=False, invert_alpha_mask=False):
    source = Path(source)
    recipe = {"schema_version": SCHEMA_VERSION, "renderer": RENDERER,
              "settings": asdict(settings), "palette": list(palette),
              "source": {"name": source.name, "sha256": file_digest(source)},
              "export": {"alpha_mask": bool(export_alpha_mask), "invert_alpha_mask": bool(invert_alpha_mask)}}
    info = PngInfo()
    info.add_itxt(KEY, json.dumps(recipe, ensure_ascii=False, allow_nan=False, separators=(',', ':')), zip=True)
    return info


def read_recipe(path: Path):
    with Image.open(path) as image:
        text = image.info.get(KEY) if image.format == 'PNG' else None
    if text is None:
        return None
    try:
        recipe = json.loads(text)
    except (ValueError, TypeError) as error:
        raise ValueError('PNG内のDigital8設定が壊れている。') from error
    if (not isinstance(recipe, dict) or type(recipe.get('schema_version')) is not int
            or recipe['schema_version'] != SCHEMA_VERSION):
        raise ValueError('このPNGのDigital8設定形式には対応していない。')
    return recipe


def decode_settings(recipe):
    # Import lazily so saving recipes does not introduce a converter import cycle.
    import digital8_converter as converter
    import anime_paint

    values = recipe.get('settings')
    default = converter.ConversionOptions()
    expected = {field.name for field in fields(default)}
    added = {'eighth_tone', 'three_112', 'three_111', 'retro_palette'}
    obsolete = {'method', 'frequent_mode', 'fill_method', 'colour_coherence', 'fill_stability'}
    if (not isinstance(values, dict) or set(values) - expected - obsolete
            or expected - set(values) - added):
        raise ValueError('PNG内の変換設定の項目が一致しない。')
    legacy = bool(set(values) & obsolete)
    values = {**{key: getattr(default, key) for key in added},
              **{key: value for key, value in values.items() if key in expected}}
    for key, value in values.items():
        if key in ('anime_palette', 'retro_palette'):
            if value is not None:
                values[key] = anime_paint.validate_palette(value)
            continue
        initial = getattr(default, key)
        if isinstance(initial, float):
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
        else:
            valid = type(value) is type(initial)
        if not valid:
            raise ValueError(f'PNG内の設定値が不正: {key}')
    if legacy:
        # The old frequent-pattern controls can exceed V4's input ranges.
        if 121 <= values['line_threshold'] <= 255:
            values['line_threshold'] = 120
        if 1 <= values['colour_groups'] <= 4096:
            values['colour_groups'] = min(64, max(2, values['colour_groups']))
    settings = converter.ConversionOptions(**values)
    converter._validate_options(settings)
    snapshot = anime_paint.validate_palette(recipe.get('palette'))
    if settings.machine == 'anime':
        try:
            current = converter.palette_for('anime', settings.palette_id).colours
        except ValueError:
            current = None
        if current != snapshot:
            settings = replace(settings, palette_id='custom', anime_palette=snapshot)
        elif settings.anime_palette is not None:
            settings = replace(settings, anime_palette=snapshot)
    else:
        if settings.retro_palette is not None:
            converter.validate_retro_palette(settings.machine, settings.retro_palette)
        try:
            current = converter.palette_for(settings.machine, settings.palette_id).colours
        except ValueError:
            current = None
        if current != snapshot or settings.retro_palette is not None:
            # Older PC98 presets contain 14/15 unique colours. Preserve those
            # snapshots as 16 editable slots without changing their colours.
            if settings.machine == 'pc9801' and len(snapshot) in (14, 15):
                snapshot = snapshot + (snapshot[-1],) * (16 - len(snapshot))
            snapshot = converter.validate_retro_palette(settings.machine, snapshot)
            settings = replace(settings, palette_id='custom', retro_palette=snapshot)
    export = recipe.get('export', {})
    if (not isinstance(export, dict) or any(type(export.get(key, False)) is not bool
                                          for key in ('alpha_mask','invert_alpha_mask'))):
        raise ValueError('PNG内のマスク出力設定が不正。')
    return settings


def find_original(png_path: Path, recipe):
    """Relink only an identical sibling image, never follow an embedded path."""
    source = recipe.get('source', {})
    if not isinstance(source, dict):
        return None
    name, digest = source.get('name'), source.get('sha256')
    if (not isinstance(name, str) or not name or name in ('.', '..')
            or any(char in name for char in ('/', '\\', ':', '\0'))
            or not isinstance(digest, str) or len(digest) != 64):
        return None
    candidate = Path(png_path).parent / name
    if candidate.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.bmp','.gif'):
        return None
    try:
        if (candidate.resolve() != Path(png_path).resolve() and candidate.is_file()
                and file_digest(candidate) == digest):
            return candidate
    except OSError:
        pass
    return None
