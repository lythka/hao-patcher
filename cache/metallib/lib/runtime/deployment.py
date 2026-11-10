"""Portable private-process bundle. Does not install or enable system services."""
import hashlib
import json
import os
from pathlib import Path
import shutil

from .build import build, native_runtime, sha

MPS_LIBRARY = ('System/Library/Frameworks/MetalPerformanceShaders.framework/Versions/A/'
               'Frameworks/MPSImage.framework/Versions/A/Resources/default.metallib')


def files(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.name != 'build.json'}


def content_identity(package):
    compiler_files = files(package / 'compiler')
    compiler_files = {n: h for n, h in compiler_files.items()
                      if n.startswith(('fw/', 'dl/', 'root/')) or n == 'paths.dylib'}
    adapter_files = {n: sha(package / 'translator' / n) for n in
                     ('compat.dylib', 'gpu-client.dylib', 'sampler-query.dylib',
                      'libLegacyLLVM.dylib')}
    contents = dict(compiler=compiler_files, libraries=files(package / 'libraries'),
                    adapters=adapter_files)
    return hashlib.sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest(), contents


def bundle(output, runtime, payload, libraries):
    """Carry the existing repairs with the new pass; do not regenerate or replace them."""
    output, runtime, payload, libraries = map(
        lambda p: Path(p).resolve(), (output, runtime, payload, libraries))
    output.mkdir(parents=True)  # A new destination, never modify a previous artifact.
    compiler = output / 'compiler'
    native_runtime(compiler, runtime, payload)
    # Input framework links may be relative within the private runtime. Reject an
    # external link rather than ship another development-machine dependency.
    for p in compiler.rglob('*'):
        if p.is_symlink() and not p.resolve().is_relative_to(compiler):
            raise ValueError('compiler input has an external symlink: ' + str(p))
    llvm = compiler / 'fw/GPUCompiler.framework/Versions/3802/Libraries/libLLVM.dylib'
    translation = build(output / 'translator', llvm)
    shutil.copytree(libraries / 'System', output / 'libraries/System', symlinks=False)
    if not (output / 'libraries' / MPS_LIBRARY).is_file():
        raise ValueError('selected library artifact has no MPSImage library')
    # Only executable/data contents affect the cache identity, not timestamps,
    # test drivers, build logs or the host path used to assemble the bundle.
    identity, contents = content_identity(output)
    report = dict(unit='cache/metallib', revision=translation['revision'],
                  commit=translation['commit'], dirty=translation['dirty'],
                  date=translation['date'], kind='private-translation-bundle',
                  inputs=[dict(path=str(p), sha256=sha(p)) for p in
                          (runtime.parent / 'build.json', payload / 'build.json',
                           libraries / 'build.json')],
                  contents=contents, cache_identity=identity,
                  mps_library='libraries/' + MPS_LIBRARY,
                  system_service_integration=False, automatic_dispatch_enforcement=False)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def environment(package, role, cache, *, reviewed_mps_dispatch=False, inherited=None):
    """Environment for a controlled test process, not a launchd configuration.

    Logical32 remains disabled unless the caller explicitly supplies the reviewed
    dispatch assertion. This function cannot observe or enforce a GPU dispatch.
    """
    package, cache = Path(package).resolve(), Path(cache).resolve()
    if role not in ('compiler', 'gpu-client'):
        raise ValueError('role must be compiler or gpu-client')
    report = json.loads((package / 'build.json').read_text())
    if report.get('kind') != 'private-translation-bundle':
        raise ValueError('expected a private translation bundle')
    compiler, translator = package / 'compiler', package / 'translator'
    # A stale caller environment must not accidentally force in-process compiling,
    # enable a different translation mode, or preload the compiler in a GPU client.
    source = os.environ if inherited is None else inherited
    env = {k: v for k, v in source.items()
           if not k.startswith(('DYLD_', 'RP_', 'COMPAT_', 'MTL_'))}
    mode = 'mps-sift-32x1x1' if reviewed_mps_dispatch else 'block-only'
    # Cache selection follows actual contents, including an updated base repair,
    # even if somebody copied a stale build report beside changed binaries.
    identity, _ = content_identity(package)
    namespace = cache / identity / mode
    namespace.mkdir(parents=True, exist_ok=True)
    adapter = 'compat.dylib' if role == 'compiler' else 'gpu-client.dylib'
    env.update(DYLD_FRAMEWORK_PATH=str(compiler / 'fw'),
               DYLD_LIBRARY_PATH=str(compiler / 'dl'),
               DYLD_INSERT_LIBRARIES=str(compiler / 'paths.dylib') + ':' + str(translator / adapter),
               RP_MTLCOMPILER=str(compiler / 'fw/MTLCompiler.framework/Versions/3802/MTLCompiler'),
               RP_ROOT=str(compiler / 'root'), RP_CACHE_DIR=str(namespace) + '/', RP_SLEEP='0',
               COMPAT_BLOCK_READS='per-axis-mip0', COMPAT_CLIENT_ABI='axis-st-v1')
    if reviewed_mps_dispatch:
        env['COMPAT_LOGICAL32'] = 'mps-sift-32x1x1'
    return env
