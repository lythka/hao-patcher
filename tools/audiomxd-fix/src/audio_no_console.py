"""Golden Gate native AudioAccessoryServices no-console notification repair.

Suppress only _handleServerDied in _ensureXPCStarted's zero-console-uid path.
The original no-user error and all real XPC-death notification paths remain.
"""
import struct
import uuid
from cache_family import plan_cache_family

IMAGE = b'/System/Library/PrivateFrameworks/AudioAccessoryServices.framework/Versions/A/AudioAccessoryServices'
UUID = uuid.UUID('34D52126-6651-3DDE-BB42-6B17B8C547E1').bytes
CALL_OFFSET = 0x2456b
BEFORE = bytes.fromhex('ff158738771e')
AFTER = bytes.fromhex('660f1f440000')


def checked_edits(read, base):
    header = read(base, 32)
    if struct.unpack_from('<II', header) != (0xfeedfacf, 0x1000007):
        raise ValueError('expected native x86_64 AudioAccessoryServices')
    count, size = struct.unpack_from('<II', header, 16)
    commands = read(base + 32, size)
    at, identity, text = 0, None, None
    for _ in range(count):
        command, length = struct.unpack_from('<II', commands, at)
        if length < 8 or at + length > len(commands):
            raise ValueError('invalid AudioAccessoryServices load command')
        if command == 0x1b:
            identity = commands[at + 8:at + 24]
        if command == 0x19 and commands[at + 8:at + 24].rstrip(b'\0') == b'__TEXT':
            text = struct.unpack_from('<Q', commands, at + 24)[0]
        at += length
    if identity != UUID or text != base:
        raise ValueError('unsupported AudioAccessoryServices UUID/layout')
    # cmpl $0,-0x2c(%rbp); je zero-console path. The positive-uid path
    # falls through to the normal XPC connection setup.
    if read(base + 0x243ac, 10) != bytes.fromhex('837dd4000f84ab010000'):
        raise ValueError('no-console conditional branch differs')
    if read(base + 0x24561, 10) != bytes.fromhex('488b35a8b3f21f4c89f7'):
        raise ValueError('_handleServerDied selector/self preparation differs')
    current = read(base + CALL_OFFSET, 6)
    if current not in (BEFORE, AFTER):
        raise ValueError('no-console notification instruction differs')
    if current == AFTER:
        return []
    return [(base + CALL_OFFSET, BEFORE, AFTER,
             'do not broadcast server death when no console user exists')]


def plan(directory):
    return plan_cache_family(directory, IMAGE, checked_edits)
