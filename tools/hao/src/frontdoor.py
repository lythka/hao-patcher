"""The front door: what `hao` alone prints, where a command runs, and the exit code of a stop.

  hao                  the commands for where it runs (build Mac, USB installer, installed Mac), the hao and macOS versions
  hao help [--all]     the same; --all adds the developer commands
  a command run in the wrong place stops with where it runs instead (exit 5)

Exit codes: 0 done, 1 failed (stopped safely), 2 usage, 3 not enough space or the wrong drive, 4 verification failed (a
file did not match its sha256), 5 refused (wrong Mac, wrong place, needs root). A Stopped carries its code when its
raiser knows it (Stopped(..., code=N)); otherwise it is read from what the report says.
"""
import os
from pathlib import Path
import platform
import re
import subprocess

PLACES = {'build': 'this Mac (the build Mac)', 'installer': 'the USB installer', 'installed': 'this Mac (macOS 27 with hao)'}
THERE = {'build': 'any Mac on its own macOS (the build Mac)', 'installer': 'the USB installer',
         'installed': 'a Mac running macOS 27 with hao'}
# name, where it runs, one line. Developer commands are listed by `hao help --all` only.
COMMANDS = (
    ('create-installer', ('build',), 'make the USB installer for an Intel Mac'),
    ('install', ('installer',), 'install macOS 27 on this Mac'),
    ('verify', ('installed',), 'check every installed file against its manifest'),
    ('verify-release', ('build', 'installer', 'installed'), 'check a release yourself: signatures, files, tag'),
    ('explain', ('installed', 'build'), 'where a file comes from and every edit to it'),
    ('run', ('installed',), 'an ARM program through hao, with diagnostics (-v, --stats, --interp)'),
    ('aot', ('installed',), 'the ahead-of-time caches: build (sudo), --status, --background, --clear'),
    ('doctor', ('build',), 'is this Mac ready to make the USB installer: Python, Xcode, disk, downloads'),
    ('status', ('build', 'installer', 'installed'), 'hao on this Mac: release, macOS, SIP, install, caches, last stop'),
    ('report', ('build', 'installer', 'installed'), 'one file with what a developer needs, for a GitHub issue'),
)
DEVELOPER = (
    ('detect', 'this Mac\'s hardware, OCLP patch sets and packs, as a profile file'),
    ('fetch', 'download or check every file a release pins'),
    ('build-image', 'assemble an image in the builder VM'),
    ('build-disk', 'build macOS 27 onto an external SSD (sudo)'),
    ('assets', 'the assets folder a fresh clone builds from'),
    ('inspect', 'original and patched files side by side'),
    ('patch', 'apply, list or revert pack overlays'),
)
# where each place's own command is, for "runs from X; here, run Y"
INSTEAD = {'build': 'hao create-installer', 'installer': '/Volumes/HAO/install', 'installed': 'hao verify'}


def translated():
    """Rosetta describes this process as Intel, but the machine is a build host."""
    try:
        result = subprocess.run(['/usr/sbin/sysctl', '-n', 'sysctl.proc_translated'],
                                capture_output=True, text=True, timeout=5)
        return result.returncode == 0 and result.stdout.strip() == '1'
    except (OSError, subprocess.TimeoutExpired):
        return False


def where(env=None, top=None, record=Path('/private/var/db/hao/install-record.json'), mac=None, machine=None):
    """'installer' (started from the USB installer: its launchers set HAO_STICK, and hao sits beside the kit),
    'installed' (macOS 27 on an Intel Mac, or an install record), else 'build'."""
    env = os.environ if env is None else env
    top = Path(top) if top else Path(__file__).resolve().parents[3]
    if env.get('HAO_STICK') or (str(top).startswith('/Volumes/') and (top.parent / 'kit' / 'kit.json').is_file()):
        return 'installer'
    mac = platform.mac_ver()[0] if mac is None else mac
    machine = platform.machine() if machine is None else machine
    if record.is_file() or (mac.split('.')[0] == '27' and machine == 'x86_64' and not translated()):
        return 'installed'
    return 'build'


def dev_label(described):
    """"dev 73b28ae2" (and "+" when the checkout has changes) from git describe's "v0.1.0-alpha.1-78-g73b28ae2e743"."""
    if not described:
        return None
    import re
    m = re.search(r'(?:^|-g)([0-9a-f]{7,})(-dirty)?$', described)
    return 'dev %s%s' % (m.group(1)[:8], '+' if m.group(2) else '') if m else 'dev ' + described


def runs_in(command):
    """The places COMMAND runs, or None for a developer command (anywhere)."""
    for name, places, _ in COMMANDS:
        if name == command:
            return places
    return None


def wrong_place(command, here):
    """The refusal when COMMAND does not run HERE, or None."""
    places = runs_in(command)
    if places is None or here in places:
        return None
    there = ' or '.join(THERE[p] for p in places)
    return 'hao %s runs from %s; on %s, run %s' % (command, there, PLACES[here], INSTEAD[here])


def listing(c, here, all_=False, versions=None):
    """`hao` alone: the commands for HERE, then (with ALL_) the developer ones."""
    versions = versions or {}
    head = '%s %s  %s' % (c.paint('bold', 'hao'), c.paint('bold', versions.get('hao') or ''),
                          c.paint('dim', 'macOS %s · %s' % (versions.get('macos') or platform.mac_ver()[0] or '?',
                                                            PLACES[here])))
    c.print(head, 'quiet')
    c.print('', 'quiet')
    for name, places, line in COMMANDS:
        if here in places:
            c.print('  %-18s%s' % (name, line), 'quiet')
    if all_:
        c.print('', 'quiet')
        c.print(c.paint('dim', 'Developer commands'), 'quiet')
        for name, line in DEVELOPER:
            c.print('  %-18s%s' % (name, line), 'quiet')
    c.print('', 'quiet')
    c.print(c.paint('dim', 'hao COMMAND --help for its options; --quiet, -v, --show-edits and --log json on every command'),
            'quiet')
    c.event('listing', where=here, all=all_)


CODES = (   # what a stop report says -> its exit code, when its raiser gave none
    (3, re.compile(r'disk space|not enough space|space on this Mac|may not be erased|too small|not big enough|'
                   r'no USB (stick|drive)|no disk named|not a whole disk|wrong (drive|disk)', re.I)),
    (4, re.compile(r'sha256|does not match|did not match|damaged|verification|checksum|corrupt', re.I)),
    (5, re.compile(r'administrator|needs root|not running as root|cannot build onto a disk|has no target|'
                   r'not supported|runs from|System Integrity Protection|AMFI', re.I)),
)


def code_of(x):
    """The exit code of a Stopped X: its own, else 1 for "not confirmed", else from its words, else 1."""
    if getattr(x, 'code', None):
        return x.code
    if x.what == 'not confirmed':
        return 1
    text = ' '.join(str(t) for t in (x.what, x.cause, x.why) if t)
    for code, pattern in CODES:
        if pattern.search(text):
            return code
    return 1
