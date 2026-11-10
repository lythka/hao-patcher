"""Standalone display package recipes; Apple inputs are supplied by the caller."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

from macho import add_load_dylib, section_ranges


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]  # repository root: patches/, cache/, tools/
# The display sources whose hashes the build record lists.
SOURCES = ('tools/display-fixes', 'tools/display-state-shim', 'patches/golden-gate-windowserver-filter')
WINDOWSERVER = 'System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer'
LIBRARIES = ('usr/lib/dss_shim.dylib', 'usr/lib/windowserver_filter.dylib')


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True, stderr=subprocess.PIPE).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    return dict(path=str(path.resolve()), sha256=sha(path))


def record(operation, inputs, output):
    value = dict(package='tools/display-fixes', operation=operation,
                 commit=run('git', '-C', ROOT, 'rev-parse', 'HEAD'),
                 dirty=bool(run('git', '-C', ROOT, 'status', '--porcelain')),
                 date=datetime.now(timezone.utc).isoformat(), inputs=inputs,
                 sources={name: sha(REPO / name) for name in run('git', '-C', REPO, 'ls-files',
                          '--cached', '--others', '--exclude-standard', '-z', '--', *SOURCES).split('\0') if name and (REPO / name).is_file()},
                 outputs={str(path.relative_to(output)): sha(path) for path in output.rglob('*')
                          if path.is_file() and path.name != 'build.json'})
    (output / 'build.json').write_text(json.dumps(value, indent=2) + '\n')


def build(output):
    producer = load('dss_producer', REPO / 'tools/display-state-shim/display-state-shim')
    producer.build(output)
    with tempfile.TemporaryDirectory(prefix='display-filter-') as temporary:
        make = load('filter_producer', REPO / 'patches/golden-gate-windowserver-filter/make')
        directory = make.main(Path(temporary))
        shutil.copy2(directory / 'windowserver_filter.dylib', output / 'windowserver_filter.dylib')
        (output / 'filter-build.json').write_bytes((directory / 'build.json').read_bytes())
    (output / 'dss-build.json').write_bytes((output / 'build.json').read_bytes())
    record('build', {}, output)
    return output


def replace_info_plist(kept, donor):
    """Preserve the recorded native-launcher recipe: adopt the donor's embedded plist."""
    original = donor.read_bytes()
    found = {original[offset:offset + size]
             for offset, size in section_ranges(original, b'__TEXT', b'__info_plist').values()}
    if len(found) != 1:
        raise ValueError('expected one embedded donor Info.plist')
    plist = re.sub(rb'>\s+<', b'><', plistlib.dumps(plistlib.loads(found.pop()), sort_keys=False))
    data = bytearray(kept.read_bytes())
    sections = section_ranges(data, b'__TEXT', b'__info_plist')
    if not sections:
        raise ValueError('native launcher has no embedded Info.plist')
    for offset, size in sections.values():
        if len(plist) > size:
            raise ValueError('donor Info.plist does not fit native launcher')
        data[offset:offset + size] = plist + b'\n' * (size - len(plist))
    kept.write_bytes(data)


