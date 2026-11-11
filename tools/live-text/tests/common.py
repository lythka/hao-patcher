"""Helpers shared by the tools/live-text tests."""
import hashlib
from pathlib import Path
import platform
import subprocess
import sys

UNIT = Path(__file__).resolve().parents[1]
REPO = UNIT.parents[1]
FIXTURES = Path(__file__).resolve().parent/'fixtures'
sys.path.insert(0, str(UNIT/'lib'))


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True, **kwargs).stdout.strip()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def x86_runs():
    """Whether this host runs x86_64 programs (Intel, or Rosetta on Apple silicon)."""
    if platform.machine() == 'x86_64':
        return True
    return subprocess.run(['arch', '-x86_64', '/usr/bin/true'], capture_output=True).returncode == 0


def clang(output, *args):
    """Build an x86_64 program or dylib and sign it ad hoc (an unsigned one hangs in exec on Apple silicon)."""
    run('xcrun', 'clang', '-arch', 'x86_64', *args, '-o', output)
    run('codesign', '-f', '-s', '-', output)
    return output


def adapters(folder):
    """cache/metallib's compiler and GPU-client adapters (translation-build), built into the new FOLDER."""
    run(sys.executable, '-I', REPO/'cache/metallib/make', 'translation-build', folder)
    return Path(folder)
