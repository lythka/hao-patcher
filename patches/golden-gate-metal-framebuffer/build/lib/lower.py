"""Host interface to the native AIR lowering used by the runtime adapter."""
import atexit
import json
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parents[1]
_executable = None


def build_native(output):
    command = ['xcrun', 'clang++', '-std=c++17', '-arch', 'x86_64',
               '-mmacosx-version-min=13.0', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
               '-framework', 'Foundation', str(HERE/'src/lower-cli.mm'),
               str(HERE/'src/lower.cpp'), '-o', str(output)]
    subprocess.run(command, check=True, capture_output=True)
    subprocess.run(['codesign', '-s', '-', str(output)], check=True, capture_output=True)
    return command


def use_executable(path):
    global _executable
    _executable = str(path)


def _run(text, mode, *args):
    global _executable
    if _executable is None:
        scratch = tempfile.TemporaryDirectory(prefix='metal-framebuffer-lower-')
        atexit.register(scratch.cleanup)
        path = Path(scratch.name)/'lower'
        build_native(path)
        _executable = str(path)
    result = subprocess.run([_executable, mode, *args], input=text, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


def lower(text, *, multisample=False):
    rewritten, plan = _run(text, 'multisample' if multisample else 'single')
    return rewritten, plan


def split(text):
    return _run(text, 'split')


def air_version(text, key):
    """(major, minor) of named metadata KEY, e.g. air.version: the runtime's VERS lookup."""
    return tuple(_run(text, 'version', key))
