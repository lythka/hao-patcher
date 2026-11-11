"""Install the repaired legacy MTLCompilerService and its compat library on an offline System.

Refuses unless the System's service executable, Info.plist and CodeResources are the exact files the artifact was
made from, and every artifact file matches its build.json hash. The library is written before the executable,
so the service never names a missing library."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

SERVICE = ('System/Library/Frameworks/Metal.framework/Versions/A/XPCServices/MTLCompilerService.xpc/'
           'Contents/MacOS/MTLCompilerService')
LIBRARY = 'System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/3802/Libraries/libhaoMetalCompilerCompat.dylib'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(system, artifact):
    system, artifact = Path(system).resolve(), Path(artifact).resolve()
    if system == Path('/'):
        raise ValueError('expected a mounted image System volume, not /')
    record = json.loads((artifact/'build.json').read_text())
    if (record.get('unit'), record.get('operation')) != ('tools/live-text', 'compiler-service'):
        raise ValueError('not a tools/live-text compiler-service artifact')
    if record.get('service') != SERVICE or set(record.get('files', {})) != {SERVICE, LIBRARY}:
        raise ValueError('unexpected compiler-service artifact scope')
    contents = Path(SERVICE).parent.parent
    sources = {Path(SERVICE): record['source_service_sha256'], contents/'Info.plist': record['source_info_sha256'],
               contents/'_CodeSignature/CodeResources': record['source_resources_sha256']}
    for path, expected in sources.items():
        if not (system/path).is_file() or sha(system/path) != expected:
            raise ValueError('MISSING/unsupported: /%s is not the service the artifact was made from' % path)
    for name, expected in record['files'].items():
        if sha(artifact/'replace'/name) != expected:
            raise ValueError('artifact file differs from build.json: ' + name)
    return record


def install(system, artifact):
    record = check(system, artifact)
    system, artifact = Path(system).resolve(), Path(artifact).resolve()
    for name in (LIBRARY, SERVICE):
        target = system/name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            subprocess.run(['chflags', 'noschg,nouchg,norestricted', target], check=True)
        shutil.copyfile(artifact/'replace'/name, target)
        target.chmod(0o755)
        subprocess.run(['chown', '0:0', target], check=True)
        subprocess.run(['chflags', 'restricted', target], check=True)
    print(json.dumps({name: sha(system/name) for name in (SERVICE, LIBRARY)}, indent=1))
    return record


if __name__ == '__main__':
    install(*sys.argv[1:])
