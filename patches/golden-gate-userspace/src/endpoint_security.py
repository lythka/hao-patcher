"""Match 26A428's native ES selectors to the retained 25G330 provider.

Golden Gate removed the old muteProc/unmuteProc and setAutomata slots. The
remaining methods retain their ordering. Patch the library's selector operands,
not IOConnect globally: other user-client interfaces must keep their own numbers.
Selectors 25..27 map beyond the donor's table and continue to fail explicitly.
No authentication, subscription, response or provider validation is bypassed.
"""
import struct
from legacy_trackpad import plan_cache_family as plan_family, code_sites

IMAGE = b'/usr/lib/libEndpointSecurity.dylib'
def checked_edits(read, address):
    result = []
    for site, before, reason in code_sites(read, address, 'libEndpointSecurity.dylib'):
        old = int(reason)
        if before != b'\xbe'+struct.pack('<I', old):
            raise ValueError('MISSING/unsupported: EndpointSecurity selector operand')
        new = 7 if old == 5 else old+3
        result.append((site, before, b'\xbe'+struct.pack('<I', new),
                       f'EndpointSecurity selector {old} -> {new}'))
    return result


def plan_cache_family(directory):
    return plan_family(directory, IMAGE, checked_edits)
