"""x86 MPSNDArray initializer repair, located by code shape; byte buffers only.

The cache planner finds the context in the supplied image's __TEXT,__text,
verifies what it references, and reads the cache without changing it. The
caller owns application of returned edits and signing. Addresses are unslid.
No offset, UUID or RIP displacement of one build is stored: the context is the
code of -[MPSNDArrayMatrixMultiplication initWithDevice:sourceCount:] with its
RIP-relative fields masked, and the class and selector it uses are resolved
through the cache. Inspected builds: 26A428 (MPSNDArray 530AFC24, __TEXT+0x2ee43)
and 26B131 (26C99E92, __TEXT+0x2ee13). Anything else that does not match stops
with MISSING/unsupported.
"""
from dataclasses import dataclass
import re
import struct
from dyld_cache import plan_cache_family as plan_family

IMAGE = b'/System/Library/Frameworks/MetalPerformanceShaders.framework/Versions/A/Frameworks/MPSNDArray.framework/Versions/A/MPSNDArray'
CLASS = b'MPSMatrixMultiplication'
SELECTOR = (b'initWithDevice:transposeLeft:transposeRight:resultRows:'
            b'resultColumns:interiorColumns:alpha:beta:')

# Entire device-family decision and native MPSMatrix construction/store block,
# as inspected in 26A428 (reference bytes; the RIP fields below vary per build).
ORIGINAL = bytes.fromhex(
    "4a8b043b8b80c005000083c0e683f8f77744"
    "488b3d2c3af62be88b801f00488b35b0685d2d0f57c00f110424"
    "f20f100569f719004889c74c89f231c94531c04531c90f28c8"
    "ff157273f82b488b0d33c88b2f4889040b"
)
# Start of each 32-bit RIP-relative field; each instruction ends 4 bytes later.
FIELDS = dict(
    class_reference=21,   # movq MPSMatrixMultiplication classref(%rip),%rdi
    alloc_stub=26,        # callq objc_alloc stub
    selector_reference=33,  # movq SELECTOR selref(%rip),%rsi
    constant=48,          # movsd 1.0(%rip),%xmm0
    message_send=71,      # callq *objc_msgSend(%rip)
    ivar_offset=78,       # movq _mmul ivar offset(%rip),%rcx
)
# add eax,-26 / cmp eax,-9 / ja skip -> -25 / -8, same branch target.
ADD_IMMEDIATE_OFFSET = 12
CMP_IMMEDIATE_OFFSET = 15
_fixed = bytearray(ORIGINAL)
_fixed[ADD_IMMEDIATE_OFFSET] = 0xE7
_fixed[CMP_IMMEDIATE_OFFSET] = 0xF8
FIXED = bytes(_fixed)


def _pattern():
    masked = set(at for start in FIELDS.values() for at in range(start, start + 4))
    parts = []
    for at, byte in enumerate(ORIGINAL):
        if at in masked:
            parts.append(b'.')
        elif at in (ADD_IMMEDIATE_OFFSET, CMP_IMMEDIATE_OFFSET):
            parts.append(b'[' + re.escape(bytes([byte])) + re.escape(bytes([FIXED[at]])) + b']')
        else:
            parts.append(re.escape(bytes([byte])))
    return re.compile(b''.join(parts), re.S)


PATTERN = _pattern()


@dataclass(frozen=True)
class Result:
    data: bytes
    offset: int
    changed: bool


def target(context, address, field):
    """RIP-relative target of FIELDS[field] in a context located at address."""
    start = FIELDS[field]
    return address + start + 4 + struct.unpack_from('<i', context, start)[0]


