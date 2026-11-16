"""Compare complete C++ conversions with Python's global-tone path.

Reports differences honestly; this is not a claim of complete Python parity.
Python is used only by this development script, never by Digital8.8bf.
"""
from dataclasses import replace
from pathlib import Path
import json
import struct
import subprocess
import sys
import time

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'distribution/Digital8_Anime'))
import digital8_converter as converter


def main():
    output=ROOT/'photoshop/build/native-comparison';output.mkdir(exist_ok=True)
    gallery=ROOT/'distribution/Digital8_Anime/output_sample/anime'
    manifest=json.loads((gallery/'manifest.json').read_text(encoding='utf-8'))
    records=[]
    for sample in manifest['samples']:
        with Image.open(gallery/sample['source']) as opened:
            image=opened.convert('RGBA');image.thumbnail((640,640),Image.Resampling.LANCZOS)
        image.save(output/'input.png');rgba=np.asarray(image)
        (output/'input.rgba').write_bytes(struct.pack('<III',0x31493844,image.width,image.height)+rgba.tobytes())
        for machine_id,machine in enumerate(('pc8801','pc9801','anime')):
            options=replace(getattr(converter,machine+'_standard')(),resize_640=False)
            start=time.perf_counter();expected,_=converter.convert_image(output/'input.png',options);reference_seconds=time.perf_counter()-start
            args=[str(ROOT/'photoshop/build/validation/native_cli.exe'),str(output/'input.rgba'),str(output/'result.rgba'),str(machine_id),'--palette',options.palette_id,'--tall',str(int(options.tall_dots))]
            start=time.perf_counter();subprocess.run(args,check=True);native_seconds=time.perf_counter()-start
            actual=np.frombuffer((output/'result.rgba').read_bytes()[12:],np.uint8).reshape(rgba.shape);expected=np.asarray(expected.convert('RGBA'))
            alpha_equal=np.array_equal(actual[...,3],expected[...,3])
            if not alpha_equal:raise AssertionError('Alpha mismatch')
            record=dict(source=sample['name'],machine=machine,size=image.size,
                        exact_pixel_percent=100*float(np.mean(np.all(actual==expected,axis=2))),
                        mean_absolute_rgb=float(np.abs(actual[...,:3].astype(int)-expected[...,:3].astype(int)).mean()),
                        alpha_equal=alpha_equal,reference_seconds=reference_seconds,native_seconds=native_seconds)
            records.append(record);print(f"{sample['name']} {machine}: {record['exact_pixel_percent']:.4f}% exact, RGB MAE {record['mean_absolute_rgb']:.6f}, {native_seconds:.3f}s",flush=True)
            base=output/f"{Path(sample['name']).stem}_{machine}"
            Image.fromarray(actual).save(str(base)+'_native.png');Image.fromarray(expected).save(str(base)+'_python.png')
    (output/'results.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
