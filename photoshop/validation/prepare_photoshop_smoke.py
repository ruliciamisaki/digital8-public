"""Prepare only local, disposable inputs and snapshots for Photoshop tests."""
from pathlib import Path
import struct
import subprocess

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
output = ROOT/'photoshop/build/photoshop-smoke'
output.mkdir(exist_ok=True)
with Image.open(ROOT/'distribution/Digital8_Anime/output_sample/anime/source/00505-1900052826.png') as opened:
    image = opened.convert('RGBA')
    image.thumbnail((256, 256), Image.Resampling.LANCZOS)
rgba = np.array(image)
rgba[16:28, 16:28, 3] = 0
rgba[40:52, 16:28, 3] = 128
Image.fromarray(rgba).save(output/'input.png')
(output/'input.rgba').write_bytes(struct.pack('<III', 0x31493844, image.width, image.height)+rgba.tobytes())
for machine in range(3):
    subprocess.run([str(ROOT/'photoshop/build/validation/native_cli.exe'),
                    str(output/'input.rgba'), str(output/f'expected-{machine}.rgba'), str(machine),
                    '--save-settings', str(output/f'settings-{machine}.txt')], check=True)
print(output)
