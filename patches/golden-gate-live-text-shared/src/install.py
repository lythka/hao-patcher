"""Install a tools/live-text framework artifact on an offline, writable System.

Checks, before writing anything: the System's libhelloblur is the exact library the artifact was made from
(golden-gate-hello installed it), its MPSNDArray library is the one the runtime's repaired copy was made from,
the repaired compiler service is installed (metal-compiler-compat), the runtime folder holds exactly its
manifest's files, and its install root is new. Then copies the runtime and replaces libhelloblur."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

LIBRARY = 'usr/lib/libhelloblur.dylib'
COMPILER_COMPAT = ('System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/3802/Libraries/'
                   'libhaoMetalCompilerCompat.dylib')
# golden-gate-live-text-service looks for the runtime here.
ROOT = '/usr/lib/hao-live-text'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(system, artifact):
    system, artifact = Path(system).resolve(), Path(artifact).resolve()
    if system == Path('/'):
        raise ValueError('expected a mounted image System volume, not /')
    record = json.loads((artifact/'build.json').read_text())
    root = record.get('runtime_root', '')
    if ((record.get('unit'), record.get('operation')) != ('tools/live-text', 'framework')
            or record.get('library_path') != LIBRARY or root != ROOT
            or record.get('umbrella') != root + '/LiveText.dylib'
            or record.get('photos_modified') is not False or record.get('metal_modified') is not False):
        raise ValueError('not a tools/live-text framework artifact for ' + ROOT)
    if sha(system/LIBRARY) != record['source_library_sha256']:
        raise ValueError('MISSING/unsupported: /%s is not the libhelloblur the artifact was made from '
                         '(golden-gate-hello must run first)' % LIBRARY)
    for path, expected in ((artifact/'original/libhelloblur.dylib', record['source_library_sha256']),
                           (artifact/'replace'/LIBRARY, record['library_sha256']),
                           (artifact/'runtime/build.json', record['runtime_manifest_sha256'])):
        if sha(path) != expected:
            raise ValueError('artifact file differs from build.json: %s' % path)
    runtime = json.loads((artifact/'runtime/build.json').read_text())
    if (runtime.get('compiler_mode') != 'shared' or runtime.get('install_root') != root
            or runtime.get('load_paths') != [record['umbrella']]):
        raise ValueError('runtime manifest does not match the framework record')
    folder = artifact/'runtime'
    if any(p.is_symlink() for p in folder.rglob('*')):
        raise ValueError('runtime contains a symlink')
    if {str(p.relative_to(folder)) for p in folder.rglob('*')
            if p.is_file() and p != folder/'build.json'} != set(runtime['files']):
        raise ValueError('runtime file set differs from its manifest')
    for name, digest in runtime['files'].items():
        if sha(folder/name) != digest:
            raise ValueError('runtime file changed: ' + name)
    metallib = runtime['inputs']['MPSNDArray.metallib']
    if not (system/metallib['source']).is_file() or sha(system/metallib['source']) != metallib['source_sha256']:
        raise ValueError('MISSING/unsupported: /%s is not the library the runtime\'s repaired copy was made from'
                         % metallib['source'])
    if not (system/COMPILER_COMPAT).is_file():
        raise ValueError('MISSING: /%s (metal-compiler-compat must run first)' % COMPILER_COMPAT)
    destination = system/root.lstrip('/')
    if destination.exists() or destination.is_symlink():
        raise ValueError('/%s exists; start from the previous image stage' % root.lstrip('/'))
    return record, destination


def install(system, artifact):
    record, destination = check(system, artifact)
    system, artifact = Path(system).resolve(), Path(artifact).resolve()
    run = lambda *args: subprocess.run(list(map(str, args)), check=True)
    shutil.copytree(artifact/'runtime', destination)
    run('chown', '-R', '0:0', destination)
    run('chmod', '-R', 'u=rwX,go=rX', destination)
    target = system/LIBRARY
    run('chflags', 'noschg,nouchg,norestricted', target)
    shutil.copyfile(artifact/'replace'/LIBRARY, target)
    target.chmod(0o755)
    run('chown', '0:0', target)
    run('chflags', 'restricted', target)
    print(json.dumps({'runtime': record['runtime_root'], 'libhelloblur_sha256': sha(target),
                      'umbrella': record['umbrella']}, indent=1))
    return record


if __name__ == '__main__':
    install(*sys.argv[1:])
