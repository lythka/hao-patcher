"""Select donor roots before extracting the installer payload.

A target root wins only with positive evidence of native Intel code. Roots paired
with Tahoe kexts always stay donors. Resource-only roots remain candidates until
installer metadata/files can be compared; absence of code is not proof of parity.
"""
from pathlib import Path
import os
import struct


def x86(path):
    try:
        with open(path, 'rb') as stream:
            head = stream.read(4096)
    except OSError:
        return None
    if len(head) < 8:
        return None
    magic = struct.unpack_from('>I', head)[0]
    if magic in (0xcafebabe, 0xcafebabf):
        count = struct.unpack_from('>I', head, 4)[0]
        stride = 32 if magic == 0xcafebabf else 20
        if count > 64 or len(head) < 8+count*stride:
            raise ValueError('MISSING/unsupported: malformed fat Mach-O: '+str(path))
        return any(struct.unpack_from('>I', head, 8+i*stride)[0] == 0x1000007 for i in range(count))
    if struct.unpack_from('<I', head)[0] in (0xfeedfacf, 0xfeedface):
        return struct.unpack_from('<I', head, 4)[0] == 0x1000007
    return None


def target_supplied(requirements, target, cache_images=()):
    """Return positive target choices; unlisted roots must still be extracted."""
    target = Path(target)
    keep = set(requirements.get('keep_donor_roots', ()))
    choices = []
    for root in requirements['system_roots']:
        if root in keep:
            continue
        path = target/root
        if not os.path.lexists(path):
            continue
        files = [path] if not path.is_dir() else [Path(d)/f for d, _, fs in os.walk(path) for f in fs]
        kinds = [x86(f) for f in files if not f.is_symlink()]
        machos = [kind for kind in kinds if kind is not None]
        if machos and all(machos):
            choices.append((root, 'target', 'target supplies x86_64 code on disk'))
        elif not machos and any(name.lstrip('/').startswith(root.rstrip('/')+'/') for name in cache_images):
            choices.append((root, 'target', 'target supplies x86_64 code in its shared cache'))
    return choices
