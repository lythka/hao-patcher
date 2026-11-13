"""Spike (a): the bundled x86_64 Python in the Tahoe NetBoot Recovery. Runs ON THE LAPTOP (spike.sh starts it).
   python_spike.py T
Answers: does python-build-standalone 3.12.14 run there, can it load every standard module the installer's tools
import, can ctypes reach CoreFoundation and the private APFS.framework (golden-layout.py's group call), can it read
diskutil's plists, and do the install-time tools' own test suites pass on it. Nothing is written but T/results."""
import ctypes
import hashlib
import importlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spikelib import Spike, main_guard   # noqa: E402

# What the installer's Python imports (pipeline, tools/edit-engine, kc-binder, hao, golden-layout, install.py).
MODULES = ['argparse', 'bz2', 'concurrent.futures', 'contextlib', 'ctypes', 'datetime', 'errno', 'fcntl', 'fnmatch',
           'gzip', 'hashlib', 'importlib.util', 'io', 'json', 'lzma', 'mmap', 'os', 'pathlib', 'plistlib', 're',
           'select', 'shlex', 'shutil', 'signal', 'socket', 'sqlite3', 'ssl', 'stat', 'struct', 'subprocess', 'tarfile',
           'tempfile', 'textwrap', 'threading', 'time', 'unittest', 'urllib.request', 'uuid', 'xml.etree.ElementTree',
           'zipfile', 'zlib']
# Programs the installer's steps call; codesign is the edit engine's signer.
# 25G241's NetBoot Recovery has no install(1), rsync, mkfile or shasum (laptop 2026-11-13): our tools copy and
# hash with Python, so they are reported (tools-other), not required.
TOOLS_REQUIRED = ['codesign', 'ditto', 'hdiutil', 'diskutil', 'bless', 'tar', 'chflags', 'plutil']
TOOLS_INFO = ['kmutil', 'rsync', 'perl', 'mkfile', 'nvram', 'csrutil']
SUITES = ['tools/edit-engine/tests', 'tools/kc-binder/tests', 'tools/hao/tests', 'patches/darwin27-kernel/tests']


def body(s):
    code = s.scratch/'code'
    missing = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as error:
            missing.append('%s (%s)' % (name, error))
    s.check('imports', not missing, '%d modules' % len(MODULES) if not missing else 'missing: '+', '.join(missing))

    absent = [t for t in TOOLS_REQUIRED if not shutil.which(t)]
    s.check('tools-present', not absent, ' '.join(TOOLS_REQUIRED) if not absent else 'absent: '+' '.join(absent))
    s.info('tools-other', ' '.join('%s=%s' % (t, shutil.which(t) or 'absent') for t in TOOLS_INFO))

    # libc through ctypes (golden-layout's xattr calls)
    libc = ctypes.CDLL(None, use_errno=True)
    s.check('ctypes-libc', all(hasattr(libc, f) for f in ('getxattr', 'setxattr', 'listxattr', 'fsctl')),
            'getxattr setxattr listxattr fsctl')

    # CoreFoundation round trip, as golden-layout.py group_add builds its CFArray of CFNumbers
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
    cf.CFNumberCreate.restype = ctypes.c_void_p
    cf.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
    cf.CFArrayCreate.restype = ctypes.c_void_p
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    numbers = [cf.CFNumberCreate(None, 1, ctypes.byref(ctypes.c_int8(i))) for i in (2, 1)]
    array = cf.CFArrayCreate(None, (ctypes.c_void_p*2)(*numbers), 2, None)
    count = cf.CFArrayGetCount(array) if array else -1
    for item in [array]+numbers:
        if item:
            cf.CFRelease(item)
    s.check('ctypes-corefoundation', count == 2, 'CFArray of 2 CFNumbers, count %d' % count)

    # the private APFS framework: only resolve the symbol here; the group spike calls it on a scratch image
    try:
        apfs = ctypes.CDLL('/System/Library/PrivateFrameworks/APFS.framework/APFS')
        ok = hasattr(apfs, 'APFSContainerVolumeGroupAdd')
        s.check('apfs-framework', ok, 'APFSContainerVolumeGroupAdd %s' % ('resolved' if ok else 'missing'))
    except OSError as error:
        s.check('apfs-framework', False, 'dlopen: %s' % error)

    info = plistlib.loads(subprocess.run(['diskutil', 'info', '-plist', '/'], capture_output=True, check=True).stdout)
    s.check('diskutil-plist', 'DeviceIdentifier' in info,
            'root %s %s' % (info.get('DeviceIdentifier'), info.get('VolumeName')))

    import ssl
    stats = ssl.create_default_context().cert_store_stats()
    s.info('ssl', '%s; default context x509_ca=%d (0 means downloads need a CA bundle of their own)'
           % (ssl.OPENSSL_VERSION, stats.get('x509_ca', 0)))

    block = os.urandom(1 << 20)
    h = hashlib.sha256()
    start = time.monotonic()
    for _ in range(256):
        h.update(block)
    seconds = time.monotonic()-start
    s.result('sha256_mb_s', '%.0f' % (256/seconds if seconds else 0))

    for suite in SUITES:
        label = suite.replace('/tests', '').split('/')[-1]
        start = time.monotonic()
        p = subprocess.run([sys.executable, '-I', '-B', '-m', 'unittest', 'discover', '-s', suite],
                           cwd=code, capture_output=True, text=True, timeout=1800)
        (s.results/('suite-%s.txt' % label)).write_text(p.stdout+p.stderr)
        lines = [l for l in (p.stdout+p.stderr).splitlines() if l.strip()]
        ran = next((l for l in reversed(lines) if l.startswith('Ran ')), 'no "Ran" line')
        last = lines[-1] if lines else ''
        s.check('suite:'+label, p.returncode == 0 and last.startswith('OK'), '%s; %s' % (ran, last))
        s.emit('TIME', 'suite:'+label, '%.1f' % (time.monotonic()-start))


if __name__ == '__main__':
    main_guard(Spike('python', sys.argv[1]), body)
