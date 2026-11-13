"""Host side of the Recovery spikes (workflow plan step 15): stage the inputs, judge a spike's output.

  spikes.py stage SPIKE DIR            verify SPIKE's input rows (inputs.lock), copy them to DIR/inputs, sign the
                                       kmutil copy ad hoc (as kc-hook.sh does), add code.tar and manifest.tsv;
                                       prints the files to copy (relative to DIR), one per line
  spikes.py ramdisk SPIKE [FILL_MB]    the RAM disk size in MB the spike asks common.sh setup for (0: none)
  spikes.py timeout SPIKE              seconds the laptop step may take
  spikes.py verdict SPIKE LOG [--results DIR] [--out FILE]
                                       PASS / PARTIAL / FAIL from the CHECK lines (and, for kmutil, a host re-bind of
                                       the collected BootKC); writes FILE (verdict.json), exit 0 unless FAIL
Python 3.9+ standard library; reads pipeline/lock.py for row parsing and hashing."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
TOP = TOOL.parents[1]
LOCK = Path(os.environ.get('RECOVERY_SPIKES_LOCK') or TOOL/'inputs.lock')   # the variable is for tests


def load_lock_module():
    spec = importlib.util.spec_from_file_location('pipeline_lock', TOP/'pipeline/lock.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each row of inputs.lock that mirrors a pipeline input row: (row folder, row name). A test keeps them equal.
MIRRORS = {
    'tahoe_kmutil': ('pipeline/inputs/25G342', 'tahoe_kmutil'),
    'darwin27_kernel': ('pipeline/inputs/golden/26B131', 'darwin27_kernel'),
    'fl': ('pipeline/inputs/common', 'fl'),
    'fl_noent': ('pipeline/inputs/common', 'fl-noent'),
    'python_x86_64': ('pipeline/inputs/common', 'python_x86_64'),
}

# Repository paths every Python spike gets in code.tar (plus kc-binder's own stage at kcstage/).
# The suites the python spike runs read tools/kernel-foundation, the 26B131 kernel-fixes profile and patches.py.
CODE = ['tools/recovery-spikes/remote', 'tools/edit-engine', 'tools/kc-binder', 'tools/hao', 'tools/kernel-foundation',
        'tools/kernel-fixes/profiles/26B131', 'pipeline/patches.py', 'pipeline/stages/golden-layout.py',
        'patches/darwin27-kernel', 'patches/golden-gate-userspace/src']

# name -> inputs (lock row -> file name on the laptop), RAM disk MB, laptop step timeout (s), checks that must PASS
SPIKES = {
    'survey': dict(inputs={}, ramdisk=0, timeout=300, required=[]),
    'python': dict(inputs={'python_x86_64': 'python.tar.gz'}, ramdisk=384, timeout=1800,
                   required=['upload', 'python-starts', 'imports', 'tools-present', 'ctypes-libc',
                             'ctypes-corefoundation', 'apfs-framework', 'diskutil-plist', 'suite:edit-engine',
                             'suite:kc-binder', 'suite:hao', 'suite:darwin27-kernel']),
    'cryptex': dict(inputs={'python_x86_64': 'python.tar.gz', 'golden_app_cryptex': 'golden_app_cryptex.dmg',
                            'newfs_apfs_25G332': 'newfs_apfs', 'apfs_prepare_cryptex_25G332': 'apfs_prepare_cryptex'},
                    ramdisk=None, timeout=1800, required=['upload', 'python-starts']),
    'group': dict(inputs={'python_x86_64': 'python.tar.gz', 'fl': 'fl', 'fl_noent': 'fl-noent'}, ramdisk=7168,
                  timeout=900, required=['upload', 'python-starts']),
    'kmutil': dict(inputs={'python_x86_64': 'python.tar.gz', 'tahoe_kmutil': 'kmutil', 'darwin27_kernel': 'kernel'},
                   ramdisk=768, timeout=2700,
                   required=['upload', 'python-starts', 'internal-system-found', 'internal-bootkc', 'memory',
                             'tree-copied', 'kmutil-boot:tahoe', 'members-equal-internal', 'kc-bind']),
}
MARGIN_MB = 1024   # memory common.sh setup leaves free beside a RAM disk (a Recovery out of memory needs hands)
ORDER = ['survey', 'python', 'cryptex', 'group', 'kmutil']
SIGN = {'kmutil'}   # staged copies signed ad hoc, as darwin27-kernel's kc-hook.sh signs tahoe_kmutil


def ramdisk_mb(spike, fill_mb=256):
    size = SPIKES[spike]['ramdisk']
    if size is None:   # cryptex: Python + app.dmg + one image (app files + 256 MB slack + filler) at a time
        size = 384 + 64 + 64 + 256 + fill_mb + 128
    return size


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 22), b''):
            h.update(block)
    return h.hexdigest()


def lock_rows():
    return load_lock_module().rows(str(LOCK))


def resolve_row(lock, rows, name):
    want, path = rows[name]
    have = sha256(path)
    if have != want:
        sys.exit('%s: %s is %s, not %s (%s)' % (LOCK, path, have, want, name))
    return path


def code_tar(out, stage_kc_binder=True):
    """code.tar: CODE from the working tree (no __pycache__), kc-binder's stage at kcstage/, CODE.json naming HEAD."""
    def add(tar, path, arcname):
        info = tar.gettarinfo(str(path), arcname)
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        if info.isfile():
            with open(path, 'rb') as f:
                tar.addfile(info, f)
        else:
            tar.addfile(info)

    def walk(tar, base, rel, arcbase):
        path = base/rel
        if path.is_file():
            add(tar, path, arcbase+rel)
            return
        for folder, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d != '__pycache__')
            frel = os.path.relpath(folder, base)
            add(tar, Path(folder), arcbase+frel)
            for name in sorted(names):
                if name != '.DS_Store' and not name.endswith('.pyc'):
                    add(tar, Path(folder)/name, arcbase+os.path.join(frel, name))

    head = subprocess.run(['git', '-C', str(TOP), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', '-C', str(TOP), 'status', '--porcelain', '--']+CODE, capture_output=True,
                           text=True).stdout.splitlines()
    with tarfile.open(out, 'w', format=tarfile.PAX_FORMAT) as tar:
        for rel in CODE:
            walk(tar, TOP, rel, '')
        if stage_kc_binder:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run([sys.executable, '-I', str(TOP/'tools/kc-binder/kc-binder'), 'stage', tmp+'/kcstage'],
                               check=True, capture_output=True)
                walk(tar, Path(tmp), 'kcstage', '')
        meta = json.dumps(dict(head=head, dirty=dirty), indent=1).encode()
        info = tarfile.TarInfo('CODE.json')
        info.size = len(meta)
        tar.addfile(info, io.BytesIO(meta))
    return head, dirty


# The NetBoot Recovery's shared cache has no libedit, libncurses or libpanel (laptop, 25G241, 2026-11-13: python3.12
# aborts "Library not loaded: /usr/lib/libpanel.5.4.dylib"). Python's core never needs them (only readline/curses
# do), so the Recovery bundle is the pinned one with those three LC_LOAD_DYLIB made LC_LOAD_WEAK_DYLIB in
# bin/python3.12, re-signed ad hoc. Declared here, made on the host from the pinned row; no Apple file is involved.
WEAK_IN_RECOVERY = ('libedit', 'libncurses', 'libpanel')


def weaken_dylibs(path, names=WEAK_IN_RECOVERY):
    """Turn LC_LOAD_DYLIB commands naming any of NAMES into LC_LOAD_WEAK_DYLIB in a thin x86_64 Mach-O; returns the
    install names changed."""
    import struct
    b = bytearray(Path(path).read_bytes())
    if struct.unpack_from('<I', b, 0)[0] != 0xfeedfacf:
        raise ValueError('%s: not a thin 64-bit Mach-O' % path)
    ncmds, off, changed = struct.unpack_from('<I', b, 16)[0], 32, []
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', b, off)
        if cmd == 0xc:
            name = bytes(b[off + struct.unpack_from('<I', b, off + 8)[0]:off + size]).split(b'\0')[0].decode()
            if any(n in name for n in names):
                struct.pack_into('<I', b, off, 0x80000018)
                changed.append(name)
        off += size
    Path(path).write_bytes(bytes(b))
    return changed


def recovery_python(source, target):
    """TARGET = SOURCE's python-build-standalone tar with bin/python3.12 weakened for the Recovery and re-signed."""
    import tarfile, tempfile
    if not tarfile.is_tarfile(source):     # test fixtures stand in for the archive with a few bytes
        shutil.copyfile(source, target)
        return
    with tempfile.TemporaryDirectory() as t:
        with tarfile.open(source) as tar:
            tar.extractall(t, filter='tar')
        exe = Path(t)/'python'/'bin'/'python3.12'
        if not weaken_dylibs(exe):
            raise ValueError('%s: none of %s linked' % (exe, ', '.join(WEAK_IN_RECOVERY)))
        subprocess.run(['codesign', '-f', '-s', '-', str(exe)], check=True, capture_output=True)
        with tarfile.open(target, 'w:gz') as tar:
            tar.add(Path(t)/'python', arcname='python')


def stage(spike, folder):
    folder = Path(folder)
    inputs = folder/'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    rows = lock_rows()
    manifest, files = [], []
    for row, dest in SPIKES[spike]['inputs'].items():
        source = resolve_row(LOCK, rows, row)
        target = inputs/dest
        if dest == 'python.tar.gz':
            recovery_python(source, target)
        else:
            shutil.copyfile(source, target)
        os.chmod(target, 0o755 if dest != 'python.tar.gz' and not dest.endswith('.dmg') else 0o644)
        if dest in SIGN:
            subprocess.run(['codesign', '-s', '-', '-f', str(target)], check=True, capture_output=True)
        manifest.append((dest, sha256(target), target.stat().st_size))
    if spike != 'survey':
        head, dirty = code_tar(inputs/'code.tar')
        manifest.append(('code.tar', sha256(inputs/'code.tar'), (inputs/'code.tar').stat().st_size))
        if dirty:
            print('note: code.tar is from a working tree with changes: %s' % '; '.join(dirty), file=sys.stderr)
    (inputs/'manifest.tsv').write_text(''.join('%s\t%s\t%d\n' % m for m in manifest))
    files = ['inputs/%s' % m[0] for m in manifest] + (['inputs/manifest.tsv'] if manifest else [])
    return files


# --- verdicts -------------------------------------------------------------------------------------------------

LINE = re.compile(r'^(CHECK|INFO|TIME|RESULT|END|SURVEY) (\S+)(?: (.*))?$')


def parse(text):
    out = dict(checks={}, info={}, time={}, result={}, survey=[], end=None)
    for raw in text.splitlines():
        m = LINE.match(raw.strip())
        if not m:
            continue
        kind, name, rest = m.group(1), m.group(2), m.group(3) or ''
        if kind == 'CHECK':
            status, _, detail = rest.partition(' ')
            out['checks'][name] = (status, detail)
        elif kind == 'INFO':
            out['info'][name] = rest
        elif kind == 'TIME':
            out['time'][name] = float(rest) if re.fullmatch(r'[0-9.]+', rest) else rest
        elif kind == 'RESULT':
            out['result'][name] = rest
        elif kind == 'SURVEY':
            out['survey'].append((name, rest))
        elif kind == 'END':
            m2 = re.search(r'rc=(\d+)', rest)
            out['end'] = int(m2.group(1)) if m2 else None
    return out


def status(parsed, name):
    return parsed['checks'].get(name, ('MISSING', ''))[0]


def all_pass(parsed, names):
    return all(status(parsed, n) == 'PASS' for n in names)


def survey_facts(parsed):
    facts = {}
    for key, value in parsed['survey']:
        if key in ('df', 'mount', 'internal_disk', 'apfs_volume'):
            facts.setdefault(key, []).append(value)
        else:
            facts[key] = value
    return facts


def judge(spike, parsed, host_checks=None):
    """(verdict, questions, failures): verdict PASS / PARTIAL / FAIL; questions name -> PASS/FAIL per question."""
    for name, (st, detail) in (host_checks or {}).items():
        parsed['checks'][name] = (st, detail)
    failures = ['%s %s %s' % (n, s, d) for n, (s, d) in parsed['checks'].items() if s == 'FAIL']
    questions = {}
    if parsed['end'] is None:
        return 'FAIL', {'finished': 'FAIL'}, failures+['no END line: the spike died or the connection dropped']
    required = SPIKES[spike]['required'] + list(host_checks or {})
    base = all_pass(parsed, required) and 'spike-error' not in parsed['checks']
    missing = [n for n in required if status(parsed, n) != 'PASS']
    if missing:
        failures += ['required check %s is %s' % (n, status(parsed, n)) for n in missing if status(parsed, n) != 'FAIL']
    if spike == 'survey':
        facts = survey_facts(parsed)
        ok = facts.get('arch') == 'x86_64'
        questions['survey'] = 'PASS' if ok else 'FAIL'
        return ('PASS' if ok and parsed['end'] == 0 else 'FAIL'), questions, failures
    if spike == 'python':
        questions['python'] = 'PASS' if base else 'FAIL'
        return questions['python'], questions, failures
    if spike == 'cryptex':
        for label in ('recovery', 'carried'):
            questions['seal-'+label] = 'PASS' if all_pass(parsed, ['seal:'+label, 'readback:'+label]) else 'FAIL'
        verdict = ('PASS' if questions['seal-recovery'] == 'PASS' else
                   'PARTIAL' if questions['seal-carried'] == 'PASS' else 'FAIL')
        return (verdict if base else 'FAIL'), questions, failures
    if spike == 'group':
        questions['group-add'] = 'PASS' if all_pass(parsed, ['seed-altlink', 'group-add', 'group-add-readback']) else 'FAIL'
        questions['group-with'] = 'PASS' if all_pass(parsed, ['group-with']) else 'FAIL'
        questions['fl'] = 'PASS' if all_pass(parsed, ['fl-entitled', 'fl-readback']) else 'FAIL'
        questions['roles'] = 'PASS' if all_pass(parsed, ['other-roles']) else 'FAIL'
        if not base:
            return 'FAIL', questions, failures
        if all(v == 'PASS' for v in questions.values()):
            return 'PASS', questions, failures
        return ('PARTIAL' if 'PASS' in (questions['group-add'], questions['group-with']) else 'FAIL'), questions, failures
    if spike == 'kmutil':
        safe = status(parsed, 'internal-readonly') in ('PASS', 'MISSING')
        if not safe:
            failures.append('the internal System volume was not mounted read-only')
        questions['link-boot'] = 'PASS' if base and safe else 'FAIL'
        sys_status = status(parsed, 'kmutil-sys:tahoe')
        questions['link-sys'] = {'PASS': 'PASS', 'SKIP': 'NOT RUN (boot scope)'}.get(sys_status, 'FAIL')
        if not (base and safe):
            return 'FAIL', questions, failures
        return {'PASS': 'PASS', 'SKIP': 'PARTIAL'}.get(sys_status, 'FAIL'), questions, failures
    raise ValueError(spike)


def host_rebind(results):
    """kmutil: bind the collected BootKC on the host too; the laptop's bind.json must list the same output hashes."""
    kc = Path(results)/'kc-tahoe/BootKernelExtensions.kc'
    laptop = Path(results)/'bind-tahoe/bind.json'
    if not kc.is_file() or not laptop.is_file():
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run([sys.executable, '-I', str(TOP/'tools/kc-binder/kc-binder'), 'bind', '--boot-kc', str(kc),
                            '--out', tmp+'/out'], capture_output=True, text=True)
        if p.returncode:
            return {'host-bind-equal': ('FAIL', 'host bind rc=%d: %s' % (p.returncode, p.stderr.strip()[-200:]))}
        here = json.loads(Path(tmp+'/out/bind.json').read_text())['files']
    there = json.loads(laptop.read_text())['files']
    same = here == there
    return {'host-bind-equal': ('PASS' if same else 'FAIL',
                                'laptop and host bind outputs %s' % ('equal' if same else 'differ: %s vs %s' % (there, here)))}


def verdict(spike, log, results=None, out=None):
    parsed = parse(Path(log).read_text(errors='replace'))
    host = host_rebind(results) if spike == 'kmutil' and results else {}
    v, questions, failures = judge(spike, parsed, host)
    record = dict(spike=spike, verdict=v, questions=questions, failures=failures, checks=parsed['checks'],
                  info=parsed['info'], time=parsed['time'], result=parsed['result'], end=parsed['end'])
    if spike == 'survey':
        facts = survey_facts(parsed)
        record['survey'] = facts
        try:
            available = int(facts.get('mem_available_mb', '0'))
        except ValueError:
            available = 0
        record['ram_budget'] = {s: dict(ramdisk_mb=ramdisk_mb(s), fits=ramdisk_mb(s)+MARGIN_MB <= available)
                                for s in ORDER if s != 'survey'}
    if out:
        Path(out).write_text(json.dumps(record, indent=1)+'\n')
    lines = ['SPIKE %s: %s' % (spike, v)]
    lines += ['  %-11s %s' % (q, s) for q, s in questions.items()]
    lines += ['  FAIL: %s' % f for f in failures]
    if spike == 'survey':
        lines += ['  RAM disk %-7s %5d MB %s' % (s, b['ramdisk_mb'], 'fits' if b['fits'] else 'DOES NOT FIT')
                  for s, b in record['ram_budget'].items()]
    return v, '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('stage')
    p.add_argument('spike', choices=ORDER)
    p.add_argument('folder')
    p = sub.add_parser('ramdisk')
    p.add_argument('spike', choices=ORDER)
    p.add_argument('fill', nargs='?', type=int, default=256)
    p = sub.add_parser('timeout')
    p.add_argument('spike', choices=ORDER)
    p = sub.add_parser('verdict')
    p.add_argument('spike', choices=ORDER)
    p.add_argument('log')
    p.add_argument('--results')
    p.add_argument('--out')
    args = parser.parse_args(argv)
    if args.command == 'stage':
        print('\n'.join(stage(args.spike, args.folder)))
    elif args.command == 'ramdisk':
        print(ramdisk_mb(args.spike, args.fill))
    elif args.command == 'timeout':
        print(SPIKES[args.spike]['timeout'])
    else:
        v, text = verdict(args.spike, args.log, args.results, args.out)
        print(text)
        return 1 if v == 'FAIL' else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
