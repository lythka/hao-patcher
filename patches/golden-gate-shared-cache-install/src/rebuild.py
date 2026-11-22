"""Build on target Data; only the separate install phase may change System or Preboot."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'build/cache'))
import cachebuild as cache
from console import Stopped


def audit_sources(inventory, system, os_root):
    roots = {'system': Path(system).resolve(), 'os': Path(os_root).resolve()}
    seen = set()
    for row in inventory['files']:
        view, logical = row['view'], row['path']
        if view not in roots or not logical.startswith('/') or '..' in Path(logical).parts:
            raise Stopped('invalid cache source inventory', code=4)
        key = (view, logical)
        if key in seen:
            raise Stopped('duplicate cache source inventory entry', code=4)
        seen.add(key)
        path = (roots[view]/logical.lstrip('/')).resolve(strict=True)
        if not path.is_relative_to(roots[view]) or not path.is_file():
            raise Stopped('cache source escapes target view: ' + logical, code=4)
        if path.stat().st_size != row['size'] or cache.sha256(path) != row['sha256']:
            raise Stopped('cache source differs from the verified build: ' + logical, code=4)
    if not seen:
        raise Stopped('empty cache source inventory', code=4)
    return len(seen)


def write_record(path, record):
    temporary = path.with_suffix('.new')
    with temporary.open('w') as stream:
        json.dump(record, stream, sort_keys=True, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def discard(workspace, names):
    for name in names:
        path = workspace/name
        if path.is_symlink():
            raise Stopped('unexpected link in cache workspace: '+name, code=5)
        if path.is_dir(): shutil.rmtree(path)
        elif path.exists(): path.unlink()


def cleanup(workspace):
    discard(workspace, ('producer', 'build', 'scratch', 'catalog.json'))


def prepare(spec, inventory, assets, roots, workspace, binding, console, *, run=None, euid=None):
    """Read-only target inputs, private scratch, verified reuse; no fold installation."""
    system, os_root, app, data = roots
    console.item('Checking the target files against the verified rebuild input inventory')
    audit_sources(inventory, system, os_root)
    cache.preflight(spec, system, os_root, workspace, offline=True, euid=euid, free=spec['space_bytes'])
    key = hashlib.sha256(json.dumps(dict(spec=spec, inventory=inventory, binding=binding,
                                        assets={role:digest for role, (_, digest) in assets.items()}),
                                     sort_keys=True).encode()).hexdigest()
    record = workspace/'prepared.json'
    family = workspace/'family'
    if record.is_file():
        prior = json.loads(record.read_text())
        if prior.get('key') != key:
            raise Stopped('saved cache belongs to another install or configuration', code=4)
        cache.verify_family(family, spec)
        if set(prior.get('metadata', {})) != {'build.json', 'provider-provenance.json', 'retired-roots.txt'}:
            raise Stopped('saved cache metadata inventory differs', code=4)
        for name, digest in prior['metadata'].items():
            if cache.sha256(family/name) != digest:
                raise Stopped('saved cache metadata changed: '+name, code=4)
        cleanup(workspace)
        console.item('Reusing the verified shared cache')
        return family
    for role, (path, digest) in assets.items():
        if path.is_symlink() or cache.sha256(path) != digest:
            raise Stopped('cache rebuild asset hash differs: '+role, code=4)
    # Only incomplete products of this private workspace are discarded. Preserve the previous failure log.
    if (workspace/'build.log').exists():
        os.replace(workspace/'build.log', workspace/'previous-build.log')
    cleanup(workspace)
    discard(workspace, ('family', 'result.json'))
    cache.preflight(spec, system, os_root, workspace, offline=True, euid=euid)
    scratch = workspace/'scratch'; scratch.mkdir(mode=0o700)
    previous = os.environ.get('TMPDIR'); os.environ['TMPDIR'] = str(scratch)
    try:
        family = cache.build(spec, assets['builder'][0], assets['catalog'][0], system, os_root, app, data,
                             workspace, console, run=run)
    finally:
        if previous is None: os.environ.pop('TMPDIR', None)
        else: os.environ['TMPDIR'] = previous
    cache.verify_family(family, spec)
    metadata = {name: cache.sha256(family/name)
                for name in ('build.json', 'provider-provenance.json', 'retired-roots.txt')}
    # The marker must not outlive buffered family bytes after power loss.
    for name in (*spec['files'], *metadata):
        with (family/name).open('rb') as stream: os.fsync(stream.fileno())
    fd = os.open(family, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    write_record(record, dict(key=key, binding=binding, metadata=metadata))
    # Free intermediate families before cryptex replacement needs space in this same APFS container.
    cleanup(workspace)
    return family


def installed_manifest(family, spec):
    cache.verify_family(family, spec)
    flags = {0x80000:'restricted', 0x20000:'schg', 0x100000:'sunlnk', 2:'uchg', 0x8000:'hidden', 1:'nodump'}
    files = {}
    for name, digest in spec['files'].items():
        st = (family/name).stat()
        files['System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/'+name] = dict(
            sha256=digest, mode='%04o' % stat.S_IMODE(st.st_mode), owner='%d:%d' % (st.st_uid, st.st_gid),
            flags=sorted(label for bit,label in flags.items() if getattr(st,'st_flags',0)&bit))
    return dict(format=1, recipe='golden-gate-shared-cache-install', pack='base', files=files, dirs={})


class Console:
    def item(self, text):
        print('Shared cache: '+text, flush=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[1] not in ('build', 'install'):
        raise Stopped('expected SYSTEM and build or install phase', code=2)
    if os.geteuid() != 0:
        raise Stopped('the cache install hook runs as root in the installer', code=5)
    system, phase = Path(argv[0]).resolve(), argv[1]
    config = HERE/'build/cache'
    spec = cache.configuration({'cache_rebuild': json.loads((config/'config.json').read_text())})
    inventory = json.loads((config/'inputs.json').read_text())
    if inventory.get('format') != 1 or inventory.get('build') != spec['build']:
        raise Stopped('cache source inventory belongs to another build', code=4)
    assets = {}
    for role, row in spec['rows'].items():
        lines = (config/(row+'.pin')).read_text().splitlines()
        digest = next(line.split()[0] for line in lines if line.strip() and not line.startswith('#'))
        assets[role] = (HERE/'inputs'/row, digest)
    sys.path.insert(0, str(HERE/'build/fold'))
    import fold
    from cryptex import image_mount
    from install import volume_mount
    with tempfile.TemporaryDirectory(prefix='cache-views-') as temporary, ExitStack() as stack:
        mount = Path(temporary)
        preboot = stack.enter_context(volume_mount(os.environ['PBU'], mount/'preboot'))
        data = stack.enter_context(volume_mount(os.environ['DATU'], mount/'data'))
        group = os.environ['GRP']
        if not group or '/' in group or group in ('.', '..'):
            raise Stopped('invalid target volume group', code=5)
        current = preboot/group/'cryptex1/current'
        workspace = data/'private/var/db/hao/cache-install'/group
        unsafe = cache.safety.others_can_change(workspace, env={}, euid=0)
        if unsafe:
            raise Stopped('cache workspace is not private: '+str(unsafe), code=5)
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        with ExitStack() as images:
            os_root = images.enter_context(image_mount(current/'os.dmg', mount/'os'))
            app = images.enter_context(image_mount(current/'app.dmg', mount/'app'))
            if phase == 'install' and not (workspace/'prepared.json').is_file():
                raise Stopped('cache install has no verified build checkpoint', code=5)
            family = prepare(spec, inventory, assets, (system, os_root, app, data), workspace,
                             dict(group=group, system=os.environ['SYSU'], data=os.environ['DATU']), Console())
        if phase == 'install':
            # Source cache plus a replacement cryptex and its compact copy coexist during fold.
            need = inventory['install_space_bytes']
            if type(need) is not int or need <= 0:
                raise Stopped('invalid cache installation space budget', code=4)
            if shutil.disk_usage(workspace).free < need:
                raise Stopped('cache verified, but cryptex installation needs 40 GB free on the target', code=3)
            fold.install(system, family)
            with image_mount(current/'os.dmg', mount/'installed-os') as installed:
                manifest = installed_manifest(installed/'System/Library/dyld', spec)
            manifests = data/'private/var/db/hao/manifests'
            manifests.mkdir(parents=True, exist_ok=True, mode=0o755)
            write_record(manifests/'shared-cache-family.json', manifest)
            shutil.rmtree(workspace)


if __name__ == '__main__':
    try:
        main()
    except (Stopped, OSError, ValueError) as error:
        print('Stopped: '+str(error), file=sys.stderr)
        print('The target OS is partially assembled. A failed build has not replaced its cache or sealed System. '
              'Resume the cache-build stage; a failed cache-install stage requires a fresh install.', file=sys.stderr)
        sys.exit(error.code if isinstance(error, Stopped) else 1)
