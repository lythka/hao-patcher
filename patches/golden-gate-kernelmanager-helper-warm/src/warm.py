"""Start Apple's kernelmanager_helper with launchd, at Interactive priority, instead of on demand at background priority.

  warm.py JOB_PLIST CACHE_PLIST OUT_DIR

Writes OUT_DIR/job.plist and OUT_DIR/cache.plist: the job plist and launchd's job cache (System/Library/xpc/
launchd.plist, whose LaunchDaemons entry for the job carries the same keys) with RunAtLoad true and POSIXSpawnType
Interactive, each in the file's own format and key order, as tools/edit-engine's plist.set writes them. Every other
key of the job stays Apple's. A job whose Label is not the helper's stops the build."""
import plistlib
import sys
from pathlib import Path

LABEL = 'com.apple.kernelmanager_helper'
JOB = 'System/Library/LaunchDaemons/com.apple.kernelmanager_helper.plist'
CACHE = 'System/Library/xpc/launchd.plist'
KEYS = (('RunAtLoad', True), ('POSIXSpawnType', 'Interactive'))


def warm(data):
    """DATA (the job plist, or launchd's cache) with the helper's job started at load at Interactive priority."""
    fmt = plistlib.FMT_BINARY if data.startswith(b'bplist00') else plistlib.FMT_XML
    root = plistlib.loads(data)
    job = root['LaunchDaemons']['/' + JOB] if 'LaunchDaemons' in root else root
    if job.get('Label') != LABEL:
        raise SystemExit('kernelmanager-helper-warm: found %r, not %s' % (job.get('Label'), LABEL))
    for key, value in KEYS:
        job[key] = value
    return plistlib.dumps(root, fmt=fmt, sort_keys=False)


def main(argv):
    job, cache, out = (Path(a) for a in argv)
    (out / 'job.plist').write_bytes(warm(job.read_bytes()))
    (out / 'cache.plist').write_bytes(warm(cache.read_bytes()))


if __name__ == '__main__':
    main(sys.argv[1:])
