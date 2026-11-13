#!/usr/bin/env python3
"""make.py: build the Metal27 adapter's laptop test bundle, and the host replay runtime it is tested against.

  make.py bundle OUT --cache DIR --metalold FILE [--template FILE] [--tar]
      OUT/metal27-adapter-test/   the bundle (copy it to the laptop as the tarball, never with scp -r)
      OUT/metal27-adapter-test.tgz (+ .sha256) with --tar
      OUT/work/                   intermediates (the unsigned Metal cut, cache-dylib's report and check, AIR)
  make.py host-runtime OUT --from RT [--laptop-paths]
      a host replay runtime for the adapter, derived from a cache/shaders replay runtime RT (see README)
  make.py installed OUT --cache DIR --metalold FILE
      OUT/root/System/...         the files an installed trial puts on the System volume (patches/golden-gate-metal27):
                                  Metal27 (the bundle's, byte for byte), the installed shim beside it, the installed
                                  stand-in at MTLCompiler.framework/Versions/{32023,32024}/MTLCompiler
      OUT/build.json              inputs, toolchain, every file's sha256 and Metal27's UUID
      OUT/work/                   intermediates, as for bundle
  make.py inputs --cache DIR [--metalold FILE] [--template FILE]
      only check the inputs against inputs.json

Inputs (all read-only, each checked against inputs.json before use; nothing Apple's is in this repository):
  --cache DIR       the 26B131 x86_64 dyld cache folder (the Rosetta cryptex of the 26B131 IPSW, mounted)
  --metalold FILE   OCLP's MetalOld.dylib (13.2.1-25): abi.py reads its descriptor struct encodings and 27's from
                    the Metal cut and writes OUT/work/layouts.inc, the shim's translation table (generated here,
                    never committed: it carries Apple's private type encodings)
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
            'lib/kernels-11.metallib', 'lib/kernels-27.metallib', 'lib/render-11.metallib', 'lib/render-27.metallib',
            'run.sh']


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(8 << 20), b''):
            h.update(b)
    return h.hexdigest()


def run(*cmd, **kw):
    print('+', ' '.join(str(c) for c in cmd), file=sys.stderr)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def check_inputs(cache, template=None, metalold=None):
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
    if metalold:
        got = sha(metalold)
        if got != INPUTS['metalold']['sha256']:
            raise SystemExit(f'MetalOld {metalold}: sha256 {got}, expected {INPUTS["metalold"]["sha256"]}')
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


def cut_metal(cd, cache, w, dest):
    """27.2.1's own x86_64 Metal, cut from the cache under its own install name, + LC_REEXPORT_DYLIB of the shim
    (beside it, @loader_path), signed ad hoc, at DEST. Refuses a cut other than the pinned one or any check.py error.
    Returns its UUID."""
    run(sys.executable, '-I', cd / 'cache_dylib.py', 'extract', cache, 'x86_64', f'{METAL}/Metal', w / 'Metal27.cut',
        '--report', w / 'Metal27.report.json')
    if sha(w / 'Metal27.cut') != INPUTS['metal27']['cut_sha256']:
        raise SystemExit(f'Metal cut {sha(w / "Metal27.cut")} differs from the pinned cut; cache-dylib changed?')
    run(sys.executable, '-I', SRC / 'add_reexport.py', w / 'Metal27.cut', dest, '@loader_path/libHaoMetalShim.dylib')
    run('codesign', '-f', '-s', '-', dest)
    uuid = run('xcrun', 'dwarfdump', '--uuid', dest, capture_output=True, text=True).stdout.split()[1]
    print('Metal27 uuid', uuid, file=sys.stderr)
    with open(w / 'Metal27.check.json', 'w') as f:
        run(sys.executable, '-I', cd / 'check.py', dest, w / 'Metal27.report.json', stdout=f)
    errors = json.loads((w / 'Metal27.check.json').read_text())['errors']
    if errors:
        raise SystemExit(f'check.py: {len(errors)} errors in the Metal cut')
    return uuid


def build_shim(metalold, w, uuid, dest, installed=False):
    """The shim (re-exported by Metal27; does not link Metal), with the descriptor layout table generated from
    MetalOld and the Metal cut (abi.py). INSTALLED: the build every process on the system loads (shim.c)."""
    if not (w / 'layouts.inc').exists():
        run(sys.executable, '-I', HERE / 'abi.py', 'layouts', metalold, w / 'Metal27.cut', '--out', w / 'layouts.inc')
    run(*CC, '-dynamiclib', '-install_name', '@loader_path/libHaoMetalShim.dylib', '-compatibility_version', '1.0',
        '-current_version', '1.0', f'-DMETAL27_UUID="{uuid}"', *(['-DHAO_METAL27_INSTALLED'] if installed else []),
        f'-I{w}', f'-I{SRC}', SRC / 'shim.c', '-o', dest, '-lobjc', '-framework', 'CoreFoundation')


def template_inc(path):
    """The C array the installed stand-in carries as its default type-7 template (template_bytes)."""
    t = template_bytes()
    rows = [', '.join(f'0x{x:02x}' for x in t[i:i + 16]) for i in range(0, len(t), 16)]
    Path(path).write_text('// generated by make.py from inputs.json downgrade_template (framing + placeholder)\n'
                          'static const unsigned char kTemplate[] = {\n  ' + ',\n  '.join(rows) + '\n};\n')


def build_standins(dest_of, w=None, installed=False):
    """The MTLCompiler stand-in at the two paths 27's Metal dlopens (DEST_OF(version) -> file). INSTALLED: silent
    unless HAO_STANDIN_LOG=1, with the template built in (template.inc in W)."""
    flags = []
    if installed:
        template_inc(w / 'template.inc')
        flags = ['-DHAO_STANDIN_INSTALLED', f'-DHAO_TEMPLATE_INC="{w / "template.inc"}"']
    for v in (32023, 32024):
        run(*CC, '-dynamiclib', '-install_name', f'{MTLCOMPILER}/{v}/MTLCompiler', *flags, SRC / 'standin.c', '-o',
            dest_of(v))


def build_bundle(out, cache, metalold, template=None, tar=False):
    out = Path(out).resolve()
    if out.exists():
        raise SystemExit(f'{out} exists; give a new folder')
    cd = check_inputs(cache, template, metalold)
    b, w = out / BUNDLE, out / 'work'
    md = b / 'fw/Metal.framework/Versions/A'
    for d in (w, b / 'bin', b / 'lib', b / 'src', md, *(b / f'fw/MTLCompiler.framework/Versions/{v}' for v in (32023, 32024))):
        d.mkdir(parents=True)

    # 1. 27.2.1's own x86_64 Metal + LC_REEXPORT_DYLIB of the shim
    uuid = cut_metal(cd, cache, w, md / 'Metal')
    # 2. shim
    build_shim(metalold, w, uuid, md / 'libHaoMetalShim.dylib')
    # 3. MTLCompiler stand-in, at the two paths 27's Metal dlopens
    build_standins(lambda v: b / f'fw/MTLCompiler.framework/Versions/{v}/MTLCompiler')
    # 4. test program (links the system Metal path; DYLD_FRAMEWORK_PATH decides which Metal) and the os_log echo
    run(*CC, '-fobjc-arc', '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'IOKit', SRC / 'test.m',
        '-o', b / 'bin/metal-adapter-test')
    run(*CC, '-dynamiclib', '-install_name', '@loader_path/q2log.dylib', SRC / 'q2log.c', '-o', b / 'bin/q2log.dylib')
    #    host only, never in the bundle: the same program for the r38 replay runtime's MetalOld (host-control.sh)
    (w / 'host').mkdir()
    run(*CC, '-fobjc-arc', '-DHOST_R38_EXPORTS', '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'IOKit',
        SRC / 'test.m', '-o', w / 'host/metal-adapter-test-r38')
    run('codesign', '-f', '-s', '-', w / 'host/metal-adapter-test-r38')
    # 5. kernels and render shaders: 27.2 default target (what 27's own metallibs look like) and macOS 11 target
    for src in ('kernels', 'render'):
        run('xcrun', 'metal', '-c', SRC / f'{src}.metal', '-o', w / f'{src}-27.air')
        run('xcrun', 'metallib', w / f'{src}-27.air', '-o', b / f'lib/{src}-27.metallib')
        run('xcrun', 'metal', '-mmacos-version-min=11.0', '-c', SRC / f'{src}.metal', '-o', w / f'{src}-11.air')
        run('xcrun', 'metallib', w / f'{src}-11.air', '-o', b / f'lib/{src}-11.metallib')
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
        if p.is_file() and p.name != 'layouts.inc':
            shutil.copyfile(p, b / 'src' / p.name)
    shutil.copyfile(w / 'layouts.inc', b / 'src' / 'layouts.inc')   # the bundle never leaves this machine and the laptop
    for p in ('make.py', 'inputs.json', 'abi.py'):
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


# The installed trial (patches/golden-gate-metal27): System paths of the files it adds or replaces, in install order
# (the shim before the Metal that re-exports it, the stand-ins before the Metal that opens them).
INSTALLED = ['System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/32023/MTLCompiler',
             'System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/32024/MTLCompiler',
             'System/Library/Frameworks/Metal.framework/Versions/A/libHaoMetalShim.dylib',
             'System/Library/Frameworks/Metal.framework/Versions/A/Metal']


def git_head():
    r = subprocess.run(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    d = subprocess.run(['git', '-C', str(REPO), 'status', '--porcelain', '--', str(HERE)], capture_output=True, text=True)
    return r.stdout.strip() + ('-dirty' if d.stdout.strip() else '') if r.returncode == 0 else None


def build_installed(out, cache, metalold):
    """OUT/root: the System files of the installed trial; OUT/build.json. Metal27 is the bundle's file byte for byte
    (same cut, same re-export, ad hoc signature of the same bytes); the shim and the stand-ins are their installed
    builds (shim.c, standin.c)."""
    out = Path(out).resolve()
    if out.exists():
        raise SystemExit(f'{out} exists; give a new folder')
    cd = check_inputs(cache, None, metalold)
    w, root = out / 'work', out / 'root'
    w.mkdir(parents=True)
    for p in INSTALLED:
        (root / p).parent.mkdir(parents=True, exist_ok=True)
    metal, shim = root / INSTALLED[3], root / INSTALLED[2]
    uuid = cut_metal(cd, cache, w, metal)
    build_shim(metalold, w, uuid, shim, installed=True)
    build_standins(lambda v: root / INSTALLED[0 if v == 32023 else 1], w, installed=True)
    for p in INSTALLED[:3]:
        run('codesign', '-f', '-s', '-', root / p)
    sdk = run('xcrun', '--show-sdk-version', capture_output=True, text=True).stdout.strip()
    clang = run('xcrun', 'clang', '--version', capture_output=True, text=True).stdout.splitlines()[0]
    record = {
        'what': 'metal-adapter installed trial: 27.2.1 (26B131) Metal on the HD 4000 for every process '
                '(patches/golden-gate-metal27)',
        'tool': 'tools/metal-adapter/make.py installed', 'commit': git_head(),
        'inputs': {'x86_64_cache_uuid': INPUTS['x86_64_cache']['uuid'],
                   'metal27_cut_sha256': INPUTS['metal27']['cut_sha256'],
                   'metalold_sha256': INPUTS['metalold']['sha256'],
                   'cache_dylib': {k: INPUTS['cache_dylib'][k] for k in ('revision', 'commit')}},
        'toolchain': {'sdk': sdk, 'clang': clang},
        'metal27_uuid': uuid,
        'files': {p: sha(root / p) for p in INSTALLED},
    }
    (out / 'build.json').write_text(json.dumps(record, indent=1) + '\n')
    print('installed trial files in', root, file=sys.stderr)
    return record


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
    b.add_argument('--metalold', required=True)
    b.add_argument('--template')
    b.add_argument('--tar', action='store_true')
    h = sub.add_parser('host-runtime')
    h.add_argument('out')
    h.add_argument('--from', dest='rt', required=True)
    h.add_argument('--laptop-paths', action='store_true')
    n = sub.add_parser('installed')
    n.add_argument('out')
    n.add_argument('--cache', required=True)
    n.add_argument('--metalold', required=True)
    i = sub.add_parser('inputs')
    i.add_argument('--cache', required=True)
    i.add_argument('--metalold')
    i.add_argument('--template')
    a = ap.parse_args(argv)
    if a.cmd == 'bundle':
        build_bundle(a.out, a.cache, a.metalold, a.template, a.tar)
    elif a.cmd == 'installed':
        build_installed(a.out, a.cache, a.metalold)
    elif a.cmd == 'host-runtime':
        host_runtime(a.out, a.rt, a.laptop_paths)
    else:
        print('inputs OK; cache-dylib', check_inputs(a.cache, a.template, a.metalold))


if __name__ == '__main__':
    main()