def find_context(data: bytes):
    """Return (offset, repaired) of the one original or repaired context.

    RIP-relative fields are ignored; every other byte must match. Mixed
    immediates, duplicates and missing contexts refuse.
    """
    original, repaired = [], []
    for match in PATTERN.finditer(bytes(data)):
        pair = (match.group()[ADD_IMMEDIATE_OFFSET], match.group()[CMP_IMMEDIATE_OFFSET])
        if pair == (ORIGINAL[ADD_IMMEDIATE_OFFSET], ORIGINAL[CMP_IMMEDIATE_OFFSET]):
            original.append(match.start())
        elif pair == (FIXED[ADD_IMMEDIATE_OFFSET], FIXED[CMP_IMMEDIATE_OFFSET]):
            repaired.append(match.start())
        else:
            raise ValueError('MPS matrix initializer: mixed family immediates')
    if len(original) + len(repaired) != 1:
        raise ValueError(
            "MISSING/unsupported: expected one MPSNDArray initializer context; "
            f"found original={len(original)}, repaired={len(repaired)}"
        )
    return (original or repaired)[0], bool(repaired)


def patch_initializer(data: bytes) -> Result:
    """Return a new buffer; refuse missing, altered, or ambiguous contexts.

    Exactly one original OR repaired context is allowed. Reapplying to a
    repaired buffer is an explicit unchanged success. No partial writes occur.
    """
    data = bytes(data)
    offset, repaired = find_context(data)
    if repaired:
        return Result(data, offset, False)
    fixed = bytearray(data)
    fixed[offset + ADD_IMMEDIATE_OFFSET] = FIXED[ADD_IMMEDIATE_OFFSET]
    fixed[offset + CMP_IMMEDIATE_OFFSET] = FIXED[CMP_IMMEDIATE_OFFSET]
    return Result(bytes(fixed), offset, True)


def text_section(read, address):
    """Return (UUID, __text address, __text size) of a native x86_64 image."""
    header = read(address, 32)
    if len(header) != 32 or struct.unpack_from('<II', header) != (0xfeedfacf, 0x1000007):
        raise ValueError('MPS matrix initializer: expected native x86_64 Mach-O')
    count, size = struct.unpack_from('<II', header, 16)
    if count > 4096 or size > 16 * 1024 * 1024:
        raise ValueError('MPS matrix initializer: invalid load-command bounds')
    commands = read(address + 32, size)
    if len(commands) != size:
        raise ValueError('MPS matrix initializer: truncated load commands')
    at, identities, texts, code = 0, [], [], []
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
                sections = struct.unpack_from('<I', commands, at + 64)[0]
                if 72 + sections * 80 > length:
                    raise ValueError('MPS matrix initializer: malformed segment')
                for row in range(at + 72, at + 72 + sections * 80, 80):
                    if commands[row:row + 16].rstrip(b'\0') == b'__text':
                        code.append(struct.unpack_from('<QQ', commands, row + 32))
        at += length
    if (at != size or len(identities) != 1 or len(texts) != 1
            or texts[0][0] != address or len(code) != 1
            or not texts[0][0] <= code[0][0]
            or code[0][0] + code[0][1] > texts[0][0] + texts[0][1]):
        raise ValueError('MISSING/unsupported: MPSNDArray UUID/__TEXT/__text layout')
    return identities[0], code[0][0], code[0][1]


def checked_edits(read, address, pointer):
    """Validate native image, code shape and references before returning edits."""
    identity, start, size = text_section(read, address)
    try:
        offset, repaired = find_context(read(start, size))
    except ValueError as error:
        raise ValueError(f'{error} (MPSNDArray {identity.hex().upper()})') from None
    location = start + offset
    before = read(location, len(ORIGINAL))
    selector = pointer(target(before, location, 'selector_reference'))
    if read(selector, len(SELECTOR) + 1) != SELECTOR + b'\0':
        raise ValueError('MISSING/unsupported: MPSNDArray initializer selector differs')
    cls = pointer(target(before, location, 'class_reference'))
    ro = pointer(cls + 32) & 0x00007ffffffffff8
    if read(pointer(ro + 24), len(CLASS) + 1) != CLASS + b'\0':
        raise ValueError('MISSING/unsupported: MPSNDArray initializer class differs')
    if repaired:
        return []
    return [(location, before, patch_initializer(before).data,
             'initialize native MPSMatrix fallback for Intel device family 25')]


def plan_cache_family(directory):
    return plan_family(directory, IMAGE, checked_edits)
