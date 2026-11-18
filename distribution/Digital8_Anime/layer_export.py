"""Disjoint colour/recipe layers, transparent PNG ZIP and 8-bit RGB PSD export.

PSD layout follows Adobe's Photoshop File Formats Specification:
https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/
"""

from __future__ import annotations

import io
import json
import struct
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import numpy as np
from PIL import Image


PSD_EXTRA_GROUPS = {"colour": "使用色ごと", "fill": "塗りごと"}
PSD_LAYER_MODES = ("pattern", *PSD_EXTRA_GROUPS)


@dataclass
class ConversionDocument:
    image: Image.Image
    alpha: Image.Image | None
    labels: np.ndarray
    names: dict[int, str]
    tile_colours: dict[int, tuple[int, int, int]] = field(default_factory=dict)

    def for_psd_layers(self, mode: str) -> ConversionDocument:
        if mode == "pattern":
            return self
        if mode == "colour":
            rgb = np.asarray(self.image.convert("RGB"))
            labels = (rgb[:, :, 0].astype(np.int32) << 16)
            labels |= rgb[:, :, 1].astype(np.int32) << 8
            labels |= rgb[:, :, 2]
            visible = labels if self.alpha is None else labels[np.asarray(self.alpha) > 0]
            names = {int(code): f"#{code:06X}" for code in np.unique(visible)}
            return ConversionDocument(self.image, self.alpha, labels, names)
        if mode != "fill":
            raise ValueError("PSDのレイヤー分割方法が不正です。")
        if not self.tile_colours:
            return self

        # Ownership identifies the whole tile-filled area, including cropped or
        # partly transparent tiles and resampled boundaries.
        lookup = np.zeros((int(self.labels.max()) + 1, 4), dtype=np.uint8)
        names = self.names.copy()
        for index, colour in self.tile_colours.items():
            if index >= len(lookup):
                continue
            lookup[index] = (*colour, 1)
            names[index] = f"Tile {index:04d} #{bytes(colour).hex().upper()}"
        mixed = lookup[self.labels, 3] != 0
        image = self.image.convert("RGBA" if self.alpha is not None else "RGB")
        pixels = np.array(image)
        pixels[mixed, :3] = lookup[self.labels[mixed], :3]
        return ConversionDocument(Image.fromarray(pixels, image.mode), self.alpha, self.labels, names)

    def layer_ids(self) -> list[int]:
        alpha = np.asarray(self.image.convert("RGBA"))[:, :, 3]
        return [int(index) for index in np.unique(self.labels[alpha > 0])]

    def layer(self, index: int, crop: bool = False) -> tuple[Image.Image, tuple[int, int]]:
        rgba = np.array(self.image.convert("RGBA"))
        rgba[:, :, 3] = np.where(self.labels == index, rgba[:, :, 3], 0)
        rgba[rgba[:, :, 3] == 0, :3] = 0
        layer = Image.fromarray(rgba, "RGBA")
        if crop:
            box = layer.getchannel("A").getbbox()
            if box is None:
                return Image.new("RGBA", (1, 1)), (0, 0)
            return layer.crop(box), box[:2]
        return layer, (0, 0)


