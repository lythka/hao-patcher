"""Resolve signed helper input rows without invoking a developer toolchain."""
import hashlib
import os
from pathlib import Path
import re
import shutil

from .exports import require

REPO = Path(__file__).resolve().parents[4]


def helper(name, destination=None):
    row = REPO / 'pipeline/inputs/common' / ('shared_cache_' + name.replace('-', '_'))
    path = None
    if row.is_file():
        lines = [line.split('#', 1)[0].split() for line in row.read_text().splitlines()]
        lines = [line for line in lines if line]
        require(len(lines) == 1 and len(lines[0]) == 2, f'invalid helper row {row.name}')
        expected, relative = lines[0]
        require(re.fullmatch('[0-9a-f]{64}', expected), f'unpinned helper row {row.name}')
        path = REPO / relative
        require(path.is_file(), f'missing artifact for pinned helper {name}: {path}')
        if path.is_file():
            with path.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(actual == expected, f'helper SHA256 mismatch: {name}')
    if path is None:
        require(os.environ.get('SHARED_CACHE_DEVELOPER_BUILD') == '1',
                f'missing pinned helper {name}; install its input row artifact or explicitly use --developer-build')
        return None
    if destination is not None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        return destination
    return path
