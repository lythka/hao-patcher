"""members.py KC: print the bundle identifiers of an x86_64 kernel collection (MH_FILESET entries), sorted.
kmutil writes BootKernelExtensions.kc.elides as these members minus com.apple.kernel."""
import struct, sys

b = open(sys.argv[1], 'rb').read()
magic, cpu, _, ftype, n, _ = struct.unpack_from('<IIIIII', b)
if (magic, cpu & 0xffffff, ftype) != (0xfeedfacf, 7, 12):
    sys.exit('%s: not an x86_64 MH_FILESET' % sys.argv[1])
off, ids = 32, []
for _ in range(n):
    cmd, size = struct.unpack_from('<II', b, off)
    if cmd == 0x80000035:   # LC_FILESET_ENTRY: vmaddr, fileoff, entry_id (string offset in the command), reserved
        name = struct.unpack_from('<I', b, off + 24)[0]
        ids.append(b[off + name:off + size].split(b'\0', 1)[0].decode())
    off += size
print('\n'.join(sorted(ids)))
