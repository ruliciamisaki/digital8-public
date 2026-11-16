"""Verify real Photoshop output against the standalone native renderer."""
from pathlib import Path
import json
import struct
import subprocess

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=ROOT/'photoshop/build/photoshop-smoke'


def rgba(name):
    with Image.open(OUTPUT/(name+'.png')) as image:
        return np.array(image.convert('RGBA'))


def equal_visible(expected,actual,label):
    np.testing.assert_array_equal(actual[...,3],expected[...,3],err_msg=label+' alpha')
    visible=expected[...,3]>0
    np.testing.assert_array_equal(actual[visible,:3],expected[visible,:3],err_msg=label+' visible RGB')
    return dict(stage=label,alpha_equal=True,visible_rgb_equal_percent=100.0,rgb_max_error=0)


def main():
    run=json.loads((OUTPUT/'results.json').read_text(encoding='utf-8-sig'))
    if not any(item['stage']=='complete' and item.get('ok') for item in run):
        raise AssertionError('Photoshop smoke script did not complete: '+str(run))
    for stage in ('smart-apply','smart-reopen','smart-source-change'):
        if not any(item['stage']==stage and item.get('snapshot_found') for item in run):
            raise AssertionError('Settings snapshot missing at '+stage)
    cleanup=next(item for item in run if item['stage']=='cleanup')
    assert cleanup['initial_documents']==cleanup['remaining_documents'],'Existing document count changed'
    records=[]
    for machine,source_name,actual_name in [(0,'before-0','normal-0'),(1,'before-1','normal-1'),
                                             (2,'before-2','normal-2'),(2,'changed-source','smart-recomputed')]:
        source,actual=rgba(source_name),rgba(actual_name)
        raw=OUTPUT/(source_name+'.rgba');output=OUTPUT/(actual_name+'-native.rgba')
        raw.write_bytes(struct.pack('<III',0x31493844,source.shape[1],source.shape[0])+source.tobytes())
        subprocess.run([str(ROOT/'photoshop/build/validation/native_cli.exe'),str(raw),str(output),str(machine),
                        '--settings',str(OUTPUT/f'settings-{machine}.txt')],check=True)
        expected=np.frombuffer(output.read_bytes()[12:],np.uint8).reshape(actual.shape)
        records.append(equal_visible(expected,actual,actual_name))
    records.append(equal_visible(rgba('normal-2'),rgba('smart-before'),'smart vs normal'))
    records.append(equal_visible(rgba('smart-before'),rgba('smart-reopened'),'PSD roundtrip'))
    assert not np.array_equal(rgba('smart-before'),rgba('smart-recomputed')),'Source edit did not change the smart-filter result'
    assert 0 in rgba('before-0')[...,3] and 128 in rgba('before-0')[...,3],'Alpha fixture is missing'
    (OUTPUT/'pixel-comparison.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')
    print('6 Photoshop output comparisons passed: visible RGB and alpha match exactly; PSD snapshot and source reevaluation passed.')


if __name__=='__main__':main()
