"""Build and verify a release's shared cache from the installed Mac's own files."""
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import runpy
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from console import Stopped
import safety

REPO = Path(__file__).resolve().parents[3]
BASE = 'dyld_shared_cache_x86_64'
VERSION = 'System/Library/CoreServices/SystemVersion.plist'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(path):
    try:
        with Path(path).open('rb') as stream:
            header = stream.read(104)
    except OSError as exc:
        raise Stopped('cannot read the dyld cache: ' + str(path), cause=str(exc), code=4) from exc
    if len(header) != 104 or not header.startswith(b'dyld_v1'):
        raise Stopped('not a dyld cache: ' + str(path), code=4)
    return str(uuid.UUID(bytes=header[88:104]))


def configuration(release):
    spec = release.get('cache_rebuild') if isinstance(release, dict) else None
    if not isinstance(spec, dict) or spec.get('format') != 1:
        raise Stopped('this release has no user cache rebuild', code=5)
    required = ('build', 'source_cache_uuid', 'cache_uuid', 'files', 'rows', 'features', 'space_bytes')
    if any(k not in spec for k in required):
        raise Stopped('incomplete cache rebuild release record', code=4)
    if not isinstance(spec['build'], str) or not re.fullmatch('[A-Za-z0-9]+', spec['build']):
        raise Stopped('invalid System build in release', code=4)
    for key in ('source_cache_uuid', 'cache_uuid'):
        try:
            uuid.UUID(spec[key])
        except (ValueError, TypeError, AttributeError):
            raise Stopped('invalid cache UUID in release', code=4) from None
    if not isinstance(spec['files'], dict) or BASE not in spec['files']:
        raise Stopped('missing cache family hashes in release', code=4)
    for name, digest in spec['files'].items():
        if not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r'dyld_shared_cache_x86_64(?:\.[A-Za-z0-9_-]+)*', name) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise Stopped('invalid cache family hash entry', code=4)
    if not isinstance(spec['rows'], dict) or set(spec['rows']) != {'builder', 'catalog'} or not all(isinstance(v, str) for v in spec['rows'].values()):
        raise Stopped('invalid cache rebuild asset rows', code=4)
    if len(set(spec['rows'].values())) != 2:
        raise Stopped('cache builder and catalog must use separate rows', code=4)
    if type(spec['space_bytes']) is not int or spec['space_bytes'] < 38_000_000_000:
        raise Stopped('invalid cache rebuild space budget', code=4)
    if not isinstance(spec['features'], str) or not re.fullmatch('[A-Za-z0-9_-]+', spec['features']):
        raise Stopped('invalid cache feature name', code=4)
    return spec


