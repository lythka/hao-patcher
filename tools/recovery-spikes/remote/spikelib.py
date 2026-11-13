"""Shared helpers of the Recovery spikes. Runs ON THE LAPTOP with the bundled Python (3.12, standard library only).

Output protocol (stdout, one line each; run.sh's verdict reads it):
  CHECK name PASS|FAIL|SKIP detail      a question the spike answers
  INFO name detail                      a fact recorded for the report, never decides the verdict
  TIME name seconds
  RESULT key value
  END spike rc=N                        last line; missing means the spike died
Every command run is logged with its output to RESULTS/commands.log.
Scratch is RAM only. Anything attached or mounted is recorded in /private/var/tmp/hao-spikes/SPIKE.state before
use (common.sh clean undoes it in reverse), so a spike that dies leaves nothing the clean step cannot remove."""
from contextlib import contextmanager
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time

W = Path('/private/var/tmp/hao-spikes')


def clean_line(text):
    return ' '.join(str(text).split())


class Spike:
    def __init__(self, name, scratch):
        self.name = name
        self.scratch = Path(scratch)
        self.results = self.scratch/'results'
        self.results.mkdir(exist_ok=True)
        self.log = (self.results/'commands.log').open('a')
        self.failed = False

    # protocol
    def emit(self, *words):
        print(' '.join(clean_line(w) for w in words if w != ''), flush=True)

    def check(self, name, ok, detail=''):
        status = 'PASS' if ok is True else 'SKIP' if ok is None else 'FAIL'
        if status == 'FAIL':
            self.failed = True
        self.emit('CHECK', name, status, detail)
        return ok is True

    def info(self, name, detail):
        self.emit('INFO', name, detail)

    def result(self, key, value):
        self.emit('RESULT', key, value)

    @contextmanager
    def timed(self, name):
        start = time.monotonic()
        try:
            yield
        finally:
            self.emit('TIME', name, '%.1f' % (time.monotonic()-start))

    def end(self):
        rc = 1 if self.failed else 0
        self.emit('END', self.name, 'rc=%d' % rc)
        self.log.close()
        return rc

    # commands
    def run(self, *args, check=True, capture=True, input=None, timeout=3600):
        command = [str(a) for a in args]
        self.log.write('$ %s\n' % ' '.join(command))
        self.log.flush()
        p = subprocess.run(command, capture_output=capture, input=input, timeout=timeout)
        if capture:
            for stream in (p.stdout, p.stderr):
                if stream:
                    self.log.write(stream.decode(errors='replace')[-20000:])
        self.log.write('# rc=%d\n' % p.returncode)
        self.log.flush()
        if check and p.returncode:
            tail = (p.stderr or p.stdout or b'').decode(errors='replace').strip().splitlines()[-3:]
            raise CommandFailed('%s: rc=%d %s' % (command[0], p.returncode, ' | '.join(tail)))
        return p

    def plist(self, *args):
        return plistlib.loads(self.run(*args).stdout)

    # state for common.sh clean
    def remember(self, kind, what):
        W.mkdir(parents=True, exist_ok=True)
        with (W/(self.name+'.state')).open('a') as f:
            f.write('%s %s\n' % (kind, what))

    # scratch devices
    def attach_raw(self, image):
        """Attach a raw image file without mounting; returns its whole disk (/dev/diskN), remembered for clean."""
        attached = self.plist('hdiutil', 'attach', '-nomount', '-plist', '-imagekey', 'diskimage-class=CRawDiskImage',
                              image)
        whole = whole_disk(attached)
        self.remember('image', whole)
        return whole

    def detach(self, device):
        p = self.run('hdiutil', 'detach', device, check=False)
        if p.returncode:
            self.run('hdiutil', 'detach', '-force', device, check=False)

    def ramdisk(self, label, megabytes):
        """A second HFS+ RAM disk at /Volumes/HAOSPIKE-SPIKE-LABEL with owners on, remembered for clean."""
        mountpoint = Path('/Volumes/HAOSPIKE-%s-%s' % (self.name, label))
        if mountpoint.exists():
            raise CommandFailed('%s exists: run.sh clean first' % mountpoint)
        out = self.run('hdiutil', 'attach', '-nomount', 'ram://%d' % (megabytes*2048)).stdout.decode().split()
        device = out[0] if out else ''
        if not re.fullmatch(r'/dev/disk[0-9]+', device):
            raise CommandFailed('hdiutil ram:// gave %r' % device)
        self.remember('ramdisk', device)
        self.run('diskutil', 'erasevolume', 'HFS+', mountpoint.name, device)
        self.run('diskutil', 'enableOwnership', mountpoint)
        return device, mountpoint


class CommandFailed(Exception):
    pass


def whole_disk(attached):
    """The /dev/diskN entry of an hdiutil attach -plist result."""
    for entity in attached['system-entities']:
        device = entity.get('dev-entry', '')
        if re.fullmatch(r'/dev/disk[0-9]+', device):
            return device
    raise CommandFailed('no whole disk in hdiutil attach output')


def apfs_volume_entity(attached):
    """The APFS volume device of an attach result (content-hint 41504653-...), as install.py seal_image picks it."""
    return next(e['dev-entry'] for e in attached['system-entities']
                if e.get('content-hint', '').startswith('41504653'))


def container_on(listing, whole):
    """The container of `diskutil apfs list -plist` whose one physical store is a slice of WHOLE (or WHOLE)."""
    disk = whole.removeprefix('/dev/')
    found = [c for c in listing['Containers']
             if any(s['DeviceIdentifier'] == disk or s['DeviceIdentifier'].startswith(disk+'s')
                    for s in c['PhysicalStores'])]
    if len(found) != 1:
        raise CommandFailed('expected one container on %s, found %d' % (whole, len(found)))
    return found[0]


def mem_available_mb():
    try:
        out = subprocess.run(['vm_stat'], capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:   # the NetBoot Recovery has no vm_stat: the kernel's page counts (errs low)
        v = [int(subprocess.run(['sysctl', '-n', k], capture_output=True, text=True, check=True).stdout)
             for k in ('vm.pagesize', 'vm.page_free_count', 'vm.page_speculative_count', 'vm.page_purgeable_count')]
        return v[0]*sum(v[1:])//(1 << 20)
    return parse_vm_stat(out)


def parse_vm_stat(text):
    page = 4096
    m = re.search(r'page size of (\d+) bytes', text)
    if m:
        page = int(m.group(1))
    pages = 0
    for key in ('free', 'inactive', 'speculative', 'purgeable'):
        m = re.search(r'^Pages %s:\s+(\d+)\.' % key, text, re.M)
        if m:
            pages += int(m.group(1))
    return pages*page//(1 << 20)


def tree_bytes(root):
    """Logical size of the files under ROOT (symlinks count as their own lstat size)."""
    total = 0
    for folder, _, names in os.walk(root):
        for name in names:
            try:
                total += (Path(folder)/name).lstat().st_size
            except OSError:
                pass
    return total


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 22), b''):
            h.update(block)
    return h.hexdigest()


def main_guard(spike, body):
    """Run BODY(spike); any exception becomes a FAIL check named 'spike-error' and the END line still appears."""
    try:
        body(spike)
    except Exception as error:   # the protocol line is the report; the traceback goes to the log
        import traceback
        spike.log.write(traceback.format_exc())
        spike.check('spike-error', False, '%s: %s' % (type(error).__name__, error))
    sys.exit(spike.end())
