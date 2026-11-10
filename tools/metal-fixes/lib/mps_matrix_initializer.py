"""Pinned 26A428 x86 MPSNDArray initializer repair; byte buffers only.

The cache planner verifies image identity and reads the cache without changing
it. The caller owns application of returned edits and signing. All preferred
addresses are unslid.
"""
from dataclasses import dataclass
import struct
import uuid
from dyld_cache import plan_cache_family as plan_family

IMAGE = b'/System/Library/Frameworks/MetalPerformanceShaders.framework/Versions/A/Frameworks/MPSNDArray.framework/Versions/A/MPSNDArray'
UUID = uuid.UUID('530AFC24-8A84-3B7A-9601-9608EA68391C').bytes
CONTEXT_OFFSET = 0x2EE43

PREFERRED_ADDRESS = 0x7FF8140BEE43
# Entire device-family decision and native MPSMatrix construction/store block.
# Exact RIP displacements deliberately pin this to the inspected cache build.
ORIGINAL = bytes.fromhex(
    "4a8b043b8b80c005000083c0e683f8f77744"
    "488b3d2c3af62be88b801f00488b35b0685d2d0f57c00f110424"
    "f20f100569f719004889c74c89f231c94531c04531c90f28c8"
    "ff157273f82b488b0d33c88b2f4889040b"
)
# add eax,-26 / cmp eax,-9 / ja skip -> -25 / -8, same branch target.
ADD_IMMEDIATE_OFFSET = 12
CMP_IMMEDIATE_OFFSET = 15
_fixed = bytearray(ORIGINAL)
_fixed[ADD_IMMEDIATE_OFFSET] = 0xE7
_fixed[CMP_IMMEDIATE_OFFSET] = 0xF8
FIXED = bytes(_fixed)


@dataclass(frozen=True)
class Result:
    data: bytes
    offset: int
    changed: bool


def _matches(data: bytes, pattern: bytes) -> list[int]:
    positions = []
    start = 0
    while (position := data.find(pattern, start)) != -1:
        positions.append(position)
        start = position + 1
    return positions


def patch_initializer(data: bytes) -> Result:
    """Return a new buffer; refuse missing, altered, or ambiguous contexts.

    Exactly one original OR repaired context is allowed. Reapplying to a
    repaired buffer is an explicit unchanged success. No partial writes occur.
    """
    data = bytes(data)
    original = _matches(data, ORIGINAL)
    repaired = _matches(data, FIXED)
    if len(original) + len(repaired) != 1:
        raise ValueError(
            "expected one verified 26A428 MPSNDArray initializer context; "
            f"found original={len(original)}, repaired={len(repaired)}"
        )
    if repaired:
        return Result(data, repaired[0], False)
    offset = original[0]
    return Result(data[:offset] + FIXED + data[offset + len(ORIGINAL):],
                  offset, True)


def checked_edits(read, address):
    """Validate native image identity and code before returning any edits."""
    header = read(address, 32)
    if len(header) != 32 or struct.unpack_from('<II', header) != (0xfeedfacf, 0x1000007):
        raise ValueError('MPS matrix initializer: expected native x86_64 Mach-O')
    count, size = struct.unpack_from('<II', header, 16)
    if count > 4096 or size > 16 * 1024 * 1024:
        raise ValueError('MPS matrix initializer: invalid load-command bounds')
    commands = read(address + 32, size)
    if len(commands) != size:
        raise ValueError('MPS matrix initializer: truncated load commands')
    at, identities, texts = 0, [], []
    for _ in range(count):
        if at + 8 > size:
            raise ValueError('MPS matrix initializer: truncated load command')
        cmd, length = struct.unpack_from('<II', commands, at)
        if length < 8 or at + length > size:
            raise ValueError('MPS matrix initializer: malformed load command')
        if cmd == 0x1b:
            if length != 24:
                raise ValueError('MPS matrix initializer: malformed UUID')
            identities.append(commands[at + 8:at + 24])
        if cmd == 0x19:
            if length < 72:
                raise ValueError('MPS matrix initializer: malformed segment')
            if commands[at + 8:at + 24].rstrip(b'\0') == b'__TEXT':
                texts.append(struct.unpack_from('<QQ', commands, at + 24))
        at += length
    if (at != size or identities != [UUID] or len(texts) != 1
            or texts[0][0] != address
            or CONTEXT_OFFSET + len(ORIGINAL) > texts[0][1]):
        raise ValueError('MPS matrix initializer: unsupported UUID/layout')
    location = address + CONTEXT_OFFSET
    before = read(location, len(ORIGINAL))
    result = patch_initializer(before)
    if not result.changed:
        return []
    return [(location, before, result.data,
             'initialize native MPSMatrix fallback for Intel device family 25')]


def plan_cache_family(directory):
    return plan_family(directory, IMAGE, checked_edits)
