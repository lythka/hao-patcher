"""Repair the Intel backend's active-mask emission for split SIMD16.

The first SIMD8 compare can change the flag read by the second compare.
Use unsigned >= 0, which stays true regardless of the intervening flag write.
The emitted source and immediate have unsigned-word type (EU type 2).
"""
import struct

SYMBOL = 'Convert_ACTIVE_CHANNEL_MASK'
RELATIVE = 'System/Library/Extensions/AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS/iCBE.dylib'


def function_bytes(data):
    if data[:4] != bytes.fromhex('cffaedfe'):
        raise ValueError('expected a thin 64-bit Mach-O compiler')
    ncmds = struct.unpack_from('<I', data, 16)[0]
    position = 32
    segments, symtab = [], None
    for _ in range(ncmds):
        command, size = struct.unpack_from('<II', data, position)
        if command == 0x19:
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, position + 24)
            segments.append((vmaddr, fileoff, filesize))
        elif command == 2:
            symtab = struct.unpack_from('<IIII', data, position + 8)
        position += size
    if symtab is None:
        raise ValueError('compiler has no symbol table')
    symoff, count, stroff, strsize = symtab
    symbols = []
    for index in range(count):
        string, kind, section, description, address = struct.unpack_from('<IBBHQ', data, symoff + index * 16)
        if kind & 0xe == 0xe and string < strsize:
            start = stroff + string
            name = data[start:data.index(b'\0', start)].decode()
            symbols.append((address, name))
    matches = [(a, n) for a, n in symbols if SYMBOL in n]
    if len(matches) != 1:
        raise ValueError('expected one active-mask emitter')
    address, name = matches[0]
    end = min(a for a, n in symbols if a > address)
    for vmaddr, fileoff, filesize in segments:
        if vmaddr <= address < vmaddr + filesize:
            offset = fileoff + address - vmaddr
            return offset, data[offset:offset + end - address]
    raise ValueError('active-mask emitter is outside file-backed segments')


def repair(data):
    offset, body = function_bytes(data)
    # or eax, NE-condition; store instruction word. Change NE to unsigned GE.
    condition = bytes.fromhex('0d0000000241894630')
    # SetSrcImmediate(source=1, value=1, type=unsigned word, modifier=0).
    immediate = bytes.fromhex('be01000000ba01000000b9020000004531c0')
    if body.count(condition) != 1 or body.count(immediate) != 1:
        raise ValueError('unrecognized active-mask emission recipe')
    changes = [(offset + body.index(condition) + 4, 4),
               (offset + body.index(immediate) + 6, 0)]
    result = bytearray(data)
    for position, value in changes:
        result[position] = value
    return bytes(result), dict(function=SYMBOL, changes=[dict(offset=p, before=data[p], after=v) for p, v in changes])
