"""Assemble the Live Text client runtime: x86_64 helpers every Metal client loads through libhelloblur.

Shared-compiler mode only: shaders compile in the image's (repaired) MTLCompilerService, never in a private
compiler. The runtime holds
  libLiveText.dylib          umbrella; needs every helper below, so one load command attaches them all
  libLiveTextConfig.dylib    process-local settings (HAO_LIVE_TEXT_ROOT, VISION_MPSNDARRAY_LIBRARY), first
  gpu-client.dylib           cache/metallib's GPU-client adapter (cold and cached translated shaders)
  libLiveTextPolicy.dylib    TextRecognition E5 compiles: GPU device mask, the prepared detector, MPSGraph non-memref
  libLiveTextLibrary.dylib   HD 4000 devices load MPSNDArray.metallib (legacy-sync repaired) instead of Apple's
  libhao_input_coherence.dylib  IOSurface input coherence for the detector's E5 operations
  MPSNDArray.metallib, detector/  the repaired library and the input-binding-rewritten detector
The MPS matrix initializer repair is not here: golden-gate-userspace applies it to the x86_64 dyld cache.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
REPO = UNIT.parents[1]
sys.path.insert(0, str(HERE))
import detector as detector_tool  # noqa: E402

UMBRELLA = 'libLiveText.dylib'
# The System library the runtime's MPSNDArray.metallib replaces for HD 4000 devices (installers check its hash).
SOURCE_METALLIB = ('System/Library/Frameworks/MetalPerformanceShaders.framework/Versions/A/Frameworks/'
                   'MPSNDArray.framework/Versions/A/Resources/default.metallib')
HELPERS = ['libLiveTextConfig.dylib', 'gpu-client.dylib', 'libLiveTextPolicy.dylib', 'libLiveTextLibrary.dylib',
           'libhao_input_coherence.dylib']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(*args):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('%s failed (%d): %s' % (args[0], result.returncode, (result.stdout + result.stderr).strip()))


def check_root(root):
    if not re.fullmatch(r'(/[A-Za-z0-9._-]+)+', root) or '/../' in root + '/' or '/./' in root + '/':
        raise ValueError('install root must be a canonical absolute path: %r' % root)
    return root


def build(output, install_root, compat, metallib, detector, source_metallib):
    """Build the runtime for INSTALL_ROOT (absolute; every helper's install name lives there). METALLIB is the
    repaired MPSNDArray library, SOURCE_METALLIB the Apple library it was made from (recorded for the installer)."""
    root = check_root(install_root)
    out = Path(output).resolve()
    compat, metallib, detector, source_metallib = (Path(p).resolve() for p in (compat, metallib, detector, source_metallib))
    record = json.loads((compat/'build.json').read_text())
    if record.get('outputs', {}).get('gpu-client.dylib') != sha(compat/'gpu-client.dylib'):
        raise ValueError('compiler adapter artifact differs from its build.json')
    out.mkdir(parents=True, exist_ok=False)
    inputs = {}
    shutil.copy2(compat/'gpu-client.dylib', out/'gpu-client.dylib')
    shutil.copy2(metallib, out/'MPSNDArray.metallib')
    inputs['gpu-client.dylib'] = dict(path=str(compat/'gpu-client.dylib'), sha256=sha(compat/'gpu-client.dylib'),
                                      cache_metallib_commit=record.get('commit'), revision=record.get('revision'))
    inputs['MPSNDArray.metallib'] = dict(path=str(metallib), sha256=sha(metallib), source=SOURCE_METALLIB,
                                         source_sha256=sha(source_metallib))
    inputs['detector'] = detector_tool.prepare(detector, out/'detector')

    def compile(name, source, *extra):
        run('xcrun', 'clang++' if str(source).endswith('.mm') else 'clang', '-arch', 'x86_64',
            '-mmacosx-version-min=13.0', '-O2', '-dynamiclib', source, *extra,
            # A /usr/lib install name makes ld treat the library as shared-cache eligible and refuse
            # -undefined dynamic_lookup; these helpers are never in a dyld cache.
            '-Wl,-install_name,' + root + '/' + name, '-Wl,-not_for_dyld_shared_cache', '-o', out/name)
        # An unsigned x86_64 image hangs in exec on Apple silicon hosts; sign every output.
        run('codesign', '-f', '-s', '-', out/name)

    src = UNIT/'src'
    config = out/'libLiveTextConfig.dylib'
    compile(config.name, src/'config.c', '-DLIVE_TEXT_ROOT=' + json.dumps(root))
    first = ('-Wl,-needed_library,' + str(config),)
    compile('libLiveTextPolicy.dylib', src/'policy.mm', '-std=c++17', '-fobjc-arc', '-undefined', 'dynamic_lookup',
            '-framework', 'Foundation', '-framework', 'IOSurface', *first)
    compile('libhao_input_coherence.dylib', src/'input_coherence.mm', '-std=c++17', '-fobjc-arc', '-fblocks',
            '-undefined', 'dynamic_lookup', '-framework', 'IOSurface', '-framework', 'CoreFoundation', *first)
    compile('libLiveTextLibrary.dylib', src/'library.m', '-fobjc-arc', '-fblocks', '-framework', 'Foundation',
            '-framework', 'Metal', *first)
    run('install_name_tool', '-id', root + '/gpu-client.dylib', out/'gpu-client.dylib')
    run('codesign', '-f', '-s', '-', out/'gpu-client.dylib')
    umbrella = out/'umbrella.c'
    umbrella.write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n'
                        '__attribute__((constructor)) static void loaded(void) { '
                        'const char *d=getenv("HAO_LIVE_TEXT_DIAGNOSTICS"); '
                        'if(d&&!strcmp(d,"1")) fputs("hao: Live Text client support loaded\\n",stderr); }\n')
    compile(UMBRELLA, umbrella, '-framework', 'Foundation', '-framework', 'Metal',
            *('-Wl,-needed_library,' + str(out/name) for name in HELPERS))
    umbrella.unlink()
    files = {str(f.relative_to(out)): sha(f) for f in sorted(out.rglob('*')) if f.is_file() and not f.is_symlink()}
    git = lambda *a: subprocess.check_output(['git', '-C', str(REPO), *a], text=True).strip()
    manifest = dict(unit='tools/live-text', operation='runtime', compiler_mode='shared', install_root=root,
                    commit=git('rev-parse', 'HEAD'), dirty=bool(git('status', '--porcelain', '--', str(UNIT))),
                    date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    source_sha256={str(p.relative_to(REPO)): sha(p) for p in sorted(src.iterdir()) if p.is_file()},
                    inputs=inputs, files=files, load_paths=[root + '/' + UMBRELLA],
                    helper_paths=[root + '/' + name for name in HELPERS])
    (out/'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def verify(runtime):
    """The runtime manifest, after checking that the folder holds exactly its files with their hashes."""
    runtime = Path(runtime).resolve()
    record = json.loads((runtime/'build.json').read_text())
    root = record.get('install_root')
    if (record.get('unit') != 'tools/live-text' or record.get('operation') != 'runtime'
            or record.get('compiler_mode') != 'shared' or not isinstance(root, str)
            or record.get('load_paths') != [check_root(root) + '/' + UMBRELLA]):
        raise ValueError('expected a shared-compiler Live Text runtime')
    if any(p.is_symlink() for p in runtime.rglob('*')):
        raise ValueError('runtime contains a symlink')
    actual = {str(p.relative_to(runtime)) for p in runtime.rglob('*') if p.is_file() and p != runtime/'build.json'}
    if actual != set(record['files']):
        raise ValueError('runtime file set differs from its manifest')
    for name, digest in record['files'].items():
        if sha(runtime/name) != digest:
            raise ValueError('runtime file changed: ' + name)
    return record
