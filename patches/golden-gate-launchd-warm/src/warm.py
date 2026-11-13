"""Start Apple LaunchDaemons that boot waits on with launchd, at Interactive priority, instead of on demand.

  warm.py SYSTEM OUT_DIR

For every job in JOBS, writes OUT_DIR/<its plist path> from SYSTEM/<its plist path>, and writes OUT_DIR/<CACHE> from
SYSTEM/<CACHE> (launchd's job cache, whose LaunchDaemons entry for each job carries the same keys), each with that
job's KEYS set, in the file's own format and key order, as tools/edit-engine's plist.set writes them. Every other key
stays Apple's. A job whose Label is not the one listed stops the build."""
import plistlib
import sys
from pathlib import Path

CACHE = 'System/Library/xpc/launchd.plist'
SETUP_DONE = '/private/var/db/.AppleSetupDone'
# (label, job plist, keys set in order)
JOBS = (
    # kernelmanagerd asks it for the load report of every kext OpenCore injects, while the kernel's requests wait.
    ('com.apple.kernelmanager_helper', 'System/Library/LaunchDaemons/com.apple.kernelmanager_helper.plist',
     (('RunAtLoad', True), ('POSIXSpawnType', 'Interactive'))),
    # loginwindow waits for it before Setup Assistant's first pane. Started at boot only while Setup is not done
    # (Apple's own idiom, as cryptexd's and configureLocalKDC's KeepAlive PathState); on demand after that, as today.
    ('com.apple.mbsystemadministration', 'System/Library/LaunchDaemons/com.apple.mbsystemadministration.plist',
     (('KeepAlive', {'PathState': {SETUP_DONE: False}}), ('POSIXSpawnType', 'Interactive'))),
)


def _fmt(data):
    return plistlib.FMT_BINARY if data.startswith(b'bplist00') else plistlib.FMT_XML


def _set(job, label, keys):
    if job.get('Label') != label:
        raise SystemExit('launchd-warm: found %r, not %s' % (job.get('Label'), label))
    for key, value in keys:
        job[key] = value


def warm_job(data, label, keys):
    """DATA (a job plist) with KEYS set, after checking its Label."""
    root = plistlib.loads(data)
    _set(root, label, keys)
    return plistlib.dumps(root, fmt=_fmt(data), sort_keys=False)


def warm_cache(data, jobs=JOBS):
    """DATA (launchd's job cache) with every job's KEYS set in its LaunchDaemons entry, in JOBS order."""
    root = plistlib.loads(data)
    for label, path, keys in jobs:
        _set(root['LaunchDaemons']['/' + path], label, keys)
    return plistlib.dumps(root, fmt=_fmt(data), sort_keys=False)


def main(argv):
    system, out = (Path(a) for a in argv)
    for label, path, keys in JOBS:
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(warm_job((system / path).read_bytes(), label, keys))
    (out / CACHE).parent.mkdir(parents=True, exist_ok=True)
    (out / CACHE).write_bytes(warm_cache((system / CACHE).read_bytes()))


if __name__ == '__main__':
    main(sys.argv[1:])