def prepare(artifact, windowserver, donor, output):
    """Produce the exact three signed replacement files; never mount or alter inputs."""
    output = output.resolve()
    output.mkdir(mode=0o755, parents=True)
    workspace = output / 'work'
    workspace.mkdir(mode=0o755)
    try:
        staged_ws = output / 'payload' / WINDOWSERVER
        staged_ws.parent.mkdir(parents=True, mode=0o755)
        shutil.copyfile(windowserver, staged_ws)
        staged_ws.chmod(0o755)
        replace_info_plist(staged_ws, donor)
        data = bytearray(staged_ws.read_bytes())
        add_load_dylib(data, b'/usr/lib/dss_shim.dylib', 0x1000007)
        staged_ws.write_bytes(data)
        entitlements = workspace / 'WindowServer.plist'
        entitlements.write_text(run('codesign', '-d', '--entitlements', '-', '--xml', donor))
        plistlib.loads(entitlements.read_bytes())
        details = subprocess.run(['codesign', '-dv', str(donor)], check=True, capture_output=True, text=True).stderr
        identifier = next(line.split('=', 1)[1] for line in details.splitlines() if line.startswith('Identifier='))
        run('codesign', '-f', '-s', '-', '-i', identifier, '--entitlements', entitlements, staged_ws)
        libraries = output / 'payload/usr/lib'
        libraries.mkdir(parents=True, mode=0o755)
        dss = libraries / 'dss_shim.dylib'
        loader = load('filter_load_command', REPO / 'patches/golden-gate-windowserver-filter/src/load_shim.py')
        data = bytearray((artifact / dss.name).read_bytes())
        loader.add(data)
        dss.write_bytes(data)
        dss.chmod(0o755)
        run('codesign', '-f', '-s', '-', '--preserve-metadata=identifier,entitlements,flags,runtime', dss)
        # Preserve signing identity from the existing signed producer while editing in scratch.
        # The staged source must retain the embedded signature before re-signing.
        shutil.copy2(artifact / 'windowserver_filter.dylib', libraries / 'windowserver_filter.dylib')
        for path in (staged_ws, dss, libraries / 'windowserver_filter.dylib'):
            run('codesign', '--verify', '--strict', path)
    finally:
        shutil.rmtree(workspace)
    inputs = dict(windowserver=identity(windowserver), golden_windowserver=identity(donor),
                  dss_shim=identity(artifact / 'dss_shim.dylib'),
                  windowserver_filter=identity(artifact / 'windowserver_filter.dylib'))
    record('prepare', inputs, output)
    return output


def install(prepared, system):
    system = system.resolve()
    if system == Path('/'):
        raise ValueError('select an offline System mount, not the running root')
    if os.geteuid() != 0:
        raise PermissionError('offline System installation requires root ownership')
    for relative in (WINDOWSERVER, *LIBRARIES):
        source, target = prepared / 'payload' / relative, system / relative
        run('codesign', '--verify', '--strict', source)
        if not target.parent.resolve().is_relative_to(system):
            raise ValueError('target path escapes offline System: ' + str(target))
        flags = target.stat().st_flags if target.exists() else 0
        if relative in LIBRARIES:
            flags |= stat.SF_RESTRICTED
        if target.exists():
            run('chflags', 'noschg,nouchg,norestricted', target)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copyfile(source, target)
        os.chown(target, 0, 0)
        target.chmod(0o755)
        os.chflags(target, flags)
        print(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command')
    command = commands.add_parser('build', help='compile both signed Intel libraries')
    command.add_argument('--out', type=Path, required=True)
    command = commands.add_parser('prepare', help='prepare signed payload from supplied Apple launcher originals')
    command.add_argument('--artifact', type=Path, required=True)
    command.add_argument('--windowserver', type=Path, required=True, help='kept native Intel launcher')
    command.add_argument('--golden-windowserver', type=Path, required=True, help='original 26A428 launcher for metadata/entitlements')
    command.add_argument('--out', type=Path, required=True)
    command = commands.add_parser('install', help='copy prepared payload to an already-mounted offline System')
    command.add_argument('--prepared', type=Path, required=True)
    command.add_argument('--system', type=Path, required=True)
    commands.add_parser('test', help='run all package tests without parent source or inputs')
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == 'test':
        suites = unittest.TestSuite(unittest.TestLoader().discover(str(path)) for path in
                                   (ROOT / 'tests', REPO / 'tools/display-state-shim/tests',
                                    REPO / 'patches/golden-gate-windowserver-filter/tests'))
        return int(not unittest.TextTestRunner(verbosity=2).run(suites).wasSuccessful())
    try:
        if args.command == 'build':
            build(args.out)
        elif args.command == 'prepare':
            prepare(args.artifact, args.windowserver, args.golden_windowserver, args.out)
        else:
            install(args.prepared, args.system)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'display-fixes: {error}\n')
    return 0
