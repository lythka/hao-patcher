"""Attach the Live Text runtime to libhelloblur, the dylib OCLP's Metal wrapper already loads (golden-gate-hello).

Every Metal client loads /usr/lib/libhelloblur.dylib through Metal's weak load command; the copy made here has
one more load command, the runtime's umbrella. Photos, Metal and every other Apple binary keep their bytes. The
other load commands, their order and the signature flags are unchanged; the copy is re-signed ad hoc, like the
original. If the umbrella cannot load, dyld skips the weak libhelloblur as a whole (no hello blur cache), and Metal
clients still start.
"""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
REPO = UNIT.parents[1]
sys.path.insert(0, str(HERE))
import macho_edit as editor  # noqa: E402
import runtime as runtime_tool  # noqa: E402

STARTUP = 'usr/lib/libhelloblur.dylib'
DYLIB = 6


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def attach(library, umbrella):
    commands = editor.load_commands(library, DYLIB)
    identities = [library[at+24:at+size].split(b'\0', 1)[0].decode() for cmd, at, size in commands if cmd == 0xd]
    if identities != ['/' + STARTUP]:
        raise ValueError('expected the libhelloblur install name /' + STARTUP)
    return editor.append_dylibs(library, [umbrella], resign_filetype=DYLIB)


def build(library, runtime, output):
    library, runtime, output = (Path(p).resolve() for p in (library, runtime, output))
    record = runtime_tool.verify(runtime)
    umbrella = record['load_paths'][0]
    before = library.read_bytes()
    changed, edit = attach(before, umbrella)
    output.mkdir(parents=True, exist_ok=False)
    target = output/'replace'/STARTUP
    target.parent.mkdir(parents=True)
    target.write_bytes(changed)
    target.chmod(0o755)
    run('codesign', '-f', '-s', '-', '--preserve-metadata=identifier,entitlements,flags,runtime', target)
    run('codesign', '--verify', target)
    after = target.read_bytes()
    expected = editor.dependencies(before, editor.load_commands(before, DYLIB)) + [(0xc, umbrella)]
    if editor.dependencies(after, editor.load_commands(after, DYLIB)) != expected:
        raise ValueError('libhelloblur dependency ordinals changed')
    old = editor.signature_directories(before, editor.load_commands(before, DYLIB))
    new = editor.signature_directories(after, editor.load_commands(after, DYLIB))
    if [c[7] for c in old] != [c[7] for c in new]:
        raise ValueError('libhelloblur signature flags changed')
    shutil.copytree(runtime, output/'runtime')
    (output/'original').mkdir()
    shutil.copy2(library, output/'original/libhelloblur.dylib')
    git = lambda *a: run('git', '-C', REPO, *a)
    manifest = dict(unit='tools/live-text', operation='framework',
                    commit=git('rev-parse', 'HEAD'), dirty=bool(git('status', '--porcelain', '--', str(UNIT))),
                    date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    inputs={str(library): sha(library), str(runtime/'build.json'): sha(runtime/'build.json')},
                    source_sha256={str(p.relative_to(REPO)): sha(p) for p in (HERE/'framework.py', HERE/'macho_edit.py')},
                    runtime_root=record['install_root'], umbrella=umbrella,
                    runtime_manifest_sha256=sha(output/'runtime/build.json'),
                    library_path=STARTUP, source_library_sha256=sha(library), library_sha256=sha(target),
                    edit=edit, photos_modified=False, metal_modified=False)
    (output/'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
