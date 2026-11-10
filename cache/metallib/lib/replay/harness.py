#!/usr/bin/env python3
"""Host replay harness for the HD 4000 (MTLCompiler 3802) legalizer crash.

Pipeline:
  CANDIDATE.ll -> xcrun metal -c -> .air -> xcrun metallib (+ vertex air) -> replay-own.
Every run gets a fresh private output folder.

Library use:  classify_ll(ll_text, rundir, vertex_air, vertex, fragment)
              run_replay(metallib, rundir, vertex, fragment)
"""
import hashlib, json, os, pathlib, re, subprocess, time

# Legacy replay controls require an explicit prepared runtime and local tools.
P = pathlib.Path(os.environ['METALLIB_RUNTIME']).resolve()
W = pathlib.Path(os.environ.get('METALLIB_REPLAY_TOOLS', str(P / 'bin'))).resolve()
SETTINGS = dict(DYLD_FRAMEWORK_PATH=str(P / 'fw'), DYLD_LIBRARY_PATH=str(P / 'dl'),
                DYLD_INSERT_LIBRARIES=str(W / 'ip.dylib'), MTL_MONOLITHIC_COMPILER='1',
                RP_ROOT=str(P / 'root'),
                RP_MTLCOMPILER=str(P / 'fw/MTLCompiler.framework/Versions/3802/MTLCompiler'),
                RP_STEP0='9', RP_SLEEP='0')
SIG = ('[segv] sig 11 addr 0x28', 'libLLVM.dylib+0x620899')


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def capture_tool_pins():
    paths = [pathlib.Path(os.environ.get('METALLIB_REPLAY', str(W / 'replay'))),
             pathlib.Path(os.environ.get('METALLIB_INTERPOSE', SETTINGS['DYLD_INSERT_LIBRARIES'])),
             P / 'fw/Metal.framework/Versions/A/Metal', pathlib.Path(SETTINGS['RP_MTLCOMPILER']),
             P / 'bundles/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver']
    paths += [p for root in (P / 'fw', P / 'dl') for p in root.rglob('*.dylib') if p.is_file()]
    return {str(p): sha(p) for p in sorted(set(paths))}


def run_replay(metallib, rundir, vertex, fragment, pixel_format='80', shader_state=None, timeout=40, mode='render',
               vertex_descriptor=False, bool_true=None, uint_constants=None, tool_sha256=None):
    rundir = pathlib.Path(rundir); rundir.mkdir(parents=True); (rundir / 'cache').mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith('DYLD_') and not k.startswith('RP_')}
    env.update(SETTINGS);
    if os.environ.get('METALLIB_INTERPOSE'): env['DYLD_INSERT_LIBRARIES'] = os.environ['METALLIB_INTERPOSE']
    env.update(RP_OUT=str(rundir), RP_CACHE_DIR=str(rundir / 'cache') + '/')
    if shader_state: env['RP_SHADER_STATE'] = shader_state
    if vertex_descriptor: env['RP_VERTEX_DESCRIPTOR'] = 'auto'
    if bool_true: env['RP_BOOL_TRUE'] = ','.join(bool_true)
    if uint_constants is not None:
        if mode != 'compute' or not isinstance(uint_constants, dict) or any(
                not isinstance(k, str) or type(v) is not int or not 0 <= v <= 0xffffffff
                for k, v in uint_constants.items()):
            raise ValueError('uint constants require compute mode and uint32 values')
        env['RP_UINT_CONSTANTS'] = json.dumps(uint_constants)
    args = [os.environ.get('METALLIB_REPLAY', str(W / 'replay')), str(P / 'fw/Metal.framework/Versions/A/Metal'),
            str(P / 'bundles/AppleIntelHD4000GraphicsMTLDriver.bundle'), str(metallib), vertex, 'render',
            fragment, pixel_format]
    if mode in ('compute', 'inspect'): args = args[:5] + [mode]  # vertex = kernel name
    if tool_sha256 is None: tool_sha256 = capture_tool_pins()
    settings = {k: v for k, v in env.items() if k.startswith(('DYLD_', 'RP_', 'MTL_'))}
    (rundir / 'invocation.json').write_text(json.dumps(dict(args=args, settings=settings,
                                                            tool_sha256=tool_sha256,
                                                            shader_state=shader_state,
                                                            vertex_descriptor=vertex_descriptor, bool_true=bool_true,
                                                            uint_constants=uint_constants), indent=2) + '\n')
    t = time.monotonic()
    with (rundir / 'stdout.log').open('wb') as so, (rundir / 'stderr.log').open('wb') as se:
        p = subprocess.Popen(args, env=env, stdout=so, stderr=se)
        try:
            code = p.wait(timeout=timeout); state = 'exited'
        except subprocess.TimeoutExpired:
            p.kill(); code = p.wait(); state = 'timeout'
    log = (rundir / 'stderr.log').read_text(errors='replace')
    crash = all(s in log for s in SIG)
    ok = code == 0 and 'pipeline=OK' in log and 'BuildRequest #' in log and not re.search(r'reply #\d+ status=[1-9]', log)
    other = [s for s in log.splitlines() if any(x in s for x in ['[segv]', 'Cannot select', 'LLVM ERROR',
                                                                   'failed assertion', 'pipeline=FAIL', 'abort'])][:4]
    status = 'loaded' if mode == 'inspect' and code == 0 and 'LIBRARY loaded=yes' in log else ('pass' if ok else ('crash' if crash else 'fail'))
    res = dict(status=status, exit_code=code, state=state, seconds=round(time.monotonic() - t, 2),
               lines=other, metallib=str(metallib), metallib_sha256=sha(metallib))
    (rundir / 'result.json').write_text(json.dumps(res, indent=2) + '\n')
    return res


def build(ll_text, rundir, vertex_air):
    rundir = pathlib.Path(rundir); rundir.mkdir(parents=True, exist_ok=True)
    ll = rundir / 'CANDIDATE.ll'; ll.write_text(ll_text)
    air = rundir / 'CANDIDATE.air'; lib = rundir / 'CANDIDATE.metallib'
    r = subprocess.run(['xcrun', 'metal', '-c', '-mmacos-version-min=13.0', str(ll), '-o', str(air)],
                       capture_output=True, text=True)
    if r.returncode:
        (rundir / 'build.err').write_text(r.stderr); return None
    r = subprocess.run(['xcrun', 'metallib', str(vertex_air), str(air), '-o', str(lib)], capture_output=True, text=True)
    if r.returncode:
        (rundir / 'build.err').write_text(r.stderr); return None
    return lib


def classify_ll(ll_text, rundir, vertex_air=W / 'reduce-baseline/plane__vertex.air', vertex='plane__vertex',
                fragment='uber_resolve_fragment'):
    lib = build(ll_text, rundir, vertex_air)
    if lib is None: return dict(status='build-error')
    return run_replay(lib, pathlib.Path(rundir) / 'replay', vertex, fragment)


if __name__ == '__main__':
    import sys
    ll, out = sys.argv[1], sys.argv[2]
    print(json.dumps(classify_ll(pathlib.Path(ll).read_text(), out), indent=1))
