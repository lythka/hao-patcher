#!/usr/bin/env python3
"""Add new helpers to a pinned split runtime in a fresh local work directory."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HELPERS = {'rotate': 'air_simd_shuffle_rotate_down_f32',
           'group-size': 'air_get_simdgroup_size_i16',
           'insert-bits': 'air_insert_bits_u_i64',
           'extract-bits': 'air_extract_bits_u_i64'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decode(data):
    require(len(data) >= 4, 'missing split header')
    count, = struct.unpack_from('<I', data)
    require(0 < count <= len(data) // 6, 'invalid symbol count')
    position = 4
    entries, ranges = {}, []
    for _ in range(count):
        end = data.index(0, position)
        name = data[position:end].decode('ascii')
        require(name and name not in entries, 'empty or duplicate symbol')
        offset, = struct.unpack_from('<I', data, end + 1)
        position = end + 5
        size, = struct.unpack_from('<I', data, offset)
        require(size >= 24 and offset + 4 + size <= len(data), 'invalid block range')
        block = data[offset + 4:offset + 4 + size]
        magic, version, start, length, cpu = struct.unpack_from('<5I', block)
        require(magic == 0x0b17c0de and version == 0 and start == 20 and
                start + length <= len(block) and block[start:start + 4] == b'BC\xc0\xde',
                'invalid bitcode wrapper')
        entries[name] = block
        ranges.append((offset, offset + 4 + size))
    ranges.sort()
    require(ranges[0][0] == position and ranges[-1][1] == len(data) and
            all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])), 'blocks must tile payload')
    return entries


def encode(entries):
    names = sorted(entries)
    offset = 4 + sum(len(n.encode('ascii')) + 5 for n in names)
    header = bytearray(struct.pack('<I', len(names)))
    payload = bytearray()
    for name in names:
        require('\0' not in name, 'invalid symbol name')
        header += name.encode('ascii') + b'\0' + struct.pack('<I', offset + len(payload))
        block = entries[name]
        payload += struct.pack('<I', len(block)) + block
    output = bytes(header + payload)
    require(decode(output) == entries, 'encoded runtime differs')
    return output


def extend(original, additions):
    require(not original.keys() & additions.keys(), 'refuse replacing an existing symbol')
    result = dict(original, **additions)
    output = encode(result)
    checked = decode(output)
    require(all(checked[n] == b for n, b in original.items()), 'original runtime block changed')
    return output


def build(args):
    require(sha(args.input) == args.input_sha256, 'runtime input hash drift')
    require(sha(args.llvm) == args.llvm_sha256, 'legacy LLVM hash drift')
    output = args.output.resolve()
    require(not output.exists(),
            'output must be fresh')
    original = decode(args.input.read_bytes())
    output.mkdir(parents=True)
    commands = []

    def run(command):
        result = subprocess.run(command, capture_output=True, text=True)
        commands.append(dict(args=command, returncode=result.returncode,
                             stdout=result.stdout, stderr=result.stderr))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        require(result.returncode == 0, result.stderr)

    clang = Path(subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip())
    run([str(clang), '-arch', 'x86_64', '-Wall', '-Wextra', '-Werror', '-O2',
         str(HERE / 'assemble.c'), '-o', str(output / 'assemble')])
    additions = {}
    for filename, symbol in HELPERS.items():
        bc = output / (filename + '.bc')
        run([str(output / 'assemble'), str(args.llvm), str(HERE / (filename + '.ll')), str(bc)])
        additions[symbol] = bc.read_bytes()
    result = output / 'runtime.igil64.split'
    result.write_bytes(extend(original, additions))
    report = dict(classification='FACT', native_acceptance=False,
                  input=str(args.input), input_sha256=args.input_sha256,
                  llvm=str(args.llvm), llvm_sha256=args.llvm_sha256,
                  clang=dict(path=str(clang), sha256=sha(clang)),
                  source_sha256={name: sha(HERE / name) for name in
                                 ('build.py', 'assemble.c', *(n + '.ll' for n in HELPERS))},
                  output=str(result), output_sha256=sha(result),
                  preserved_symbols=len(original), added_symbols=sorted(additions),
                  original_blocks_equal=True, commands=commands)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('output', 'output_sha256', 'preserved_symbols', 'added_symbols')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--input-sha256', required=True)
    parser.add_argument('--llvm', type=Path, required=True)
    parser.add_argument('--llvm-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args())
