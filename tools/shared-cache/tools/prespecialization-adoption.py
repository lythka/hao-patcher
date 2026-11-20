#!/usr/bin/env python3.14
"""Is the installed cache's generated Swift prespecialization table the one the Swift runtime uses?

  prespecialization-adoption.py expect CACHE   the unslid addresses a running system must show (JSON)
  prespecialization-adoption.py judge EXPECT LOG

On the target, LOG is the stderr of any Swift program run with SWIFT_DEBUG_ENABLE_LIB_PRESPECIALIZED_LOGGING=1, e.g.
  SWIFT_DEBUG_ENABLE_LIB_PRESPECIALIZED_LOGGING=1 /usr/bin/swcutil --help 2>&1 | grep Prespecializations

The slide is the logged data pointer minus its unslid address. PASS needs the logged pointer-keyed map, less that
slide, to be the cache's own map (the one its build wrote), and at least one lookup that found an entry. The build's
audit proves the table's contents; this proves the running Swift runtime reads that table.
"""
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

DATA = re.compile(r'Got dataPtr (0x[0-9a-f]+) from _dyld_get_swift_prespecialized_data')
MAP = re.compile(r'pointerKeyedMetadataMap=(0x[0-9a-f]+)')
LOOKUP = re.compile(r'Looking up description 0x[0-9a-f]+ in dyld table, found (0x[0-9a-f]+)')


def expect(cache):
    from dyld_rebuild.image_indexes import swift_roots
    from dyld_rebuild.loader_generation import LoaderFamily
    from dyld_rebuild.objc_emit import FamilyReader
    family = LoaderFamily(cache)
    base, roots = swift_roots(family)
    data = base + roots['prespecialization']
    major, minor = struct.unpack('<II', family.read(data, 8))
    table = FamilyReader(family).ptr(data + 24)        # LibPrespecializedData.pointerKeyedMetadataMap
    if not table:
        raise SystemExit('prespecialization-adoption: the cache has no pointer-keyed metadata map')
    return dict(schema=1, cache_uuid=family.main.data[88:104].hex(), data=hex(data), map=hex(table),
                version='%d.%d' % (major, minor))


def judge(want, log):
    data, maps = DATA.findall(log), MAP.findall(log)
    found = [f for f in LOOKUP.findall(log) if int(f, 16)]
    looked = len(LOOKUP.findall(log))
    reasons = []
    if not data or not maps:
        reasons.append('no prespecialization data in the log: SWIFT_DEBUG_ENABLE_LIB_PRESPECIALIZED_LOGGING unset, or '
                       'the Swift runtime did not load the cache data')
    else:
        slide = int(data[0], 16) - int(want['data'], 16)
        got = int(maps[0], 16) - slide
        if got != int(want['map'], 16):
            reasons.append('the runtime reads map %s (unslid %#x), not the cache build\'s %s'
                           % (maps[0], got, want['map']))
    if not found:
        reasons.append('no lookup found an entry (%d looked up)' % looked)
    return dict(result='FAIL' if reasons else 'PASS', lookups=looked, found=len(found), reasons=reasons,
                expected=want)


def main(argv):
    if len(argv) == 2 and argv[0] == 'expect':
        print(json.dumps(expect(argv[1]), indent=1))
        return 0
    if len(argv) == 3 and argv[0] == 'judge':
        r = judge(json.loads(Path(argv[1]).read_text()), Path(argv[2]).read_text(errors='replace'))
        print(json.dumps(r, indent=1))
        return 0 if r['result'] == 'PASS' else 1
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
