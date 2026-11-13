#!/usr/bin/env python3
"""make.py: build the Metal27 adapter's laptop test bundle, and the host replay runtime it is tested against.

  make.py bundle OUT --cache DIR [--template FILE] [--tar]
      OUT/metal27-adapter-test/   the bundle (copy it to the laptop as the tarball, never with scp -r)
      OUT/metal27-adapter-test.tgz (+ .sha256) with --tar
      OUT/work/                   intermediates (the unsigned Metal cut, cache-dylib's report and check, AIR)
  make.py host-runtime OUT --from RT [--laptop-paths]
      a host replay runtime for the adapter, derived from a cache/shaders replay runtime RT (see README)
  make.py inputs --cache DIR [--template FILE]
      only check the inputs against inputs.json

Inputs (all read-only, each checked against inputs.json before use; nothing Apple's is in this repository):
  --cache DIR       the 26B131 x86_64 dyld cache folder (the Rosetta cryptex of the 26B131 IPSW, mounted)
  CACHE_DYLIB       hao-tools' tools/cache-dylib (a hao-tools checkout next to this repository unless set)
  --template FILE   optional: the captured MetalOld type-7 request r2 shipped; without it the bundle gets the
                    template built from the framing in inputs.json (same requests on the wire, see README)
Toolchain: Xcode with the macOS 27 SDK (clang, metal, metallib, codesign, dwarfdump). Every x86_64 binary built here
is signed ad hoc. Nothing is run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SRC = HERE / 'src'
INPUTS = json.loads((HERE / 'inputs.json').read_text())
REPO = HERE.parents[1]
CACHE_DYLIB = Path(os.environ.get('CACHE_DYLIB', REPO.parent / 'hao-tools' / 'tools' / 'cache-dylib'))
METAL = '/System/Library/Frameworks/Metal.framework/Versions/A'
MTLCOMPILER = '/System/Library/PrivateFrameworks/MTLCompiler.framework/Versions'
BUNDLE = 'metal27-adapter-test'
CC = ['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=27.0', '-O1', '-Wall']
SIGNED = ['bin/metal-adapter-test', 'bin/q2log.dylib', 'fw/Metal.framework/Versions/A/libHaoMetalShim.dylib',
          'fw/MTLCompiler.framework/Versions/32023/MTLCompiler', 'fw/MTLCompiler.framework/Versions/32024/MTLCompiler']
# files run.sh checks before it runs anything (MANIFEST.sha256), in this order
MANIFEST = ['bin/metal-adapter-test', 'bin/q2log.dylib', 'fw/Metal.framework/Versions/A/libHaoMetalShim.dylib',
            'fw/Metal.framework/Versions/A/Metal', 'fw/MTLCompiler.framework/Versions/32023/MTLCompiler',
            'fw/MTLCompiler.framework/Versions/32024/MTLCompiler', 'lib/downgrade-template.req7',
            'lib/kernels-11.metallib', 'lib/kernels-27.metallib', 'run.sh']


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(8 << 20), b''):
            h.update(b)
    return h.hexdigest()


def run(*cmd, **kw):
    print('+', ' '.join(str(c) for c in cmd), file=sys.stderr)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def check_inputs(cache, template=None):
    """Refuse unless every input is the file inputs.json names. Returns the cache-dylib directory."""
    want = INPUTS['x86_64_cache']
    for name, digest in want['files'].items():
        p = Path(cache) / name
        if not p.is_file():
            raise SystemExit(f'input missing: {p} ({want["what"]})')
        got = sha(p)
        if got != digest:
            raise SystemExit(f'input {p}: sha256 {got}, expected {digest}')
    cd = INPUTS['cache_dylib']
    for name, digest in cd['files'].items():
        p = CACHE_DYLIB / name
        if not p.is_file():
            raise SystemExit(f'cache-dylib not found at {CACHE_DYLIB}: check out hao-tools next to this repository '
                             'or set CACHE_DYLIB')
        if sha(p) != digest:
            raise SystemExit(f'{p}: not hao-tools {cd["commit"]} ({cd["revision"]}); sha256 {sha(p)}, expected {digest}')
    if template:
        got = sha(template)
        if got != INPUTS['downgrade_template']['captured_sha256']:
            raise SystemExit(f'template {template}: sha256 {got}, expected the captured r2 template')
    return CACHE_DYLIB


def template_bytes():
    """The type-7 (AIR downgrade) request template from its framing in inputs.json: MetalOld's 13.2.1 header with
    name "add1" and an 8-byte placeholder module. The stand-in replaces name and module (resplice), so the requests it
    sends are byte-identical to the ones made from the captured template (tests/test_standin.py)."""
    t = INPUTS['downgrade_template']
    return bytes.fromhex(t['framing_hex']) + bytes(t['placeholder_module_size'])


def build_bundle(out, cache, template=None, tar=False):
    out = Path(out).resolve()
    if out.exists():
        raise SystemExit(f'{out} exists; give a new folder')
    cd = check_inputs(cache, template)
    b, w = out / BUNDLE, out / 'work'
    md = b / 'fw/Metal.framework/Versions/A'
    for d in (w, b / 'bin', b / 'lib', b / 'src', md, *(b / f'fw/MTLCompiler.framework/Versions/{v}' for v in (32023, 32024))):
        d.mkdir(parents=True)

    # 1. 27.2.1's own x86_64 Metal, cut from the cache under its own install name; + LC_REEXPORT_DYLIB of the shim
    run(sys.executable, '-I', cd / 'cache_dylib.py', 'extract', cache, 'x86_64', f'{METAL}/Metal', w / 'Metal27.cut',
        '--report', w / 'Metal27.report.json')
    if sha(w / 'Metal27.cut') != INPUTS['metal27']['cut_sha256']:
        raise SystemExit(f'Metal cut {sha(w / "Metal27.cut")} differs from the pinned cut; cache-dylib changed?')
    run(sys.executable, '-I', SRC / 'add_reexport.py', w / 'Metal27.cut', md / 'Metal', '@loader_path/libHaoMetalShim.dylib')
    run('codesign', '-f', '-s', '-', md / 'Metal')
    uuid = run('xcrun', 'dwarfdump', '--uuid', md / 'Metal', capture_output=True, text=True).stdout.split()[1]
    print('Metal27 uuid', uuid, file=sys.stderr)
    with open(w / 'Metal27.check.json', 'w') as f:
        run(sys.executable, '-I', cd / 'check.py', md / 'Metal', w / 'Metal27.report.json', stdout=f)
    errors = json.loads((w / 'Metal27.check.json').read_text())['errors']
    if errors:
        raise SystemExit(f'check.py: {len(errors)} errors in the Metal cut')

    # 2. shim (re-exported by Metal27; does not link Metal)
    run(*CC, '-dynamiclib', '-install_name', '@loader_path/libHaoMetalShim.dylib', '-compatibility_version', '1.0',
        '-current_version', '1.0', f'-DMETAL27_UUID="{uuid}"', f'-I{SRC}', SRC / 'shim.c', '-o',
        md / 'libHaoMetalShim.dylib', '-lobjc')
    # 3. MTLCompiler stand-in, at the two paths 27's Metal dlopens
    for v in (32023, 32024):
        run(*CC, '-dynamiclib', '-install_name', f'{MTLCOMPILER}/{v}/MTLCompiler', SRC / 'standin.c', '-o',
            b / f'fw/MTLCompiler.framework/Versions/{v}/MTLCompiler')
    # 4. test program (links the system Metal path; DYLD_FRAMEWORK_PATH decides which Metal) and the os_log echo
    run(*CC, '-fobjc-arc', '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'IOKit', SRC / 'test.m',
        '-o', b / 'bin/metal-adapter-test')
    run(*CC, '-dynamiclib', '-install_name', '@loader_path/q2log.dylib', SRC / 'q2log.c', '-o', b / 'bin/q2log.dylib')
    # 5. kernels: 27.2 default target (what 27's own metallibs look like) and macOS 11 target (older AIR)
    run('xcrun', 'metal', '-c', SRC / 'kernels.metal', '-o', w / 'kernels-27.air')
    run('xcrun', 'metallib', w / 'kernels-27.air', '-o', b / 'lib/kernels-27.metallib')
    run('xcrun', 'metal', '-mmacos-version-min=11.0', '-c', SRC / 'kernels.metal', '-o', w / 'kernels-11.air')
    run('xcrun', 'metallib', w / 'kernels-11.air', '-o', b / 'lib/kernels-11.metallib')
    if template:
        shutil.copyfile(template, b / 'lib/downgrade-template.req7')
    else:
        (b / 'lib/downgrade-template.req7').write_bytes(template_bytes())
    # 6. ad hoc sign every x86_64 binary (unsigned x86_64 code hangs in exec on Apple silicon)
    for f in SIGNED:
        run('codesign', '-f', '-s', '-', b / f)
    # 7. framework layout around Metal27, made after signing (with Info.plist reachable, codesign would sign it as a
    #    bundle and seal the host's Resources). Resources and XPCServices point at the running system's own
    #    Metal.framework (resolved on the laptop at run time); without them 27's Metal finds no MTLCompilerService.
    for l in ('Resources', 'XPCServices'):
        (md / l).symlink_to(f'{METAL}/{l}')
    (b / 'fw/Metal.framework/Versions/Current').symlink_to('A')
    for l in ('Metal', 'Resources', 'XPCServices'):
        (b / f'fw/Metal.framework/{l}').symlink_to(f'Versions/Current/{l}')
    # 8. runner, README, sources, manifest
    shutil.copyfile(HERE / 'run.sh', b / 'run.sh')
    os.chmod(b / 'run.sh', 0o755)
    shutil.copyfile(HERE / 'README.md', b / 'README.md')
    for p in sorted(SRC.iterdir()):
        if p.is_file():
            shutil.copyfile(p, b / 'src' / p.name)
    for p in ('make.py', 'inputs.json'):
        shutil.copyfile(HERE / p, b / 'src' / p)
    (b / 'MANIFEST.sha256').write_text(''.join(f'{sha(b / f)}  {f}\n' for f in MANIFEST))
    print('built', b, file=sys.stderr)
    if tar:
        t = out / f'{BUNDLE}.tgz'
        run('tar', '--no-mac-metadata', '--no-xattrs', '--uid', '501', '--gid', '20', '--uname', 'hao', '--gname',
            'staff', '-czf', t, '-C', out, BUNDLE, env={**os.environ, 'COPYFILE_DISABLE': '1'})
        (out / f'{BUNDLE}.tgz.sha256').write_text(f'{sha(t)}  {t.name}\n')
        print(sha(t), t, file=sys.stderr)
    return b


def macho(p):
    if p.is_symlink() or not p.is_file():
        return False
    with open(p, 'rb') as f:
        return f.read(4) in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe')


HELPER_31001 = 'System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM_helper.dylib'


def host_runtime(out, rt, laptop_paths=False):
    """A host replay runtime for the adapter from a cache/shaders replay runtime RT (cache/shaders/lib/prepare.py):
    RT's OCLP compilers, HD 4000 driver, plugin chain and IGIL runtime, with every link to RT's own Metal (OCLP's
    wrapper + MetalOld) pointed back at /System/.../Metal, so that the process's Metal (the bundle's Metal27 through
    DYLD_FRAMEWORK_PATH) serves them as on the laptop. --laptop-paths also restores OCLP 31001 libLLVM's relative
    libLLVM_helper load path (laptop run 1). RT/bin (replay, ip.dylib) is copied; RT/fw/Metal.framework is not."""
    out, rt = Path(out).resolve(), Path(rt).resolve()
    if out.exists():
        raise SystemExit(f'{out} exists; give a new folder')
    for top in ('fw/GPUCompiler.framework', 'fw/MTLCompiler.framework', 'dl', 'bundles', 'root', 'bin'):
        shutil.copytree(rt / top, out / top, symlinks=True)
    log = []
    for p in sorted(x for x in out.rglob('*') if macho(x)):
        deps = subprocess.run(['otool', '-L', p], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
        changes = []
        for line in deps:
            dep = line.strip().split(' (')[0]
            if '/Metal.framework/' in dep and not dep.startswith('/System/') or dep.endswith('MetalAdapter.dylib'):
                changes.append((dep, f'{METAL}/Metal'))
            if laptop_paths and dep == '@loader_path/libLLVM_helper.dylib' and '31001' in str(p):
                changes.append((dep, HELPER_31001))
        for old, new in changes:
            subprocess.run(['install_name_tool', '-change', old, new, p], check=True, capture_output=True)
            log.append(f'{p.relative_to(out)}: {old} -> {new}')
        if changes or p.parent.name == 'bin':
            subprocess.run(['codesign', '-f', '-s', '-', p], check=True, capture_output=True)
    (out / 'relink.log').write_text('\n'.join(log) + '\n')
    print(f'host runtime {out}: {len(log)} links changed', file=sys.stderr)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('bundle')
    b.add_argument('out')
    b.add_argument('--cache', required=True)
    b.add_argument('--template')
    b.add_argument('--tar', action='store_true')
    h = sub.add_parser('host-runtime')
    h.add_argument('out')
    h.add_argument('--from', dest='rt', required=True)
    h.add_argument('--laptop-paths', action='store_true')
    i = sub.add_parser('inputs')
    i.add_argument('--cache', required=True)
    i.add_argument('--template')
    a = ap.parse_args(argv)
    if a.cmd == 'bundle':
        build_bundle(a.out, a.cache, a.template, a.tar)
    elif a.cmd == 'host-runtime':
        host_runtime(a.out, a.rt, a.laptop_paths)
    else:
        print('inputs OK; cache-dylib', check_inputs(a.cache, a.template))


if __name__ == '__main__':
    main()
