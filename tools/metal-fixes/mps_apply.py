"""Apply the native MPS initializer repair and sign a fresh cache family."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]  # repository root: patches/, cache/, tools/
sys.path.insert(0, str(REPO / 'patches/golden-gate-userspace/src'))
sys.path.insert(0, str(ROOT / 'lib'))  # dyld_cache, mps_matrix_initializer
from cache_pages import resign_file
from dyld_cache import apply_edits
from mps_matrix_initializer import plan_cache_family


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def apply(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    plan = plan_cache_family(source)
    files = [p for p in sorted(source.glob('dyld_shared_cache_x86_64*')) if p.is_file()]
    output.mkdir(mode=0o755, parents=True)
    inputs, outputs, actions = [], {}, []
    for original in files:
        target = output / original.name
        # Copy-on-write avoids allocating another complete Apple cache family.
        subprocess.run(['cp', '-c', str(original), str(target)], check=True)
        os.chflags(target, 0)
        target.chmod(0o644)
        changes = plan.get(original, [])
        if changes:
            apply_edits(target, changes)
        resigned = False
        if original.suffix not in ('.map', '.atlas', '.symbols'):
            resigned = resign_file(target, force=bool(changes))
        inputs.append(dict(path=str(original), sha256=digest(original)))
        outputs[original.name] = digest(target)
        actions.append(dict(file=original.name, edits=len(changes), resigned_4k=resigned))
    record = dict(unit='tools/metal-fixes/mps-apply', revision=1,
                  date=datetime.now(timezone.utc).isoformat(),
                  commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
                  inputs=inputs, outputs=outputs, actions=actions,
                  source_sha256={str(p.relative_to(REPO)): digest(p) for p in [
                      Path(__file__).resolve(), ROOT / 'lib/dyld_cache.py',
                      REPO / 'patches/golden-gate-userspace/src/cache_pages.py',
                      ROOT / 'lib/mps_matrix_initializer.py']})
    (output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
    return record
