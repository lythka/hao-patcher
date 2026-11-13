"""Step 9a laptop run helpers; run ON the laptop (as root) with the Python run-9a.sh ships there.

  hao9a.py overlay OUT          make the test overlay from this Mac's own files: a new System file, a replaced
                                System file of no consequence, a new Data volume file; OUT/expect.json has their
                                sha256 before and after
  hao9a.py verify PHASE OUT     PHASE before | applied | reverted: check the running system against OUT/expect.json
                                and hao's newest live record; prints PASS/FAIL/NOTE lines, exit 1 on any FAIL

Only reads, except `overlay`, which writes OUT. It never runs hao patch itself.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import livepatch as LP   # noqa: E402

NEW = 'usr/share/hao/9a-probe.txt'
DATA = 'Library/Application Support/hao-9a/data-probe.txt'
REPLACE = ('usr/share/misc/ascii', 'usr/share/misc/operator', 'usr/share/misc/units.lib')   # first that exists
STATE = Path('/private/var/db/hao')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def overlay(out):
    out = Path(out)
    replaced = next((p for p in REPLACE if Path('/' + p).is_file() and not Path('/' + p).is_symlink()
                     and Path('/' + p).stat().st_size < 1 << 20), None)
    if replaced is None:
        sys.exit('none of %s is a regular file here' % ', '.join(REPLACE))
    st = os.lstat('/' + replaced)
    old = Path('/' + replaced).read_bytes()
    files = {NEW: (b'hao 9a probe: a new file on the System volume\n', '0644', 'root:wheel', []),
             replaced: (old + b'# hao 9a probe: this copy replaced the original\n', '%04o' % (st.st_mode & 0o7777),
                        '%d:%d' % (st.st_uid, st.st_gid), LP.flag_names(st.st_flags)),
             DATA: (b'hao 9a probe: a new file on the Data volume\n', '0644', 'root:wheel', [])}
    m = {'format': 1, 'pack': 'hao-9a', 'phase': 'live-capable', 'files': {}}
    expect = {}
    for path, (data, mode, owner, flags) in files.items():
        (out / 'files' / path).parent.mkdir(parents=True, exist_ok=True)
        (out / 'files' / path).write_bytes(data)
        m['files'][path] = {'sha256': sha(data), 'mode': mode, 'owner': owner, 'flags': flags}
        expect[path] = {'before': sha(old) if path == replaced else None, 'after': sha(data), 'mode': mode,
                        'owner': owner, 'flags': flags}
    (out / 'manifest.json').write_text(json.dumps(m, indent=1) + '\n')
    (out / 'expect.json').write_text(json.dumps(expect, indent=1) + '\n')
    for path, e in expect.items():
        print('%-52s before %s after %s' % (path, (e['before'] or 'none')[:16], e['after'][:16]))


def verify(phase, out):
    expect = json.loads((Path(out) / 'expect.json').read_text())
    fails = []

    def check(ok, text):
        print('%s %s' % ('PASS' if ok else 'FAIL', text))
        if not ok:
            fails.append(text)

    root = plistlib.loads(subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', '/'], capture_output=True,
                                         check=True).stdout)
    booted = root.get('APFSSnapshotName')
    print('NOTE booted snapshot %s (%s), sealed %s' % (booted, root.get('APFSSnapshotUUID'), root.get('Sealed')))
    recs = [r for r in LP.records(STATE) if r.get('backend') == 'macos-live']
    rec = recs[-1] if recs else None
    for path, e in expect.items():
        f = Path('/' + path)
        have = LP.sha256_file(f) if f.is_file() else None
        want = {'before': e['before'], 'applied': e['after'], 'reverted': e['before']}[phase]
        check(have == want, '%s is %s (want %s)' % (path, (have or 'absent')[:16], (want or 'absent')[:16]))
        if have is not None and phase == 'applied':
            st = os.lstat(f)
            a = LP.Attributes(Path('/'))
            w = a.wanted(e)
            check((st.st_mode & 0o7777, st.st_uid, st.st_gid, st.st_flags & (LP.SYSTEM_FLAGS | 0xffff))
                  == (w['mode'], w['uid'], w['gid'], w['flags']),
                  '%s has mode %04o owner %d:%d flags %s as the manifest says' % (
                      path, st.st_mode & 0o7777, st.st_uid, st.st_gid, LP.flag_names(st.st_flags)))
    if phase == 'before':
        check(rec is None or rec['state'] != 'applied', 'no live hao patch is applied')
    elif rec is None:
        check(False, 'hao has a live record')
    else:
        print('NOTE record %s state %s: booted before %s, pre %s, patch snapshot %s' % (
            rec['id'], rec['state'], (rec.get('snapshot_booted') or {}).get('name'),
            (rec.get('snapshot_pre') or {}).get('name'), rec.get('snapshot')))
        if phase == 'applied':
            check(rec['state'] == 'applied', 'record %s is applied' % rec['id'])
            check(booted == rec.get('snapshot'), 'running the patch\'s snapshot %s' % rec.get('snapshot'))
        else:
            check(rec['state'] == 'reverted', 'record %s is reverted' % rec['id'])
            check(booted == (rec.get('reverted_to') or {}).get('name'), 'running the snapshot revert chose (%s, %s)' % (
                (rec.get('reverted_to') or {}).get('name'), (rec.get('reverted_to') or {}).get('kind')))
            check(booted == (rec.get('snapshot_booted') or {}).get('name'),
                  'that is the snapshot booted before the patch')
        snaps = plistlib.loads(subprocess.run(['/usr/sbin/diskutil', 'apfs', 'listSnapshots', '-plist',
                                               rec['system_device']], capture_output=True, check=True).stdout)
        names = [s.get('SnapshotName') for s in snaps.get('Snapshots', [])]
        for kind in ('snapshot_booted', 'snapshot_pre'):
            name = (rec.get(kind) or {}).get('name')
            print('NOTE %s %s %s this boot' % (kind, name, 'SURVIVED' if name in names else 'is GONE after'))
        print('NOTE snapshots now: %s' % ', '.join(names))
    print('%s: %d failed' % (phase, len(fails)))
    return 1 if fails else 0


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'overlay':
        overlay(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == 'verify' and sys.argv[2] in ('before', 'applied', 'reverted'):
        sys.exit(verify(sys.argv[2], sys.argv[3]))
    else:
        sys.exit(__doc__)
