"""edit_dyld.py IN OUT: dyld4::SyscallDelegate::internalInstall() answers true in the x86_64 slice (edits/dyld.json).

The function is `push rbp; mov rbp,rsp; mov edi,0x10; call _csr_check; test eax,eax; sete al; pop rbp; ret`; the call
becomes `xor eax,eax; nop dword [rax]`. Exactly one match in the x86_64 slice, or it refuses; an already edited file is
reported as such. Standard library only (runs in the builder VM's Python)."""
import struct
import sys

FIND = bytes.fromhex('554889e5bf10000000e8')          # then rel32, then:
TAIL = bytes.fromhex('85c00f94c05dc3')
NEW = bytes.fromhex('31c00f1f00')
DONE = bytes.fromhex('554889e5bf1000000031c00f1f0085c00f94c05dc3')
CPU_X86_64 = 0x01000007


def x86_64_slice(data):
    magic = struct.unpack('>I', data[:4])[0]
    if magic in (0xcafebabe, 0xcafebabf):
        wide = magic == 0xcafebabf
        n = struct.unpack('>I', data[4:8])[0]
        size = 32 if wide else 20
        for i in range(n):
            e = data[8 + size * i:8 + size * (i + 1)]
            cpu = struct.unpack('>I', e[:4])[0]
            off, sz = struct.unpack('>QQ', e[8:24]) if wide else struct.unpack('>II', e[8:16])
            if cpu == CPU_X86_64:
                return off, sz
        raise SystemExit('edit_dyld: no x86_64 slice')
    if data[:4] == b'\xcf\xfa\xed\xfe' and struct.unpack('<I', data[4:8])[0] == CPU_X86_64:
        return 0, len(data)
    raise SystemExit('edit_dyld: not an x86_64 Mach-O')


def edit(data):
    off, size = x86_64_slice(data)
    body = data[off:off + size]
    if DONE in body:
        raise SystemExit('edit_dyld: already edited')
    sites = []
    i = body.find(FIND)
    while i >= 0:
        if body[i + 14:i + 21] == TAIL:
            sites.append(i)
        i = body.find(FIND, i + 1)
    if len(sites) != 1:
        raise SystemExit('edit_dyld: %d matches of internalInstall in the x86_64 slice, want 1' % len(sites))
    at = off + sites[0] + 9
    return data[:at] + NEW + data[at + 5:], at


if __name__ == '__main__':
    src, dst = sys.argv[1:3]
    data = open(src, 'rb').read()
    out, at = edit(data)
    open(dst, 'wb').write(out)
    print('edit_dyld: internalInstall call at file offset 0x%x -> xor eax,eax' % at)
