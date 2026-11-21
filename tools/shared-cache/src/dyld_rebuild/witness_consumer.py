"""Build and assess an isolated native witness-consumer experiment.

The two-node standard-library fixture tests the runtime entry path and relocation,
not arbitrary witness serialization or an installed image patch.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from .swift_metadata import ROOT, provenance, write_json

GUM_VERSION = '17.25.0'
GUM_SHA256 = '9b5aa3fc08149a1bc972a25a81f87f170f7205e2f805498475ee012d7825c8f5'
MODES = ('native', 'export', 'implementation')


def gum_devkit(devkit, output):
    # The external devkit is a pinned source dependency, not a build output.
    if hashlib.sha256(devkit.read_bytes()).hexdigest() != GUM_SHA256:
        raise ValueError('expected Frida Gum 17.25.0 macos-x86_64 devkit')
    gum = output / 'gum'
    gum.mkdir()
    with tarfile.open(devkit) as archive:
        for name in ('frida-gum.h', 'libfrida-gum.a', 'frida-gum-example.c'):
            (gum / name).write_bytes(archive.extractfile('./' + name).read())
    return gum


def native_flags(toolchain, devkit, output, llvm):
    gum = gum_devkit(devkit, output)
    manifest = json.loads((toolchain / 'build.json').read_text())
    source = toolchain / ('swift-' + manifest['swift_commit'])
    flags = ['xcrun', 'clang++', '-arch', 'x86_64', '-std=c++17', '-O2',
             '-mmacosx-version-min=14.0', '-Wl,-reproducible', '-g0',
             '-ffile-prefix-map=' + str(output) + '=/build', '-Wall', '-Wextra', '-Werror',
             '-DLLVM_DISABLE_ABI_BREAKING_CHECKS_ENFORCING=1']
    for include in (source / 'include', source / 'stdlib/public/SwiftShims',
                    toolchain / 'build/include', llvm / 'include', gum):
        flags += ['-isystem', str(include.resolve())]
    return flags


def prepare(toolchain, devkit, output, llvm):
    from .artifacts import helper
    pinned = helper('witness-consumer')
    if pinned is None:
        return produce(toolchain, devkit, output, llvm)
    import shutil
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(pinned, output / 'witness-consumer')
    shutil.copy2(ROOT / 'tools/swift-witness-consumer-run.sh', output / 'run.sh')
    write_json(output / 'build.json', provenance([pinned], operation='witness-consumer-fixture'))
    return output


def produce(toolchain, devkit, output, llvm):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    flags = native_flags(toolchain, devkit, output, llvm)
    gum = output / 'gum'
    values = ROOT / 'tests/swift-witness-values.swift'
    cpp = ROOT / 'tools/swift-witness-consumer.cpp'
    header = ROOT / 'tools/cache-address.h'
    hook = ROOT / 'tools/witness-runtime.h'
    object_file = output / 'values.o'
    binary = output / 'witness-consumer'
    commands = [
        ['xcrun', 'swiftc', '-target', 'x86_64-apple-macos14.0', '-parse-as-library',
         '-O', '-module-name', 'HaoWitnessFixture', '-emit-object', str(values), '-o', str(object_file)],
    ]
    swiftc = Path(subprocess.check_output(['xcrun', '--find', 'swiftc'], text=True).strip())
    flags += [str(cpp), str(object_file), '-L', str(gum), '-lfrida-gum', '-lresolv',
              '-L', str(swiftc.parent.parent / 'lib/swift/macosx'), '-o', str(binary)]
    commands += [flags, ['codesign', '-s', '-', str(binary)]]
    with (output / 'compile.log').open('w') as log:
        for command in commands:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    runner = ROOT / 'tools/swift-witness-consumer-run.sh'
    (output / 'run.sh').write_bytes(runner.read_bytes())
    (output / 'run.sh').chmod(0o755)
    inputs = [devkit, toolchain / 'build.json', values, cpp, header, hook, runner, Path(__file__)]
    write_json(output / 'build.json', provenance(inputs, operation='witness-consumer-fixture',
               architecture='x86_64', frida_gum=GUM_VERSION, commands=commands,
               llvm=subprocess.check_output([str(llvm / 'bin/llvm-config'), '--version'], text=True).strip(),
               swift_compiler=subprocess.check_output(['xcrun', 'swiftc', '--version'], text=True),
               cxx_compiler=subprocess.check_output(['xcrun', 'clang++', '--version'], text=True)))
    return output


def summarize(directory):
    runs, errors = {}, []
    for mode in ('produce', *MODES):
        path = directory / (mode + '.jsonl')
        try:
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            runs[mode] = rows[-1] if rows else {}
        except (OSError, ValueError) as error:
            errors.append(f'{mode}: {error}')
            runs[mode] = {}
    baseline, exported, impl = (runs[m] for m in MODES)
    completed = (runs['produce'].get('produced') is True and
                 all(runs[m].get('complete') is True and runs[m].get('mode') == m and
                     runs[m].get('direct_calls') == 4 and runs[m].get('conformance_calls') == 4 and
                     runs[m].get('dependencies') == 1 and runs[m].get('comparisons') == 100 and
                     runs[m].get('hashes') == 25 for m in MODES))
    uuids = {runs[m].get('cache_uuid') for m in MODES}
    completed = bool(completed and len(uuids) == 1 and None not in uuids and not errors)
    supported = bool(completed and baseline.get('map_hits') == 0 and
                     baseline.get('conformance_adopted') == 4 and
                     impl.get('conformance_adopted') == 4 and impl.get('map_hits', 0) >= 6 and
                     impl.get('fallbacks', 0) >= 1)
    return dict(complete=completed, consumer_supported=supported, runs=runs, errors=errors,
                export_reaches_conformance=bool(completed and exported.get('conformance_adopted') == 4),
                scope='Own-process two-node fixture. No installed patch, dyld witness map, '
                      'arbitrary witness closure or app cache adoption is proven.')
