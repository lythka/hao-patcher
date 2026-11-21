"""Developer-only producers for fixed target probes; consumers use pinned rows."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    'witness-probe': ('swift-witness-probe.c', []),
    'witness-v4': ('swift-witness-probe.c', ['-dynamiclib', '-DTABLE_LIBRARY', '-DMINOR=4']),
    'witness-v5': ('swift-witness-probe.c', ['-dynamiclib', '-DTABLE_LIBRARY', '-DMINOR=5']),
    'objc-probe': ('objc-runtime-probe.m', ['-fobjc-arc', '-framework', 'Foundation']),
    'bind-runtime-probes': ('bind-runtime-probes.c', []),
    'swift-conformance-probe': ('swift-conformance-probe.c', []),
    'swift-capture': ('swift-capture.c', []),
    'swift-graph': ('swift-graph.c', []),
    'swift-reflection-reader': ('swift-reflection-reader.c',
                                ['-I' + str(ROOT / 'vendor/swift-remote-mirror'), '-lswiftRemoteMirror']),
}


def compile_probe(name, destination):
    source, extra = SOURCES[name]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if '-dynamiclib' in extra:
        extra = [*extra, '-Wl,-install_name,@rpath/' + name]
    if name == 'swift-reflection-reader':
        sdk = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
        extra = [*extra, '-L' + sdk + '/usr/lib/swift']
    arches = ['-arch', 'arm64', '-arch', 'x86_64'] if name == 'swift-reflection-reader' else ['-arch', 'x86_64']
    subprocess.run(['xcrun', 'clang', *arches, '-mmacosx-version-min=15.0',
                    '-O2', '-g0', '-Wall', '-Wextra', '-Werror', '-Wl,-reproducible',
                    '-ffile-prefix-map=' + str(ROOT) + '=/source', *extra,
                    str(ROOT / 'tools' / source), '-o', str(destination)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none',
                    '--identifier', 'org.hao.' + name, str(destination)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(destination)], check=True)
    return destination


def probe(name, destination):
    from .artifacts import helper
    result = helper(name, destination)
    return result if result is not None else compile_probe(name, destination)
