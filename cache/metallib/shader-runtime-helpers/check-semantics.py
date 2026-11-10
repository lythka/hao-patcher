#!/usr/bin/env python3
"""Run authored IR on the CPU with mocks; no GPU correctness is asserted."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output):
    output = output.resolve()
    if output.exists() or REPO / 'work' not in output.parents:
        raise ValueError('requires fresh directory under work/')
    output.mkdir(parents=True)
    objects, commands = [], []
    clang = Path(subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip())

    def command(args):
        result = subprocess.run(args, capture_output=True, text=True)
        commands.append(dict(args=args, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        if result.returncode:
            raise ValueError(result.stderr or 'semantic check failed')
        return result.stdout

    for name in ('rotate', 'group-size', 'insert-bits', 'extract-bits'):
        source = (HERE / (name + '.ll')).read_text()
        for original, replacement in (('llvm.igil.get.simd.id', 'mock_lane'),
                                      ('llvm.igil.get.simd.size', 'mock_width'),
                                      ('llvm.igil.simd.shuffle', 'mock_shuffle')):
            source = source.replace(original, replacement)
        # Mocks read mutable test state, unlike real per-thread GPU intrinsics.
        source = source.replace(' nounwind readnone', ' nounwind')
        path = output / (name + '.ll')
        path.write_text(source)
        obj = output / (name + '.o')
        objects.append(str(obj))
        command([str(clang), '-O2', '-Wno-override-module', '-c', str(path), '-o', str(obj)])
    command([str(clang), '-O2', '-Wall', '-Wextra', '-Werror', str(HERE / 'semantic-test.c'),
             *objects, '-o', str(output / 'test')])
    result = json.loads(command([str(output / 'test')]))
    result.update(classification='FACT', native_acceptance=False,
                  source_sha256={name: sha(HERE / name) for name in
                                 ('check-semantics.py', 'semantic-test.c', 'rotate.ll',
                                  'group-size.ll', 'insert-bits.ll', 'extract-bits.ll')},
                  clang=dict(path=str(clang), sha256=sha(clang)))
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('rotation_cases', 'bit_cases', 'width_cases', 'passed')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
