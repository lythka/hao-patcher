#!/usr/bin/env python3
"""Build six additive runtime helpers; preserve the frozen four-helper tool."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'shader-runtime-helpers'
spec = importlib.util.spec_from_file_location('split_runtime', BASE / 'build.py')
split = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split)
RUNTIME_SHA = 'e524e4433b63d4e4dcd43c0ec98f36b808ae3c862db9fbbc6f8d5ebf18086d33'


def build(args):
    split.require(split.sha(args.input) == RUNTIME_SHA, 'unexpected original runtime')
    split.require(split.sha(args.llvm) == args.llvm_sha256, 'legacy LLVM hash drift')
    output = args.output.resolve()
    split.require(not output.exists(),
                  'requires fresh output')
    original = split.decode(args.input.read_bytes())
    split.require(len(original) == 4357, 'unexpected original symbol count')
    output.mkdir(parents=True)
    commands = []

    def run(command):
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True)
        commands.append(dict(args=list(map(str, command)), returncode=result.returncode,
                             stdout=result.stdout, stderr=result.stderr))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        split.require(result.returncode == 0, result.stderr)

    clang = Path(subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip())
    assembler = output / 'assemble'
    run([clang, '-arch', 'x86_64', '-Wall', '-Wextra', '-Werror', '-O2',
         BASE / 'assemble.c', '-o', assembler])
    run(['/usr/bin/codesign', '--force', '--sign', '-', assembler])
    inputs = [(BASE / (name + '.ll'), symbol) for name, symbol in split.HELPERS.items()]
    inputs += [(HERE / (op + '.ll'), 'air_simd_' + op + '_f32') for op in ('min', 'max')]
    additions = {}
    for source, symbol in inputs:
        bitcode = output / (source.stem + '.bc')
        run([assembler, args.llvm, source, bitcode])
        additions[symbol] = bitcode.read_bytes()
    runtime = output / 'runtime.igil64.split'
    runtime.write_bytes(split.extend(original, additions))
    report = dict(classification='FACT', native_acceptance=False,
                  input=str(args.input), input_sha256=RUNTIME_SHA,
                  llvm=str(args.llvm), llvm_sha256=args.llvm_sha256,
                  clang=dict(path=str(clang), sha256=split.sha(clang)),
                  output=str(runtime), output_sha256=split.sha(runtime),
                  preserved_symbols=len(original), added_symbols=sorted(additions),
                  original_blocks_equal=True,
                  source_sha256={str(p): split.sha(p) for p in
                                 [Path(__file__), BASE / 'build.py', BASE / 'assemble.c'] +
                                 [p for p, _ in inputs]})
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--llvm', type=Path, required=True)
    parser.add_argument('--llvm-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args())
