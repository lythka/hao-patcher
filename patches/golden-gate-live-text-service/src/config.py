"""Remove only RestrictEvents' `media` process block from the image's OpenCore config.

OCLP (efi_builder/misc.py, setting disable_mediaanalysisd) writes NVRAM 4D1FDA02-...:revblock=media for Ivy Bridge
and other 3802-compiler GPUs, and RestrictEvents then returns EPERM for every spawn of mediaanalysisd. Photos'
Live Text asks mediaanalysisd (com.apple.mediaanalysisd.analysis) for the text, so it never answers. This edit
keeps every other revblock token, RestrictEvents itself and revpatch, and filters a revblock= boot argument (it
takes precedence over NVRAM). `media` alone becomes `none`; RestrictEvents' default when revblock is absent is
`auto`, which blocks nothing on this model, but an explicit value leaves no doubt. revblock stays in NVRAM/Delete
(OCLP's base config lists it), so OpenCore replaces a stale firmware `media` at the next boot. Refuses unless the
image's System carries the Live Text runtime the daemon needs on the HD 4000."""
import copy
import hashlib
import json
from pathlib import Path
import plistlib
import re
import sys

VENDOR = '4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102'
APPLE = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
RUNTIME = 'usr/lib/hao-live-text'
STARTUP = 'usr/lib/libhelloblur.dylib'
UMBRELLA = 'LiveText.dylib'


def text(value):
    if isinstance(value, bytes):
        value = value.decode('ascii')
    if not isinstance(value, str) or '\0' in value.rstrip('\0'):
        raise ValueError('expected a text NVRAM value')
    return value.rstrip('\0')


def without_media(value):
    tokens = text(value).split(',')
    # RestrictEvents matches substrings: an unknown token containing "media" would still block the daemon.
    if any('media' in item and item != 'media' for item in tokens):
        raise ValueError('ambiguous media substring in revblock: %r' % text(value))
    result = ','.join(item for item in tokens if item and item != 'media') or 'none'
    return result.encode('ascii') if isinstance(value, bytes) else result


def edit(config):
    result = copy.deepcopy(config)
    nvram = result.setdefault('NVRAM', {})
    vendor = nvram.setdefault('Add', {}).setdefault(VENDOR, {})
    vendor['revblock'] = without_media(vendor.get('revblock', 'auto'))
    delete = nvram.setdefault('Delete', {}).setdefault(VENDOR, [])
    if 'revblock' not in delete:
        delete.append('revblock')
    apple = nvram['Add'].get(APPLE, {})
    if 'boot-args' in apple:
        value = apple['boot-args']
        raw = text(value)
        matches = list(re.finditer(r'(?<!\S)revblock=(\S+)', raw))
        if len(matches) > 1:
            raise ValueError('more than one revblock boot argument')
        if matches:
            m = matches[0]
            raw = raw[:m.start(1)] + without_media(m.group(1)) + raw[m.end(1):]
            apple['boot-args'] = raw.encode('ascii') if isinstance(value, bytes) else raw
    return result


def installed_runtime(system):
    """The runtime's install root, if SYSTEM carries an intact Live Text runtime attached to libhelloblur."""
    system = Path(system).resolve()
    if system == Path('/'):
        raise ValueError('expected the mounted image System volume, not /')
    manifest = system/RUNTIME/'build.json'
    if not manifest.is_file():
        raise ValueError('MISSING: /%s (golden-gate-live-text-shared must be in the set)' % RUNTIME)
    record = json.loads(manifest.read_text())
    umbrella = '/' + RUNTIME + '/' + UMBRELLA
    if (record.get('unit') != 'tools/live-text' or record.get('compiler_mode') != 'shared'
            or record.get('install_root') != '/' + RUNTIME or record.get('load_paths') != [umbrella]):
        raise ValueError('/%s is not a shared-compiler Live Text runtime' % RUNTIME)
    if hashlib.sha256((system/umbrella.lstrip('/')).read_bytes()).hexdigest() != record['files'][UMBRELLA]:
        raise ValueError('%s differs from its manifest' % umbrella)
    if umbrella.encode() + b'\0' not in (system/STARTUP).read_bytes():
        raise ValueError('/%s does not load %s' % (STARTUP, umbrella))
    return record['install_root']


def apply(path, system):
    runtime = installed_runtime(system)
    path = Path(path)
    changed = edit(plistlib.loads(path.read_bytes()))
    path.write_bytes(plistlib.dumps(changed, sort_keys=False))
    print(json.dumps({'runtime': runtime, 'revblock': text(changed['NVRAM']['Add'][VENDOR]['revblock']),
                      'effective': 'next OpenCore boot'}))


if __name__ == '__main__':
    apply(*sys.argv[1:])
