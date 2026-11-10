"""Generate fixed path + full-file digest mappings from a hao payload manifest.

The mapping is compiled into the observer shim. It cannot be extended by a
user-writable registry, environment variable, identifier, or filename alone.
"""
import hashlib
import json
import plistlib
from pathlib import Path, PurePosixPath
import struct


def architecture(data):
    if data[:4] == b'\xcf\xfa\xed\xfe':
        arches = [struct.unpack_from('<II', data, 4)]
    elif data[:4] == b'\xca\xfe\xba\xbe':
        count, = struct.unpack_from('>I', data, 4)
        if count > (len(data) - 8) // 20:
            raise ValueError('truncated fat header')
        arches = [struct.unpack_from('>II', data, 8 + i * 20) for i in range(count)]
    else:
        raise ValueError('unsupported Mach-O header')
    # Prefer the ordinary arm64e ABI over arm64e.x1, as hao does.
    if any(cpu == 0x100000c and sub & 0xffffff == 2 for cpu, sub in arches):
        return 'arm64e'
    if any(cpu == 0x100000c and sub & 0xffffff == 0 for cpu, sub in arches):
        return 'arm64'
    raise ValueError('no supported original ARM slice')


def rows(manifest, original_root, installed_prefix='/'):
    result = []
    for item in manifest['files']:
        path = PurePosixPath(item['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('non-relative payload path')
        source = (Path(original_root) / path).read_bytes()
        if hashlib.sha256(source).hexdigest() != item['source_sha256']:
            raise ValueError(f'original source mismatch: {path}')
        digest = bytes.fromhex(item['result_sha256'])
        if len(digest) != 32:
            raise ValueError('wrong digest size')
        executable = str(Path(installed_prefix) / path)
        aliases = [executable]
        # Bundle identities are requested at both the bundle and executable URL.
        if '/Contents/MacOS/' in executable:
            bundle, leaf = str(path).rsplit('/Contents/MacOS/', 1)
            info = Path(original_root) / bundle / 'Contents/Info.plist'
            if info.is_file() and plistlib.loads(info.read_bytes()).get('CFBundleExecutable') == leaf:
                aliases.append(str(Path(installed_prefix) / bundle))
        for alias in aliases:
            result.append((alias, executable, architecture(source), digest))
    return result


def header(entries):
    if not entries:
        raise ValueError('empty identity map')
    lines = ['static const struct identity identities[] = {']
    seen = set()
    for path, executable, arch, digest in entries:
        if path in seen:
            raise ValueError(f'duplicate identity path: {path}')
        seen.add(path)
        values = ', '.join(json.dumps(x) for x in (path, executable, arch))
        lines.append('    {' + values + ', {' + ','.join(f'0x{x:02x}' for x in digest) + '}},')
    return '\n'.join(lines + ['};', ''])
