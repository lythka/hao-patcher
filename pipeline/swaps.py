#!/usr/bin/python3 -I
"""swaps: no fix proven by a live swap on the laptop may be lost at the next image push.

  swaps.py status [--ledger FILE]      every swapped laptop path, newest swap first, with its resolution
  swaps.py check  [--ledger FILE]      exit 1 when a path's newest swap has no resolution (pipeline/build runs it)

hao-tools' laptop-swap records each put on this Mac (ledger: date, laptop path, sha256, mode, local source, who; the
bytes under objects/<sha256> beside it). HAO_SWAP_LEDGER or --ledger names it; the default is laptop-swap's. Only a
path's newest swap needs a resolution: earlier ones were iterations it replaced.

A swap is resolved when:
  - pipeline/live-swaps.tsv has a line "SHA256<TAB>LAPTOP PATH<TAB>RESOLUTION" for it, RESOLUTION being
    "row NAME" or "recipe NAME" (the fix now ships in images that way) or "dropped: WHY" (not wanted), or
  - its path is under /usr/lib/hao/ and the hao_runtime row's payload holds the same bytes at root/<path> (hao-runtime
    fixes ship in its payload).
"""
import argparse
import hashlib
import os
from pathlib import Path
import runpy
import sys

TOP = Path(__file__).resolve().parents[1]
LEDGER = Path(os.environ.get('HAO_SWAP_LEDGER') or Path.home() / 'mac-image/store/laptop-swaps/ledger.tsv')
RESOLUTIONS = TOP / 'pipeline' / 'live-swaps.tsv'
RUNTIME_ROWS = (TOP / 'pipeline/inputs/golden/26B131', 'hao_runtime')
KINDS = ('row ', 'recipe ', 'dropped: ')


def ledger(path=LEDGER):
    """[{date, path, sha256, mode, source, who}] in file order (oldest first); [] when there is no ledger."""
    out = []
    if not Path(path).is_file():
        return out
    for line in Path(path).read_text().splitlines():
        f = line.split('\t')
        if len(f) >= 3 and len(f[2]) == 64:
            out.append(dict(zip(('date', 'path', 'sha256', 'mode', 'source', 'who'), f + [''] * (6 - len(f)))))
    return out


def resolutions(path=RESOLUTIONS):
    """{(sha256, laptop path): resolution} from the tracked file; malformed lines raise."""
    out = {}
    if not Path(path).is_file():
        return out
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        f = line.split('\t')
        if len(f) != 3 or len(f[0]) != 64 or not f[1].startswith('/') or not f[2].startswith(KINDS):
            raise ValueError('%s:%d: want SHA256<TAB>/LAPTOP/PATH<TAB>row NAME | recipe NAME | dropped: WHY' % (path, n))
        out[(f[0], f[1])] = f[2]
    return out


def _sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def runtime_payload(rows=RUNTIME_ROWS):
    """The hao_runtime row's payload folder, or None when it does not resolve."""
    try:
        return Path(runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))['path'](str(rows[0]), rows[1]))
    except Exception:
        return None


def status(entries, resolved, payload=None):
    """[{path, sha256, date, newest, resolution}] for each path's newest swap (newest first), plus the older swaps of
    that path counted. resolution None means unresolved."""
    newest, count = {}, {}
    for e in entries:
        newest[e['path']] = e
        count[e['path']] = count.get(e['path'], 0) + 1
    out = []
    for p, e in newest.items():
        r = resolved.get((e['sha256'], p))
        if r is None and payload is not None and p.startswith('/usr/lib/hao/'):
            f = payload / 'root' / p.lstrip('/')
            if f.is_file() and _sha(f) == e['sha256']:
                r = 'row hao_runtime (same bytes in its payload)'
        out.append(dict(e, swaps=count[p], resolution=r))
    return sorted(out, key=lambda x: x['date'], reverse=True)


def unresolved(ledger_path=LEDGER, resolutions_path=RESOLUTIONS, payload=None):
    rows = status(ledger(ledger_path), resolutions(resolutions_path),
                  payload if payload is not None else runtime_payload())
    return [r for r in rows if r['resolution'] is None]


def main(argv):
    ap = argparse.ArgumentParser(prog='swaps.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['status', 'check'])
    ap.add_argument('--ledger', type=Path, default=LEDGER)
    a = ap.parse_args(argv)
    rows = status(ledger(a.ledger), resolutions(), runtime_payload())
    if a.cmd == 'status':
        if not rows:
            print('swaps: no recorded swaps in %s' % a.ledger)
        for r in rows:
            print('%s  %-10s %s  %s%s' % (r['date'], 'OPEN' if r['resolution'] is None else 'resolved', r['sha256'][:12],
                                         r['path'], '  -> ' + r['resolution'] if r['resolution'] else ''))
        return 0
    bad = [r for r in rows if r['resolution'] is None]
    for r in bad:
        print('swaps: %s (%s, swapped %s from %s) has no resolution: the fix exists only on the laptop'
              % (r['path'], r['sha256'][:12], r['date'], r['source']), file=sys.stderr)
    if bad:
        print('swaps: put each into a recipe or row, or mark it, in pipeline/live-swaps.tsv '
              '(SHA256<TAB>PATH<TAB>row NAME | recipe NAME | dropped: WHY)', file=sys.stderr)
        return 1
    print('swaps: every swapped laptop path is resolved (%d)' % len(rows))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
