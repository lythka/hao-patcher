"""Remove build-path debug records and derive helper UUIDs from unsigned bytes."""
import hashlib
from pathlib import Path
import struct
import subprocess

from .exports import require


def sign(binary, identifier):
    binary = Path(binary)
    subprocess.run(['codesign', '--remove-signature', str(binary)], check=True)
    subprocess.run(['strip', '-S', '-x', str(binary)], check=True)
    raw = bytearray(binary.read_bytes())
    if raw[:4] == b'\xca\xfe\xba\xbe':
        count = struct.unpack_from('>I', raw, 4)[0]
        slices = [struct.unpack_from('>II', raw, 8+20*i+8) for i in range(count)]
    else:
        slices = [(0, len(raw))]
    for start, length in slices:
        require(start+length <= len(raw) and struct.unpack_from('<I', raw, start)[0] == 0xfeedfacf,
                'unsupported helper Mach-O slice')
        pos = start+32
        uuid = None
        for _ in range(struct.unpack_from('<I', raw, start+16)[0]):
            kind, size = struct.unpack_from('<II', raw, pos)
            require(size >= 8 and pos+size <= start+length, 'malformed helper command')
            if kind == 0x1b:
                require(size == 24 and uuid is None, 'invalid helper UUID command')
                uuid = pos+8
            pos += size
        require(uuid is not None, 'helper requires LC_UUID')
        raw[uuid:uuid+16] = bytes(16)
        identity = bytearray(hashlib.sha256(raw[start:start+length]).digest()[:16])
        identity[6] = (identity[6] & 15) | 0x50
        identity[8] = (identity[8] & 63) | 0x80
        raw[uuid:uuid+16] = identity
    binary.write_bytes(raw)
    subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none',
                    '--identifier', identifier, str(binary)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(binary)], check=True)
