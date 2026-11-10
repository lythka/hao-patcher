#!/usr/bin/env python3
"""Run the exact reduction control flow on a CPU with checked intrinsic stand-ins."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent


def run(output):
    output = output.resolve()
    if output.exists():
        raise ValueError('requires fresh output')
    output.mkdir(parents=True)
    commands = []

    def command(args):
        result = subprocess.run(list(map(str, args)), capture_output=True, text=True)
        commands.append(dict(args=list(map(str, args)), returncode=result.returncode,
                             stdout=result.stdout, stderr=result.stderr))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        if result.returncode:
            raise ValueError(result.stderr or 'semantic check failed')
        return result.stdout

    clang = command(['xcrun', '--find', 'clang']).strip()
    objects = []
    for op in ('min', 'max'):
        text = (HERE / (op + '.ll')).read_text()
        for intrinsic, mock in (('active.channel.mask', 'mask'), ('simd.shuffle', 'shuffle'),
                                ('minf', 'min'), ('maxf', 'max')):
            text = text.replace('llvm.igil.' + intrinsic, 'mock_' + mock)
        # The stand-ins observe mutable test state.
        text = text.replace(' nounwind readnone', ' nounwind')
        source = output / (op + '.ll')
        source.write_text(text)
        obj = output / (op + '.o')
        command([clang, '-O2', '-Wno-override-module', '-c', source, '-o', obj])
        objects.append(obj)
    command([clang, '-O2', '-Wall', '-Wextra', '-Werror', HERE / 'semantic-test.c',
             *objects, '-o', output / 'test'])
    result = json.loads(command([output / 'test']))
    result.update(classification='FACT', native_acceptance=False,
                  source_sha256={name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                                 for name in ('min.ll', 'max.ll', 'semantic-test.c', 'check-semantics.py')})
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