def write_zip(path: Path, document: ConversionDocument) -> None:
    manifest = {"version": 1, "size": list(document.image.size), "order": "bottom-to-top",
                "partition": "Disjoint visible colour/recipe pixels; no inferred hidden paint", "layers": []}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for number, index in enumerate(document.layer_ids(), 1):
            layer, _offset = document.layer(index)
            filename = f"layers/{number:04d}.png"
            buffer = io.BytesIO()
            layer.save(buffer, format="PNG")
            archive.writestr(filename, buffer.getvalue())
            manifest["layers"].append({"file": filename, "name": document.names.get(index, str(index)), "id": index})
        buffer = io.BytesIO()
        document.image.save(buffer, format="PNG")
        archive.writestr("composite.png", buffer.getvalue())
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def _packbits(row: bytes) -> bytes:
    """Encode a PSD RLE scanline (runs of 3+, literals of at most 128)."""
    if not row:
        return b""
    encoded = bytearray()
    values = np.frombuffer(row, dtype=np.uint8)
    starts = np.r_[0, np.flatnonzero(values[1:] != values[:-1]) + 1]
    lengths = np.diff(np.r_[starts, len(row)])
    position = 0

    def literals(start: int, end: int) -> None:
        for offset in range(start, end, 128):
            part = row[offset:min(end, offset + 128)]
            encoded.append(len(part) - 1)
            encoded.extend(part)

    for start, length in zip(starts[lengths >= 3].tolist(), lengths[lengths >= 3].tolist()):
        literals(position, start)
        remaining = length
        while remaining:
            count = min(remaining, 128)
            encoded.extend(((257 - count) % 256, row[start]))
            remaining -= count
        position = start + length
    literals(position, len(row))
    return bytes(encoded)


def _channel_data(plane: np.ndarray) -> bytes:
    rows = [_packbits(row.tobytes()) for row in plane]
    # A PackBits NOP (128) supplies even-byte row/channel alignment without
    # changing decoded pixels. Both Photoshop and Pillow accept this layout.
    rows = [row + b"\x80" if len(row) % 2 else row for row in rows]
    if any(len(row) > 65535 for row in rows):
        return b"\0\0" + plane.tobytes()
    return b"\0\1" + struct.pack(f">{len(rows)}H", *(len(row) for row in rows)) + b"".join(rows)


def _layer_bounds(labels: np.ndarray, alpha: np.ndarray, ids: list[int]) -> np.ndarray:
    height, width = labels.shape
    sorted_ids = np.asarray(ids)
    left = np.full(len(ids), width)
    top = np.full(len(ids), height)
    right = np.zeros(len(ids), dtype=np.intp)
    bottom = np.zeros(len(ids), dtype=np.intp)
    # Work in strips to bound temporary space even on large canvases.
    for start in range(0, height, 128):
        rows, cols = np.nonzero(alpha[start:start + 128])
        rows += start
        groups = np.searchsorted(sorted_ids, labels[rows, cols])
        np.minimum.at(left, groups, cols)
        np.minimum.at(top, groups, rows)
        np.maximum.at(right, groups, cols + 1)
        np.maximum.at(bottom, groups, rows + 1)
    return np.stack((left, top, right, bottom), axis=1)


