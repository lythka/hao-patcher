"""Initialize CoreRoutine's console UID before its fallible native lookup.

The native 26A428 caller ignores a NULL SCDynamicStoreCopyConsoleUser result and
reads its untouched stack output. Its existing uid==0 branch skips setting an
explicit XPC target. Initialize that local to zero, preserving successful lookup
results, the original API call, and ownership of the returned name.
"""
import struct
from legacy_trackpad import plan_cache_family as plan_family, code_sites, image_layout

IMAGE = b'/System/Library/PrivateFrameworks/CoreRoutine.framework/Versions/A/CoreRoutine'
def branch(opcode, source, target):
    return bytes([opcode]) + struct.pack('<i', target-source-5)


def checked_edits(read, address):
    header_end, sections = image_layout(read, address)
    lookup, guard = code_sites(read, address, 'CoreRoutine')
    site, caller, _ = lookup
    if not 0 < guard[0]-site < 512:
        raise ValueError('MISSING/unsupported: CoreRoutine console UID branch is not near its lookup')
    call = site+8
    stub = call+5+struct.unpack('<i', caller[9:13])[0]
    if not any(start <= stub < start+size for name, (start, size) in sections.items() if name == b'__stubs'):
        raise ValueError('MISSING/unsupported: CoreRoutine lookup does not call a local stub')
    code = sections[b'__text'][0]
    cave = code-16
    thunk = bytes.fromhex('c70600000000')+branch(0xe9, cave+6, stub)
    if (cave < address+header_end or cave//4096 != code//4096
            or any(start < cave+len(thunk) and cave < start+size for start, size in sections.values() if size)
            or read(cave, len(thunk)) != bytes(len(thunk))):
        raise ValueError('MISSING/unsupported: CoreRoutine unused executable header padding')
    return [(cave, bytes(len(thunk)), thunk, 'initialize local console UID before lookup'),
            (call, caller[8:13], branch(0xe8, call, cave),
             'route console lookup through local-output initializer')]


def plan_cache_family(directory):
    return plan_family(directory, IMAGE, checked_edits)
