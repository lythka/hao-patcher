"""Golden Gate native AudioAccessoryServices no-console notification repair.

Suppress only _handleServerDied in _ensureXPCStarted's zero-console-uid path.
The original no-user error and all real XPC-death notification paths remain.

The call is found in the supplied image, not at a stored offset: it is the
one `_handleServerDied` message send (selector resolved through its selector
reference) that is the target of a `cmpl $0,disp8(%rbp); je` branch. Every
build whose code has that shape is handled; any other refuses with MISSING.
"""
import re
import struct
from cache_family import plan_cache_family

IMAGE = b'/System/Library/PrivateFrameworks/AudioAccessoryServices.framework/Versions/A/AudioAccessoryServices'
SELECTOR = b'_handleServerDied'
AFTER = bytes.fromhex('660f1f440000')        # six-byte NOP
# movq SELREF(%rip),%rsi; movq %reg,%rdi; callq *GOT(%rip) (or its repair)
SEND = re.compile(rb'\x48\x8b\x35(.{4})[\x48\x4c]\x89[\xc7\xcf\xd7\xdf\xe7\xef\xf7\xff]'
                  rb'(\xff\x15.{4}|' + re.escape(AFTER) + rb')', re.S)
# cmpl $0x0,disp8(%rbp); je rel32: the console uid compared with zero
ZERO_BRANCH = re.compile(rb'\x83\x7d.\x00\x0f\x84(.{4})', re.S)


def image_layout(read, base):
    """Return (UUID, __text address, __text size) of a native x86_64 image."""
    header = read(base, 32)
    if struct.unpack_from('<II', header) != (0xfeedfacf, 0x1000007):
        raise ValueError('expected native x86_64 AudioAccessoryServices')
    count, size = struct.unpack_from('<II', header, 16)
    commands = read(base + 32, size)
    at, identity, text, code = 0, None, None, None
    for _ in range(count):
        command, length = struct.unpack_from('<II', commands, at)
        if length < 8 or at + length > len(commands):
            raise ValueError('invalid AudioAccessoryServices load command')
        if command == 0x1b:
            identity = commands[at + 8:at + 24]
        if command == 0x19 and commands[at + 8:at + 24].rstrip(b'\0') == b'__TEXT':
            text = struct.unpack_from('<Q', commands, at + 24)[0]
            sections = struct.unpack_from('<I', commands, at + 64)[0]
            for index in range(sections):
                row = at + 72 + index * 80
                if row + 80 > at + length:
                    raise ValueError('invalid AudioAccessoryServices __TEXT section')
                if commands[row:row + 16].rstrip(b'\0') == b'__text':
                    code = struct.unpack_from('<QQ', commands, row + 32)
        at += length
    if identity is None or text != base or code is None:
        raise ValueError('MISSING/unsupported: AudioAccessoryServices UUID, __TEXT or __text layout')
    return identity, code[0], code[1]


def checked_edits(read, base, pointer):
    identity, start, size = image_layout(read, base)
    code = read(start, size)
    targets = {start + m.end() + struct.unpack('<i', m.group(1))[0]
               for m in ZERO_BRANCH.finditer(code)}
    sites = []
    for m in SEND.finditer(code):
        reference = start + m.end(1) + struct.unpack('<i', m.group(1))[0]
        try:
            name = read(pointer(reference), len(SELECTOR) + 1)
        except ValueError:
            continue
        if name == SELECTOR + b'\0' and start + m.start() in targets:
            sites.append((start + m.start(2), m.group(2)))
    if len(sites) != 1:
        raise ValueError('MISSING/unsupported: AudioAccessoryServices %s: expected one '
                         'no-console _handleServerDied call, found %d'
                         % (identity.hex().upper(), len(sites)))
    address, current = sites[0]
    if current == AFTER:
        return []
    return [(address, current, AFTER,
             'do not broadcast server death when no console user exists')]


def plan(directory):
    return plan_cache_family(directory, IMAGE, checked_edits)
