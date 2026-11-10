"""Match 26A428's native ES selectors to the retained 25G330 provider.

Golden Gate removed the old muteProc/unmuteProc and setAutomata slots. The
remaining methods retain their ordering. Patch the library's selector operands,
not IOConnect globally: other user-client interfaces must keep their own numbers.
Selectors 25..27 map beyond the donor's table and continue to fail explicitly.
No authentication, subscription, response or provider validation is bypassed.
"""
import struct
import uuid
from legacy_trackpad import plan_cache_family as plan_family

IMAGE = b'/usr/lib/libEndpointSecurity.dylib'
UUID = uuid.UUID('F61755B5-7585-32FD-BE8F-366C1EAE012C').bytes
# Every changed mov esi,selector preceding an IOConnectCall* in this image.
# The conditional mute/unmute helper starts at 13 and subtracts a carry: changing
# that base to 16 maps both branches (12/13 -> 15/16).
SELECTORS = (
    (0x2702, 12, 15), (0x2862, 13, 16), (0x28ff, 12, 15),
    (0x297f, 13, 16), (0x2ba5, 13, 16), (0x2d1c, 5, 7),
    (0x2f85, 14, 17), (0x3781, 9, 12), (0x3bf4, 10, 13),
    (0x3c84, 18, 21), (0x3fe7, 11, 14), (0x45c0, 27, 30),
    (0x46fb, 15, 18), (0x4749, 15, 18), (0x4b93, 6, 9),
    (0x52fa, 8, 11), (0x5b6b, 17, 20), (0x5bbb, 17, 20),
    (0x5c26, 16, 19), (0x5c7b, 19, 22), (0x5ccb, 19, 22),
    (0x5d19, 20, 23), (0x5d8a, 21, 24), (0x5e09, 22, 25),
    (0x5e76, 23, 26), (0x5ee9, 25, 28), (0x5f56, 26, 29),
    (0x5f9b, 20, 23), (0x5feb, 20, 23), (0x6040, 24, 27),
    (0x621d, 7, 10),
)


def checked_edits(read, address):
    header = read(address, 32)
    if struct.unpack_from('<II', header) != (0xfeedfacf, 0x1000007):
        raise ValueError('EndpointSecurity: expected native x86_64 Mach-O')
    count, size = struct.unpack_from('<II', header, 16)
    commands = read(address+32, size)
    pos, identity, text = 0, None, None
    for _ in range(count):
        cmd, length = struct.unpack_from('<II', commands, pos)
        if length < 8 or pos+length > len(commands):
            raise ValueError('EndpointSecurity: malformed load command')
        if cmd == 0x1b:
            identity = commands[pos+8:pos+24]
        if cmd == 0x19 and commands[pos+8:pos+24].rstrip(b'\0') == b'__TEXT':
            text = struct.unpack_from('<Q', commands, pos+24)[0]
        pos += length
    if identity != UUID or text != address:
        raise ValueError('EndpointSecurity: unsupported UUID/layout')
    result = []
    for offset, old, new in SELECTORS:
        before = b'\xbe' + struct.pack('<I', old)
        if read(address+offset, 5) != before:
            raise ValueError(f'EndpointSecurity: selector instructions differ at {offset:#x}')
        result.append((address+offset, before, b'\xbe'+struct.pack('<I', new),
                       f'EndpointSecurity selector {old} -> {new}'))
    return result


def plan_cache_family(directory):
    return plan_family(directory, IMAGE, checked_edits)
