"""Initialize CoreRoutine's console UID before its fallible native lookup.

The native 26A428 caller ignores a NULL SCDynamicStoreCopyConsoleUser result and
reads its untouched stack output. Its existing uid==0 branch skips setting an
explicit XPC target. Initialize that local to zero, preserving successful lookup
results, the original API call, and ownership of the returned name.
"""
import struct
import uuid
from legacy_trackpad import plan_cache_family as plan_family

IMAGE = b'/System/Library/PrivateFrameworks/CoreRoutine.framework/Versions/A/CoreRoutine'
UUID = uuid.UUID('00CAB276-D3AE-35C9-9A4A-B16605C3D72F').bytes
CALL, STUB, CAVE = 0xede8, 0x9179c, 0x1140


def branch(opcode, source, target):
    return bytes([opcode]) + struct.pack('<i', target-source-5)


def checked_edits(read, address):
    header = read(address, 32)
    if struct.unpack_from('<II', header) != (0xfeedfacf, 0x1000007):
        raise ValueError('CoreRoutine: expected native x86_64 Mach-O')
    count, size = struct.unpack_from('<II', header, 16)
    commands = read(address+32, size)
    pos, identity, text = 0, None, None
    sections = []
    for _ in range(count):
        cmd, length = struct.unpack_from('<II', commands, pos)
        if length < 8 or pos+length > len(commands):
            raise ValueError('CoreRoutine: malformed load command')
        if cmd == 0x1b:
            identity = commands[pos+8:pos+24]
        if cmd == 0x19 and commands[pos+8:pos+24].rstrip(b'\0') == b'__TEXT':
            text = struct.unpack_from('<Q', commands, pos+24)[0]
            for index in range(struct.unpack_from('<I', commands, pos+64)[0]):
                section = pos+72+index*80
                if section+80 > pos+length:
                    raise ValueError('CoreRoutine: malformed section table')
                name = commands[section:section+16].rstrip(b'\0')
                start, length_ = struct.unpack_from('<QQ', commands, section+32)
                sections.append((name, start-address, length_))
        pos += length
    if identity != UUID or text != address:
        raise ValueError('CoreRoutine: unsupported UUID/layout')
    # The thunk occupies zero-filled header padding in the first code page,
    # outside both load commands and every section. It does not grow the cache.
    thunk = bytes.fromhex('c70600000000') + branch(0xe9, CAVE+6, STUB)
    code = [start for name, start, _ in sections if name == b'__text']
    if (32+size > CAVE or len(code) != 1 or CAVE+len(thunk) > code[0]
            or CAVE//4096 != code[0]//4096
            or any(start < CAVE+len(thunk) and CAVE < start+length
                   for _, start, length in sections if length)):
        raise ValueError('CoreRoutine: no unused executable header padding')
    guards = (
        (CALL-8, bytes.fromhex('488d75c431ff31d2')+branch(0xe8, CALL, STUB)),
        (0xeed7, bytes.fromhex('837dc400742d')),
    )
    for offset, expected in guards:
        if read(address+offset, len(expected)) != expected:
            raise ValueError(f'CoreRoutine: caller differs at {offset:#x}')
    if read(address+CAVE, len(thunk)) != bytes(len(thunk)):
        raise ValueError('CoreRoutine: header padding is occupied')
    return [
        (address+CAVE, bytes(len(thunk)), thunk, 'initialize local console UID before lookup'),
        (address+CALL, branch(0xe8, CALL, STUB), branch(0xe8, CALL, CAVE),
         'route console lookup through local-output initializer'),
    ]


def plan_cache_family(directory):
    return plan_family(directory, IMAGE, checked_edits)
