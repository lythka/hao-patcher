"""Diagnostic only: retile MPS's fixed-32-lane multifetch kernel for one SIMD8 group.

This is not a generic compiler pass or a shipping repair. The shader's x tile,
last neighbor lane, histogram loops and rounded orientation work all assume 32.
Changing dispatch alone leaves gaps and does not test equivalent work.
The input can be Apple's library or the existing repaired library; all other
instructions, including their respective synchronization ABIs, stay intact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record import record

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import mtlb

FUNCTION = 'ExtractFeaturePointsAndOrientationFusedMultifetch'


def rewrite(text):
    edits = []
    patterns = (
        (r'( = mul nuw nsw i32 %\w+, )62$', '14', 1),
        (r'( = icmp ult i16 %\w+, )31$', '7', 1),
        (r'( = add i16 %\w+, )32$', '8', 16),
        (r'( = and i32 %\w+, )65504$', '65528', 4),
    )
    for pattern, value, expected in patterns:
        text, count = re.subn(pattern, lambda m: m[1] + value, text, flags=re.M)
        if count != expected:
            raise ValueError(f'unrecognized multifetch width expression: {pattern}: {count}, expected {expected}')
        edits.append(dict(pattern=pattern, value=value, count=count))
    return text, edits


def build(library, output):
    output.mkdir(parents=True)
    data = library.read_bytes()
    lib = mtlb.check(data)
    fn = next(f for f in lib['fns'] if f['name'] == FUNCTION)
    (output / 'original.air').write_bytes(mtlb.bitcode(data, lib, fn))
    text = subprocess.check_output(['xcrun', 'metal-objdump', '--disassemble', output / 'original.air'], text=True)
    text = text[text.index('; ModuleID'):]
    (output / 'original.ll').write_text(text)
    text, edits = rewrite(text)
    (output / 'width8.ll').write_text(text)
    subprocess.run(['xcrun', 'metal-as', output / 'width8.ll', '-o', output / 'width8.air'], check=True)
    (output / 'width8.metallib').write_bytes(mtlb.splice(data, {FUNCTION: (output / 'width8.air').read_bytes()}))
    (output / 'diagnostic.json').write_text(json.dumps(dict(
        diagnostic_only=True, function=FUNCTION, input=str(library),
        input_sha256=hashlib.sha256(data).hexdigest(), edits=edits,
        dispatch='threads=(8,1,1); grid.x=ceil((width-2*imageBorder)/14); original grid.y,z'), indent=2) + '\n')
    record(output, [library])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('library', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    build(a.library, a.output)