def _layer_record(bounds, channels, name, flags=8, tagged_blocks=b"") -> bytes:
    record = struct.pack(">4iH", *bounds, len(channels))
    record += b"".join(struct.pack(">hI", channel_id, len(data))
                       for channel_id, data in zip((0, 1, 2, -1), channels))
    record += b"8BIMnorm" + bytes((255, 0, flags, 0))
    ascii_name = name.encode("ascii", "replace")[:255]
    pascal = bytes((len(ascii_name),)) + ascii_name
    pascal += b"\0" * (-len(pascal) % 4)
    unicode_name = name.encode("utf-16-be")
    unicode_data = struct.pack(">I", len(unicode_name) // 2) + unicode_name
    unicode_data += b"\0" * (-len(unicode_data) % 4)
    extra = b"\0" * 8 + pascal + b"8BIMluni" + struct.pack(">I", len(unicode_data)) + unicode_data + tagged_blocks
    return record + struct.pack(">I", len(extra)) + extra


def _write_layer_set(document: ConversionDocument, rgba: np.ndarray, records: list[bytes],
                     channel_file: BinaryIO, group: str | None, visible: bool) -> None:
    alpha = rgba[:, :, 3]
    ids = np.unique(document.labels[alpha > 0]).tolist()
    # An empty transparent image still needs a transparent layer record.
    record_count = max(1, len(ids)) + (2 if group else 0)
    if len(records) + record_count > 32767:
        raise ValueError("PSDのレイヤー数を超えています。追加するレイヤーを減らしてください。")
    if ids:
        bounds = _layer_bounds(document.labels, alpha, ids)
    else:
        ids = [0]
        bounds = [(0, 0, 1, 1)]
    # PSD stores the bottom divider, the member layers, then the folder record.
    if group:
        empty = (b"\0\0",) * 4
        divider = b"8BIMlsct" + struct.pack(">II", 4, 3)
        records.append(_layer_record((0, 0, 0, 0), empty, "</Layer group>", 0x18, divider))
        channel_file.write(b"".join(empty))
    for index, (left, top, right, bottom) in zip(reversed(ids), reversed(bounds)):
        crop = np.s_[top:bottom, left:right]
        layer = rgba[crop].copy()
        layer[(document.labels[crop] != index) | (layer[:, :, 3] == 0)] = 0
        channels = [_channel_data(layer[:, :, channel]) for channel in (0, 1, 2, 3)]
        records.append(_layer_record((top, left, bottom, right), channels,
                                     document.names.get(index, "Transparent")))
        channel_file.write(b"".join(channels))
    if group:
        # Show the primary group expanded, and keep alternate groups collapsed and hidden.
        divider = b"8BIMlsct" + struct.pack(">II", 12, 1 if visible else 2) + b"8BIMpass"
        flags = 0x18 if visible else 0x1A
        records.append(_layer_record((0, 0, 0, 0), empty, group, flags, divider))
        channel_file.write(b"".join(empty))


def write_psd(path: Path, document: ConversionDocument, *, extra_layers: str | Iterable[str] = ()) -> None:
    width, height = document.image.size
    if max(width, height) > 30000:
        raise ValueError("PSDは1辺30000pxまで。大きい画像はZIPで保存して。")
    requested = {extra_layers} if isinstance(extra_layers, str) else set(extra_layers)
    if requested.difference(PSD_LAYER_MODES):
        raise ValueError("PSDのレイヤー分割方法が不正です。")
    requested.discard("pattern")
    rgba = np.array(document.image.convert("RGBA"))
    records = []
    # Spool encoded channel streams rather than retaining every layer in RAM.
    with tempfile.TemporaryFile() as channel_file:
        for mode, group in reversed(PSD_EXTRA_GROUPS.items()):
            if mode in requested:
                split = document.for_psd_layers(mode)
                pixels = rgba if split.image is document.image else np.asarray(split.image.convert("RGBA"))
                _write_layer_set(split, pixels, records, channel_file, group, visible=False)
                del split, pixels
        _write_layer_set(document, rgba, records, channel_file, "タイル模様を保持" if requested else None, visible=True)
        layer_size = 2 + sum(map(len, records)) + channel_file.tell()
        padded_size = layer_size + layer_size % 2
        with Path(path).open("wb") as stream:
            stream.write(b"8BPS" + struct.pack(">H6sHIIHH", 1, b"\0" * 6, 4, height, width, 8, 3))
            stream.write(b"\0" * 8)  # No colour mode data or image resources.
            stream.write(struct.pack(">IIh", 4 + padded_size + 4, padded_size, -len(records)))
            stream.write(b"".join(records))
            channel_file.seek(0)
            while chunk := channel_file.read(1024 * 1024):
                stream.write(chunk)
            stream.write(b"\0" * (layer_size % 2))
            stream.write(b"\0" * 4)  # Empty global layer mask.
            # Photoshop's merged RGB preview is stored over white; layer
            # channels above retain straight RGB and exact transparency.
            coverage = rgba[:, :, 3:4].astype(np.uint32)
            rgba[:, :, :3] = ((rgba[:, :, :3].astype(np.uint32) * coverage
                              + 255 * (255 - coverage) + 127) // 255).astype(np.uint8)
            rows = [_packbits(row.tobytes()) for channel in range(4) for row in rgba[:, :, channel]]
            if any(len(row) > 65535 for row in rows):
                stream.write(b"\0\0")
                for channel in range(4):
                    stream.write(rgba[:, :, channel].tobytes())
            else:
                stream.write(b"\0\1" + struct.pack(f">{len(rows)}H", *(len(row) for row in rows)))
                for row in rows:
                    stream.write(row)
