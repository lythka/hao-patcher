#!/bin/sh
# Ship the fix recipes from this checkout; they are not per-OS binary inputs.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 - "$HERE/../.." "${1:?output}" <<'PY'
from pathlib import Path
import shutil
import sys
root, out = map(Path, sys.argv[1:])
out.mkdir(parents=True, exist_ok=False)
for name in ('audiomxd-fix', 'metal-fixes'):
    shutil.copytree(root/'tools'/name, out/name,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
PY
