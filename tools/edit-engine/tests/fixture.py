"""Synthetic x86_64 Mach-O dylibs for the tests: one __TEXT segment with __text, a symbol table, an id, padding."""
import struct

X86_64, ARM64 = 0x1000007, 0x100000c


def dylib(size=0x3000, text_at=0x1000, text_size=0x1000, symbols=None, code=None, padding_dirty=False,
          ident=b'/usr/lib/libfixture.dylib'):
    """A thin MH_DYLIB. SYMBOLS {name: offset}; CODE {offset: bytes} written into the file."""
    symbols = symbols or {}
    seg = struct.pack('<II16sQQQQIIII', 0x19, 72 + 80, b'__TEXT', 0, size, 0, size, 5, 5, 1, 0)
    sect = struct.pack('<16s16sQQIIIIIIII', b'__text', b'__TEXT', text_at, text_size, text_at, 4, 0, 0,
                       0x80000400, 0, 0, 0)
    name = ident + b'\0'
    idsize = (24 + len(name) + 7) & ~7
    idcmd = struct.pack('<6I', 0xd, idsize, 24, 2, 0x10000, 0x10000) + name
    idcmd += bytes(idsize - len(idcmd))
    strtab = b'\0' + b''.join(n.encode() + b'\0' for n in symbols)
    symoff = text_at + text_size
    stroff = symoff + 16 * len(symbols)
    if stroff + len(strtab) > size:
        raise ValueError('fixture too small')
    symtab = struct.pack('<6I', 0x2, 24, symoff, len(symbols), stroff, len(strtab))
    cmds = seg + sect + idcmd + symtab
    out = bytearray(size)
    struct.pack_into('<8I', out, 0, 0xfeedfacf, X86_64, 3, 6, 3, len(cmds), 0x100085, 0)
    out[32:32 + len(cmds)] = cmds
    if padding_dirty:
        out[32 + len(cmds) + 8] = 0x41
    strx = 1
    for i, (n, off) in enumerate(symbols.items()):
        struct.pack_into('<IBBHQ', out, symoff + 16 * i, strx, 0x0f, 1, 0, off)
        strx += len(n) + 1
    out[stroff:stroff + len(strtab)] = strtab
    for off, data in (code or {}).items():
        out[off:off + len(data)] = data
    return bytes(out)


def universal(x86, arm=b'\xcf\xfa\xed\xfe' + bytes(0x1ffc), align=12):
    """A universal file holding ARM (first) and X86 slices, each page-aligned."""
    step = 1 << align
    arm_at = step
    x86_at = arm_at + ((len(arm) + step - 1) // step) * step
    out = bytearray(x86_at + len(x86))
    struct.pack_into('>II', out, 0, 0xcafebabe, 2)
    struct.pack_into('>5I', out, 8, ARM64, 0, arm_at, len(arm), align)
    struct.pack_into('>5I', out, 28, X86_64, 3, x86_at, len(x86), align)
    out[arm_at:arm_at + len(arm)] = arm
    out[x86_at:] = x86
    return bytes(out), x86_at
