"""Install the Metal27 trial (tools/metal-adapter make.py installed) on an offline System.

  install.py SYSTEM ARTIFACT [--check]

Refuses unless: SYSTEM is not /; the artifact is a `make.py installed` result whose files are exactly the four this
recipe declares and each matches its build.json hash; the System's Metal is one of the files bases.json names
(OCLP's wrapper, with or without golden-gate-hello's edits) or already the artifact's; neither MTLCompiler 32023 nor
32024 exists on the System other than as the artifact's (on Golden Gate they are dyld cache images only); and every
OCLP file the stand-in routes to is there (bases.json "needs"). Writes the stand-ins, then the shim, then Metal, so
a write that stops part way leaves OCLP's wrapper in charge. --check only checks."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ORDER = ['System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/32023/MTLCompiler',
         'System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/32024/MTLCompiler',
         'System/Library/Frameworks/Metal.framework/Versions/A/libHaoMetalShim.dylib',
         'System/Library/Frameworks/Metal.framework/Versions/A/Metal']
METAL = ORDER[3]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(system, artifact, bases=None):
    system, artifact = Path(system).resolve(), Path(artifact).resolve()
    if system == Path('/'):
        raise ValueError('expected a mounted image System volume, not / (the live path is tools/hao)')
    bases = bases or json.loads((HERE / 'bases.json').read_text())
    record = json.loads((artifact / 'build.json').read_text())
    if record.get('tool') != 'tools/metal-adapter/make.py installed' or sorted(record.get('files', {})) != sorted(ORDER):
        raise ValueError('not a tools/metal-adapter installed artifact with the four declared files')
    for name, expected in record['files'].items():
        if sha(artifact / 'root' / name) != expected:
            raise ValueError('artifact file differs from build.json: ' + name)
    for name in bases['needs']:
        if not (system / name).is_file():
            raise ValueError('MISSING: /%s (OCLP Metal 3802 stack; the stand-in routes there)' % name)
    have = sha(system / METAL) if (system / METAL).is_file() else None
    if have not in bases[METAL] and have != record['files'][METAL]:
        raise ValueError('MISSING/unsupported: /%s is %s, not a Metal this trial replaces (bases.json)' % (METAL, have))
    for name in ORDER[:3]:
        if (system / name).exists() and sha(system / name) != record['files'][name]:
            raise ValueError('/%s exists and is not the artifact\'s: not replacing an unknown file' % name)
    return record


def install(system, artifact):
    record = check(system, artifact)
    system, artifact = Path(system).resolve(), Path(artifact).resolve()
    for name in ORDER:
        target = system / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            subprocess.run(['chflags', 'noschg,nouchg,norestricted', target], check=True)
        shutil.copyfile(artifact / 'root' / name, target)
        target.chmod(0o755)
        subprocess.run(['chown', '0:0', target], check=True)
        subprocess.run(['chflags', 'restricted', target], check=True)
    print(json.dumps({name: sha(system / name) for name in ORDER}, indent=1))
    return record


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--check']
    try:
        check(*args) if '--check' in sys.argv[1:] else install(*args)
    except ValueError as x:
        sys.exit('golden-gate-metal27: %s' % x)