def preflight(spec, system, os_root, work_parent, *, machine=None, euid=None, free=None, offline=False):
    machine = platform.machine() if machine is None else machine
    euid = os.geteuid() if euid is None else euid
    architectures = ('x86_64', 'arm64') if offline else ('x86_64',)
    if machine not in architectures or euid != 0:
        raise Stopped('cache build runs as root on the installed Intel Mac', code=5)
    try:
        build = plistlib.loads((system / VERSION).read_bytes())['ProductBuildVersion']
    except (OSError, ValueError, KeyError):
        raise Stopped('cannot read the installed System build', code=5) from None
    if build != spec['build']:
        raise Stopped('this cache rebuild needs macOS build ' + spec['build'] + ', found ' + build, code=5)
    if identity(os_root / 'System/Library/dyld' / BASE) != str(uuid.UUID(spec['source_cache_uuid'])):
        raise Stopped('the installed source cache UUID differs from this release', code=4)
    parent = work_parent
    while not parent.exists():
        parent = parent.parent
    available = shutil.disk_usage(parent).free if free is None else free
    if available < spec['space_bytes']:
        raise Stopped('not enough space to rebuild the cache', cause='%d GB free; %d GB required' %
                      (available // 10**9, spec['space_bytes'] // 10**9), code=3)


def extract(archive, destination):
    """Only regular files/directories from our pinned producer archive; no links or devices."""
    destination.mkdir(mode=0o700)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        names = set()
        for m in members:
            parts = Path(m.name).parts
            if (not parts or m.name.startswith('/') or '..' in parts or m.name in names
                    or not (m.isfile() or m.isdir())):
                raise Stopped('unsafe cache builder archive entry', code=4)
            names.add(m.name)
        tar.extractall(destination, members=members, filter='data')


def verify_family(family, spec):
    found = {p.name for p in family.glob(BASE + '*')}
    if found != set(spec['files']):
        raise Stopped('rebuilt cache file list differs from the release', code=4)
    if identity(family / BASE) != str(uuid.UUID(spec['cache_uuid'])):
        raise Stopped('rebuilt cache UUID differs from the release', code=4)
    for name, expected in sorted(spec['files'].items()):
        path = family / name
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise Stopped('rebuilt cache SHA256 differs: ' + name, code=4)
    return {name: digest for name, digest in sorted(spec['files'].items())}


@contextmanager
def awake():
    process = subprocess.Popen(['/usr/bin/caffeinate', '-dims', '-w', str(os.getpid())],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield
    finally:
        process.terminate()
        process.wait()


def run_logged(command, *, env, stdout, stderr, console):
    process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
    try:
        while True:
            try:
                code = process.wait(timeout=30)
                return subprocess.CompletedProcess(command, code)
            except subprocess.TimeoutExpired:
                with Path(stdout.name).open('rb') as log:
                    log.seek(max(0, log.seek(0, 2) - 2048))
                    lines = log.read().decode(errors='replace').splitlines()
                console.item('Still rebuilding' + (': ' + safety.text(lines[-1]) if lines else ''))
    except BaseException:
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise


def build(spec, builder, catalog, system, os_root, app, data, workspace, console,
          *, program_files=None, run=None):
    """Explicit views for the host proof; the user command supplies only the live Mac's roots."""
    source = workspace / 'producer'
    extract(builder, source)
    catalog_path = workspace / 'catalog.json'
    with gzip.open(catalog, 'rb') as src, catalog_path.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    value = json.loads(catalog_path.read_text())
    if str(uuid.UUID(value['source_cache_uuid'])) != str(uuid.UUID(spec['source_cache_uuid'])):
        raise Stopped('Swift catalog belongs to another source cache', code=4)
    del value
    family = workspace / 'family'
    command = [sys.executable, '-I', '-B', str(source / 'tools/shared-cache/make'),
               '--system', str(system), '--os', str(os_root), '--app', str(app), '--data', str(data),
               '--features', spec['features'], '--swift-catalog', str(catalog_path),
               '--work', str(workspace / 'build'), '--output', str(family)]
    if program_files is not None:
        command += ['--program-files', str(program_files)]
    # A user environment cannot select alternate compiler tools or bypass pinned helpers.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SHARED_CACHE_', 'CACHE_TOOLCHAIN'))}
    env['DEVELOPER_DIR'] = str(workspace / 'no-developer-tools')
    env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
    started = time.monotonic()
    console.item('Rebuilding the shared cache; details: ' + str(workspace / 'build.log'))
    with (workspace / 'build.log').open('w') as log:
        if run is None:
            result = run_logged(command, env=env, stdout=log, stderr=subprocess.STDOUT, console=console)
        else:
            result = run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise Stopped('shared-cache rebuild failed', cause='see ' + str(workspace / 'build.log'), code=1)
    console.item('Checking the cache UUID and every file against the release')
    hashes = verify_family(family, spec)
    record = dict(state='verified', build=spec['build'], cache_uuid=spec['cache_uuid'], files=hashes,
                  seconds=round(time.monotonic() - started, 3), installed=False)
    (workspace / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    return family


def command(args, console, release_path):
    release = json.loads(Path(release_path).read_text())
    spec = configuration(release)
    system = Path('/')
    os_root = Path('/System/Volumes/Preboot/Cryptexes/OS')
    app = Path('/System/Volumes/Preboot/Cryptexes/App')
    data = Path('/System/Volumes/Data')
    work_parent = Path(args.work)
    preflight(spec, system, os_root, work_parent)
    acquire = runpy.run_path(str(REPO / 'tools/acquire/acquire'))
    rows = [r for r in acquire['load'](release_path, assets_from=args.assets_from)
            if r.get('row') in spec['rows'].values()]
    if len(rows) != len(spec['rows']) or {r['row'] for r in rows} != set(spec['rows'].values()):
        raise Stopped('release is missing cache rebuild assets', code=4)
    console.item('Build ' + spec['build'] + '; expected cache ' + spec['cache_uuid'])
    console.item('%d MB of rebuild inputs; at least %d GB free' %
                 (sum(r['size'] for r in rows) // 10**6, spec['space_bytes'] // 10**9))
    if args.dry_run:
        console.item('Dry run: download pinned inputs, read this System, rebuild, verify every cache file')
        return 0
    unsafe = safety.others_can_change(work_parent, env={}, euid=0)
    if unsafe:
        raise Stopped('cache workspace must be owned by root and not writable by others: ' + str(unsafe), code=5)
    work_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = Path(tempfile.mkdtemp(prefix='rebuild-', dir=work_parent))
    console.item('Workspace: ' + str(workspace))
    with awake():
        try:
            acquire['fetch'](rows, workspace / 'downloads')
            inputs = {r['row']: acquire['final_path'](workspace / 'downloads', r) for r in rows}
            family = build(spec, inputs[spec['rows']['builder']], inputs[spec['rows']['catalog']],
                           system, os_root, app, data, workspace, console)
        except (OSError, ValueError, acquire['AcquireError']) as exc:
            raise Stopped('cache rebuild stopped safely', cause=str(exc), code=1) from exc
        finally:
            if not (workspace / 'result.json').exists():
                # This attempt owns this fresh directory; keep its top-level log, not partial caches.
                for log in (workspace / 'build').rglob('*.log'):
                    target = workspace / 'failure-logs' / log.relative_to(workspace / 'build')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(log, target)
                for name in ('build', 'family'):
                    if (workspace / name).is_dir():
                        shutil.rmtree(workspace / name)

    console.item('Verified cache: ' + str(family))
    console.item('The running system has not been changed.')
    return 0
