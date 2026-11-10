"""Exercise the C synchronization pass against the pinned LLVM, without GPU calls.

Usage: check_legacy_sync.py TRANSFORM_DRIVER LLVM_LIBRARY NEW_OUTPUT
Run on the Intel target. Each case verifies the original is unchanged and the
result passes LLVM verification, then checks repaired or deliberately retained IR.
"""
import json
from pathlib import Path
import subprocess
import sys


def check(driver, llvm, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    barrier = '''define void @probe(i32 %flags, i32 %scope) {
  tail call void @air.wg.barrier(i32 %flags, i32 5, i32 %scope) #0, !compat.tag !0
  ret void
}
declare void @air.wg.barrier(i32, i32, i32) #0
attributes #0 = { convergent nounwind }
!0 = !{!"preserve"}
'''
    atomic = '''define i32 @probe(i32 addrspace(1)* %p, i32 %v) {
  %r = call i32 @air.atomic.global.add.u.i32(i32 addrspace(1)* nonnull %p, i32 %v, i32 0, i32 2, i32 0, i1 true), !compat.tag !0
  ret i32 %r
}
declare i32 @air.atomic.global.add.u.i32(i32 addrspace(1)* nocapture, i32, i32, i32, i32, i1) nounwind
!0 = !{!"preserve"}
'''
    compare = '''define i1 @probe(i32 addrspace(3)* %p, i32* %expected, i32 %desired) {
  %r = call i1 @air.atomic.local.cmpxchg.weak.i32(i32 addrspace(3)* %p, i32* %expected, i32 %desired, i32 0, i32 0, i32 1, i32 0, i1 false)
  ret i1 %r
}
declare i1 @air.atomic.local.cmpxchg.weak.i32(i32 addrspace(3)*, i32*, i32, i32, i32, i32, i32, i1)
'''
    cases = {
        'barrier': (barrier, True, ['@air.wg.barrier(i32 %flags, i32 %scope)', '!compat.tag', 'convergent']),
        'simd-barrier': (barrier.replace('wg.barrier', 'simdgroup.barrier'), True,
                         ['@air.simdgroup.barrier(i32 %flags, i32 %scope)']),
        'atomic': (atomic, True, ['i32 %v, i32 0, i32 2, i1 true)', 'nonnull', 'nocapture', '!compat.tag']),
        'compare-exchange': (compare, True, ['i32 %desired, i32 0, i32 0, i32 1, i1 false)']),
        'legacy': (barrier.replace('i32 %flags, i32 5, i32 %scope', 'i32 %flags, i32 %scope')
                  .replace('barrier(i32, i32, i32)', 'barrier(i32, i32)'), False, []),
        'unknown': (barrier.replace('air.wg.barrier', 'air.other'), False, []),
        'barrier-order': (barrier.replace('i32 5, i32 %scope', 'i32 0, i32 %scope'), False, []),
        'dynamic-order': (barrier.replace('i32 5, i32 %scope', 'i32 %flags, i32 %scope'), False, []),
        'atomic-order': (atomic.replace('i32 0, i32 2, i32 0', 'i32 2, i32 2, i32 0'), False, []),
        'atomic-fence': (atomic.replace('i32 0, i1 true', 'i32 1, i1 true'), False, []),
        'compare-failure-order': (compare.replace('i32 %desired, i32 0, i32 0', 'i32 %desired, i32 0, i32 2'), False, []),
        'escaped': (barrier + '@escaped = global void (i32, i32, i32)* @air.wg.barrier\n', False, []),
        'string-attributes': (barrier.replace('convergent nounwind', r'convergent nounwind "unknown\22key\00tail"="value\22bytes"'), True, ['unknown\"key\x00tail', r'value\22bytes']),
        'notail': (barrier.replace('tail call', 'notail call'), False, []),
        'atomic-bitwise-suffix': (atomic.replace('add.u', 'and.s'), True, ['@air.atomic.global.and.s.i32']),
        'musttail': (barrier.replace('@probe(i32 %flags, i32 %scope)', '@probe(i32 %flags, i32 %order, i32 %scope)')
                    .replace('tail call', 'musttail call'), False, []),
    }
    for name, (source, changed, needles) in cases.items():
        before, after = output/(name+'.ll'), output/(name+'.out.ll')
        before.write_text(source)
        run = subprocess.run([driver, llvm, str(before), str(after)], text=True, capture_output=True)
        (output/(name+'.log')).write_text(run.stdout + run.stderr)
        if run.returncode:
            raise AssertionError((name, run.returncode, run.stderr))
        result = json.loads(run.stdout)
        assert result['original_unchanged'] and result['valid'], (name, result)
        assert result['changed'] == changed, (name, result)
        emitted = after.read_text()
        for needle in needles:
            assert needle in emitted, (name, needle)
        print('PASS', name, flush=True)
    print('PASS:', len(cases), 'runtime synchronization cases; no GPU execution')


if __name__ == '__main__':
    check(*sys.argv[1:])
