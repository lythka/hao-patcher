"""Build and launch the x86_64 replay harness (src/replay.m + src/interpose.c) against a prepared runtime (prepare.py).

The harness is started directly (never through arch(1) or a shell wrapper that could drop DYLD_* variables).
"""
import os, subprocess, json, hashlib, platform
from pathlib import Path

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
CFLAGS = ['-arch', 'x86_64', '-mmacosx-version-min=13.0', '-O1']


def build(rt, profile=None):
    """compile replay and ip.dylib into RT/bin; returns their paths"""
    if profile:
        config = Path(rt)/'runtime.json'
        actual = json.loads(config.read_text())
        actual['replay'] = json.loads(Path(profile).read_text())['replay']
        config.write_text(json.dumps(actual, sort_keys=True)+'\n')
    b = os.path.join(rt, 'bin'); os.makedirs(b, exist_ok=True)
    rp, ip = os.path.join(b, 'replay'), os.path.join(b, 'ip.dylib')
    subprocess.run(['clang'] + CFLAGS + ['-framework', 'Foundation', '-lobjc', os.path.join(SRC, 'replay.m'), '-o', rp],
                   check=True, capture_output=True)
    subprocess.run(['clang'] + CFLAGS + ['-dynamiclib', os.path.join(SRC, 'interpose.c'), os.path.join(SRC, 'IOAccelerator.tbd'),
                    '-framework', 'IOKit', '-install_name', ip, '-o', ip], check=True, capture_output=True)
    for p in (rp, ip): subprocess.run(['codesign', '-f', '-s', '-', p], check=True, capture_output=True)
    return rp, ip


_hashes = {}
def file_hash(path):
    st = path.stat()
    signature = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    key = str(path.resolve())
    if key not in _hashes or _hashes[key][0] != signature:
        value = hashlib.sha256()
        with path.open('rb') as source:
            for data in iter(lambda: source.read(1 << 20), b''): value.update(data)
        _hashes[key] = signature, value.hexdigest()
    return _hashes[key][1]


def identity(rt, dsc, environment):
    """Hash actual compiler/dependency bytes, declared target/ABI and effective options.

    Stat-cached file digests only avoid repeated reads inside this host process;
    a changed file invalidates its digest. No build-produced identity is trusted.
    """
    files = {}
    roots = [('runtime',Path(rt)), ('recipe',Path(SRC))]
    if dsc: roots.append(('dyld-cache',Path(dsc)))
    for label, root in roots:
        for path in sorted(root.rglob('*')):
            name = label+'/'+str(path.relative_to(root))
            if path.is_symlink(): files[name+'@link'] = os.readlink(path)
            if path.is_file(): files[name] = file_hash(path)
    excluded = {'RP_OUT','RP_CACHE_DIR','RP_MEMO','RP_NODUMP','RP_LOG','RP_LOGKEYS',
                'RP_SLEEP','RP_DRY','MTL_DEBUG_LOG_CACHE_KEYS'}
    options = {k:v for k,v in environment.items() if k.startswith(('RP_','MTL_','DYLD_','COMPAT_')) and k not in excluded}
    value = dict(schema=2,files=files,options=options,abi='x86_64-apple-macos',
                 host=platform.uname()._asdict(),harness=file_hash(Path(__file__)))
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def env(rt, dsc, cache, out='', keys=True, dry=None, memo=None, extra=None):
    """environment for one harness process writing its MetalOld FS cache to CACHE/com.apple.metal"""
    e = {k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'MTL_', 'RP_', 'COMPAT_'))}
    config = json.loads((Path(rt)/'runtime.json').read_text())
    target = config['replay']
    e.update({
        'DYLD_FRAMEWORK_PATH': os.path.join(rt, 'fw'),
        'DYLD_LIBRARY_PATH': os.path.join(rt, 'dl'),
        'DYLD_INSERT_LIBRARIES': os.path.join(rt, 'bin', 'ip.dylib'),
        'MTL_MONOLITHIC_COMPILER': '1',
        'RP_ROOT': os.path.join(rt, 'root'),
        'RP_MTLCOMPILER': os.path.join(rt, config['compiler']),
        'RP_CACHE_DIR': cache.rstrip('/') + '/',
        'RP_OUT': out, 'RP_SLEEP': '3',
        **{'RP_'+key.upper():str(target[key]) for key in
           ('step0','step1','pci_id','registry_id','vram','shared_memory','max_resource','device_class','abi')},
    })
    if dsc: e['DYLD_SHARED_CACHE_DIR'] = dsc     # None: the host's own x86_64 dyld cache (Rosetta)
    if not out: e['RP_NODUMP'] = '1'
    if keys: e['MTL_DEBUG_LOG_CACHE_KEYS'] = '1'
    if dry: e['RP_DRY'] = dry
    if memo: e['RP_MEMO'] = memo
    if extra: e.update(extra)
    if config.get('translation'):
        if config['translation']['mode'] != 'block-only':
            raise ValueError('general cache regeneration cannot assert a logical32 dispatch contract')
        if 'COMPAT_LOGICAL32' in e:
            raise ValueError('logical32 needs a separate reviewed specialization replay')
        e['DYLD_INSERT_LIBRARIES'] += ':' + str(Path(rt)/'translator/compat.dylib')
        e.update(COMPAT_BLOCK_READS='per-axis-mip0', COMPAT_CLIENT_ABI='axis-st-v1')
    if memo: e['RP_RUNTIME_ID'] = identity(rt,dsc,e)
    return e


def cmd(rt):
    target = json.loads((Path(rt)/'runtime.json').read_text())['replay']
    return [os.path.join(rt, 'bin', 'replay'), os.path.join(rt, target['metal']), os.path.join(rt, target['driver'])]
