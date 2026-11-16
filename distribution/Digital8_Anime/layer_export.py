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
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class ConversionDocument:
    image: Image.Image
    alpha: Image.Image | None
    labels: np.ndarray
    names: dict[int, str]

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


def write_psd(path: Path, document: ConversionDocument) -> None:
    width, height = document.image.size
    if max(width, height) > 30000:
        raise ValueError("PSDは1辺30000pxまで。大きい画像はZIPで保存して。")
    ids = document.layer_ids()
    # An empty transparent image still needs a transparent layer record.
    if not ids:
        ids = [0]
    if len(ids) > 32767:
        raise ValueError("PSDのレイヤー数を超えています。ZIPで保存して。")
    records = []
    # Spool encoded channel streams rather than retaining every layer in RAM.
    with tempfile.TemporaryFile() as channel_file:
        for index in reversed(ids):  # PSD layer records use top-to-bottom order.
            image, (left, top) = document.layer(index, crop=True)
            rgba = np.asarray(image)
            channels = [_channel_data(rgba[:, :, channel]) for channel in (0, 1, 2, 3)]
            record = struct.pack(">4iH", top, left, top + image.height, left + image.width, 4)
            record += b"".join(struct.pack(">hI", channel_id, len(data))
                               for channel_id, data in zip((0, 1, 2, -1), channels))
            record += b"8BIMnorm" + bytes((255, 0, 8, 0))
            name = document.names.get(index, "Transparent")
            ascii_name = name.encode("ascii", "replace")[:255]
            pascal = bytes((len(ascii_name),)) + ascii_name
            pascal += b"\0" * (-len(pascal) % 4)
            unicode_name = name.encode("utf-16-be")
            unicode_data = struct.pack(">I", len(unicode_name) // 2) + unicode_name
            unicode_data += b"\0" * (-len(unicode_data) % 4)
            extra = b"\0" * 8 + pascal + b"8BIMluni" + struct.pack(">I", len(unicode_data)) + unicode_data
            record += struct.pack(">I", len(extra)) + extra
            records.append(record)
            for data in channels:
                channel_file.write(data)
        layer_size = 2 + sum(map(len, records)) + channel_file.tell()
        padded_size = layer_size + layer_size % 2
        with Path(path).open("wb") as stream:
            stream.write(b"8BPS" + struct.pack(">H6sHIIHH", 1, b"\0" * 6, 4, height, width, 8, 3))
            stream.write(b"\0" * 8)  # No colour mode data or image resources.
            stream.write(struct.pack(">IIh", 4 + padded_size + 4, padded_size, -len(ids)))
            stream.write(b"".join(records))
            channel_file.seek(0)
            while chunk := channel_file.read(1024 * 1024):
                stream.write(chunk)
            stream.write(b"\0" * (layer_size % 2))
            stream.write(b"\0" * 4)  # Empty global layer mask.
            rgba = np.array(document.image.convert("RGBA"))
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
