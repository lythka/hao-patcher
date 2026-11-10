"""Provenance for the SIFT probe and diagnostic library artifacts."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent


def digest(path):
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def record(output, extra=()):
    sources = sorted(p for p in HERE.iterdir() if p.is_file())
    sources += [UNIT / 'lib/mtlb.py', UNIT / 'lib/runtime/logical32_helpers.py']
    sources += [UNIT.parent / 'shaders/src' / n for n in ('replay.m', 'memo.h', 'capture-replay.m')]
    sources += list(extra)
    git = lambda *args: subprocess.check_output(['git', '-C', UNIT, *args], text=True).strip()
    value = dict(unit='cache/metallib', revision=int(re.search(
        r'^## Revisions\n+- r(\d+)', (UNIT / 'README.md').read_text(), re.M)[1]),
        commit=git('rev-parse', 'HEAD'), dirty=bool(git('status', '--porcelain', '--', str(UNIT))),
        date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        inputs=[digest(p) for p in sources],
        outputs=[digest(p) for p in sorted(output.iterdir()) if p.is_file() and p.name != 'build.json'],
        toolchain=subprocess.check_output(['xcrun', 'clang', '--version'], text=True),
        recipe=sys.argv)
    (output / 'build.json').write_text(json.dumps(value, indent=2) + '\n')


if __name__ == '__main__':
    record(Path(sys.argv[1]))
