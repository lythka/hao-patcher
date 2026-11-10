"""Diagnostic: preserve the captured multifetch shader's logical 32-lane group.

Dispatch must remain exactly (32,1,1), with the original 62-pixel tile and grid.
This is a reviewed-kernel experiment, NOT a generic SIMD-to-threadgroup pass:
its exchanges must be reached by every thread. In particular, a barrier inside
its divergent histogram loop would deadlock. Fold the maximum locally there,
then reduce the local maxima at the existing reconverged lane-zero broadcast.

Other instructions and the input library's synchronization ABI stay intact.
The 640-byte static exchange storage is separate from MPS's dynamic histogram.
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
NEUTRAL = '0xC7EFFFFFE0000000'  # -FLT_MAX, as in the original histogram loop
CALL = re.compile(r'@air\.(simd_shuffle_down.v4f32|simd_broadcast.f32|'
                  r'simd_broadcast.u.i32|simd_ballot.i64|simd_max.f32)\((.*)\) #\d+')
EXPECTED = {'simd_shuffle_down.v4f32': 6, 'simd_ballot.i64': 4, 'barrier': 16,
            'simd_broadcast.f32': 20, 'simd_broadcast.u.i32': 16,
            'reconverged_max': 4, 'simd_max.f32': 4}


from runtime.logical32_helpers import helpers


def maximum_chains(text):
    """Check the observed max -> local fold -> exit phi -> freeze -> broadcast chains.

    This protects against reducing a different value or replacing a reduction
    whose result has another consumer. It is not a general convergence proof.
    """
    definitions = dict(re.findall(r'^  (%\w+) = (.*)$', text, re.M))
    def uses(value):
        found = []
        for line in text.splitlines():
            line = line.split(';', 1)[0].strip()
            match = re.match(r'(%\w+) = (.*)', line)
            name, rhs = match.groups() if match else (None, line)
            if re.search(re.escape(value) + r'(?!\w)', rhs):
                found.append(name)  # None also counts stores/returns as consumers.
        return found
    broadcasts = set()
    for value, rhs in definitions.items():
        if '@air.simd_max.f32(' not in rhs:
            continue
        consumers = uses(value)
        if len(consumers) != 1 or consumers[0] not in definitions:
            raise ValueError('histogram maximum has an additional consumer')
        folded = consumers[0]
        match = re.fullmatch(r'tail call fast float @air.fast_fmax.f32\(float (%\w+), float '
                             + re.escape(value) + r'\) #\d+', definitions[folded])
        if not match:
            raise ValueError('unrecognized local maximum fold')
        local = match[1]
        phis = uses(folded)
        if len(phis) != 2 or local not in phis or any(p not in definitions for p in phis):
            raise ValueError('maximum fold must feed only local and exit phis')
        for phi in phis:
            if not definitions[phi].startswith('phi float ') or NEUTRAL not in definitions[phi]:
                raise ValueError('unrecognized maximum neutral value')
        exited = next(p for p in phis if p != local)
        frozen = uses(exited)
        if len(frozen) != 1 or definitions.get(frozen[0]) != f'freeze float {exited}':
            raise ValueError('maximum exit must feed the captured freeze')
        readers = uses(frozen[0])
        if len(readers) != 1 or readers[0] not in definitions or not re.fullmatch(
                r'tail call fast float @air.simd_broadcast.f32\(float ' + re.escape(frozen[0])
                + r', i16 0\) #\d+', definitions[readers[0]]):
            raise ValueError('maximum exit must feed only the lane-zero broadcast')
        broadcasts.add(readers[0])
    if len(broadcasts) != 4:
        raise ValueError('expected four captured histogram maximum chains')
    return broadcasts


def rewrite(text):
    if text.count('define void @' + FUNCTION + '(') != 1 or '@logical32_' in text:
        raise ValueError('expected an untranslated multifetch module')
    if not re.search(r' = icmp ult i16 %\w+, 31$', text, re.M):
        raise ValueError('expected the captured last-neighbor lane exclusion')
    broadcasts = maximum_chains(text)
    modern = 'declare void @air.simdgroup.barrier(i32, i32, i32)' in text
    if not modern and 'declare void @air.simdgroup.barrier(i32, i32)' not in text:
        raise ValueError('unrecognized barrier ABI')
    counts, out = {}, []
    for line in text.splitlines():
        if line.startswith('define void @' + FUNCTION + '('):
            out += [line, '  %logical_lane = extractelement <3 x i16> %9, i32 0']
            continue
        if 'call' in line:
            match = CALL.search(line)
            if match:
                kind, args = match.groups()
                counts[kind] = counts.get(kind, 0) + 1
                if kind == 'simd_max.f32':
                    value = args[len('float '):]
                    line = line[:line.index('=') + 1] + f' select i1 true, float {value}, float {value}'
                else:
                    name = {'simd_shuffle_down.v4f32': 'shuffle_v4f32',
                            'simd_broadcast.f32': 'broadcast_f32',
                            'simd_broadcast.u.i32': 'broadcast_i32', 'simd_ballot.i64': 'ballot'}[kind]
                    if line.split('=')[0].strip() in broadcasts:
                        name, args = 'max', args[:-len(', i16 0')]
                        counts['reconverged_max'] = counts.get('reconverged_max', 0) + 1
                    line = line[:match.start()] + f'@logical32_{name}({args}, i16 %logical_lane)'
            elif '@air.simdgroup.barrier(' in line:
                counts['barrier'] = counts.get('barrier', 0) + 1
                line = line.replace('@air.simdgroup.barrier', '@air.wg.barrier').replace('i32 4)', 'i32 1)')
        out.append(line)
    if counts != EXPECTED:
        raise ValueError(f'unrecognized multifetch exchanges: {counts}')
    text = '\n'.join(out) + '\n'
    index = text.index('!llvm.module.flags')
    return text[:index] + helpers(modern) + '\n' + text[index:], dict(
        calls=counts, modern_barriers=modern, static_threadgroup_bytes=640)


def build(library, output):
    output.mkdir(parents=True)
    data = library.read_bytes()
    lib = mtlb.check(data)
    fn = next(f for f in lib['fns'] if f['name'] == FUNCTION)
    (output / 'original.air').write_bytes(mtlb.bitcode(data, lib, fn))
    text = subprocess.check_output(['xcrun', 'metal-objdump', '--disassemble', output / 'original.air'], text=True)
    text = text[text.index('; ModuleID'):]
    (output / 'original.ll').write_text(text)
    transformed, edits = rewrite(text)
    (output / 'logical32.ll').write_text(transformed)
    subprocess.run(['xcrun', 'metal-as', output / 'logical32.ll', '-o', output / 'logical32.air'], check=True)
    (output / 'logical32.metallib').write_bytes(mtlb.splice(data, {FUNCTION: (output / 'logical32.air').read_bytes()}))
    (output / 'diagnostic.json').write_text(json.dumps(dict(
        diagnostic_only=True, function=FUNCTION, input=str(library),
        input_sha256=hashlib.sha256(data).hexdigest(), edits=edits,
        dispatch='original grid and threads=(32,1,1); original dynamic threadgroup allocation'), indent=2) + '\n')
    record(output, [library])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.library, args.output)
