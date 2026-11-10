"""Assemble the Rosetta replay runtime from a patched (OCLP root-patched) System root.

Layout (RT = runtime folder; the harness and ip.dylib address it as fw/ dl/ bundles/ root/):
  RT/fw/Metal.framework/Versions/A/{Metal, MetalOld.dylib}           OCLP Metal shim + MetalOld (one byte patch, below)
  RT/fw/{GPUCompiler,MTLCompiler}.framework/Versions/{3802,31001}/...  compiler stacks MetalOld loads
  RT/bundles/AppleIntelHD4000GraphicsMTLDriver.bundle                 the HD 4000 Metal driver (MTLIGAccelDevice)
  RT/dl/{libMTLIntelCompilerPlugin,libIGIL-Metal,iCBE}.dylib          compiler plugin chain (ip.dylib maps
                                                                      /System/Library/Extensions/*.bundle/Contents/MacOS/<leaf> here)
  RT/root/System/Library/Extensions/AppleIntelGraphicsShared.bundle/Contents/Resources/runtime*   IGIL runtime bitcode
Every Mach-O is a copy: load commands that point at the System's GPUCompiler/MTLCompiler/Metal frameworks are rewritten
to @loader_path paths inside RT/fw (so the 3802 and 31001 libLLVM never collide), MetalOld gets the in-process-compile
patch, and everything is re-signed ad hoc. Nothing on the System root is modified.
"""
import hashlib, os, shutil, subprocess, sys



def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def is_macho(p):
    if os.path.islink(p) or not os.path.isfile(p): return False
    with open(p, 'rb') as f: m = f.read(4)
    return m in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe')


def copy(src, dst):
    """content-only copy (System files carry restricted flags that must not be copied); symlinks kept"""
    if not src.endswith('/'):
        os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(src, dst); os.chmod(dst, 0o755 if is_macho(src) else 0o644)
        return
    for r, ds, fs in os.walk(src):
        rel = os.path.relpath(r, src); od = os.path.join(dst, rel) if rel != '.' else dst.rstrip('/')
        os.makedirs(od, exist_ok=True)
        for n in ds + fs:
            s, d = os.path.join(r, n), os.path.join(od, n)
            if os.path.islink(s):
                if not os.path.lexists(d): os.symlink(os.readlink(s), d)
            elif os.path.isfile(s):
                shutil.copyfile(s, d); os.chmod(d, 0o755 if is_macho(s) else 0o644)


def relink(rt, log):
    fw = os.path.join(rt, 'fw')
    bins = [os.path.join(r, f) for top in ('fw', 'dl', 'bundles') for r, ds, fs in os.walk(os.path.join(rt, top)) for f in fs]
    bins = [b for b in bins if is_macho(b)]
    for p in bins:
        out = subprocess.run(['otool', '-L', p], capture_output=True, text=True).stdout.split('\n')[1:]
        for l in out:
            dep = l.strip().split(' (')[0]
            tgt = None
            # the OCLP 31001 libLLVM wrapper names its helper without the leading slash ("System/Library/...")
            norm = '/' + dep if dep.startswith('System/Library/') else dep
            for pre, sub in (('/System/Library/PrivateFrameworks/GPUCompiler.framework/', 'GPUCompiler.framework/'),
                             ('/System/Library/PrivateFrameworks/MTLCompiler.framework/', 'MTLCompiler.framework/'),
                             ('/System/Library/Frameworks/Metal.framework/', 'Metal.framework/')):
                if norm.startswith(pre): tgt = os.path.join(fw, sub + norm[len(pre):])
            if tgt and os.path.exists(tgt) and os.path.realpath(tgt) != os.path.realpath(p):
                rel = '@loader_path/' + os.path.relpath(tgt, os.path.dirname(p))
                r = subprocess.run(['install_name_tool', '-change', dep, rel, p], capture_output=True, text=True)
                log.write('relink %s: %s -> %s %s\n' % (os.path.relpath(p, rt), dep, rel, r.stderr.strip()))
    return bins


def prepare(sysroot, rt, profile, log=sys.stderr):
    """build RT from sysroot (a System root, or a list: System root then System-rooted overlays, later files win, as
    when the overlays are installed); returns a manifest {RT-relative path: sha256 of the file in RT}"""
    import json
    roots = [sysroot] if isinstance(sysroot, str) else list(sysroot)
    config = json.loads(open(profile).read())
    os.makedirs(rt)
    with open(os.path.join(rt, "runtime.json"), "w") as stream:
        json.dump(config, stream)
    man = {}
    wanted = list(config['files']) + [(os.path.join(config['resources'], f), os.path.join('root', config['resources'], f))
                                      for f in config['resource_files']]
    for s, d in wanted:
        have = [r for r in roots if os.path.exists(os.path.join(r, s).rstrip('/'))]
        if not have: raise FileNotFoundError('prepare: %s missing in %s' % (s, roots))
        for r in have:
            copy(os.path.join(r, s), os.path.join(rt, d))
    for r, ds, fs in os.walk(rt):
        for f in fs:
            p = os.path.join(r, f)
            if not os.path.islink(p): man[os.path.relpath(p, rt)] = sha(p)
    mo = os.path.join(rt, 'fw/Metal.framework/Versions/A/MetalOld.dylib')
    patch = config['metalold_patch']
    off, old, new = patch['offset'], bytes.fromhex(patch['before']), bytes.fromhex(patch['after'])
    with open(mo, 'r+b') as f:
        f.seek(off); cur = f.read(len(old))
        if cur != old:
            raise RuntimeError('prepare: MetalOld %s has %s at %#x, expected %s (new OCLP Metal stack: find the '
                               '_MTLIsInternalBuild call in -[MTLCompiler ...monolithic...] again)' % (man['fw/Metal.framework/Versions/A/MetalOld.dylib'][:12], cur.hex(), off, old.hex()))
        f.seek(off); f.write(new)
    log.write('MetalOld patched at %#x: %s -> %s\n' % (off, old.hex(), new.hex()))
    bins = relink(rt, log)
    for p in bins:
        r = subprocess.run(['codesign', '-f', '-s', '-', p], capture_output=True, text=True)
        if r.returncode: raise RuntimeError('codesign %s: %s' % (p, r.stderr))
    return man


if __name__ == '__main__':
    import json
    # prepare.py SYSTEM_ROOT [OVERLAY...] RUNTIME PROFILE
    m = prepare(sys.argv[1:-2], sys.argv[-2], sys.argv[-1])
    json.dump(m, open(os.path.join(sys.argv[-2], 'sources.json'), 'w'), indent=0, sort_keys=True)
    print('runtime', sys.argv[-2], len(m), 'files')
