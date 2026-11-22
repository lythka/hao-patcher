"""hao doctor: is this Mac ready to make the USB installer, run CI or a release proof. Read only.

Checks, each ok, warn (reported, not stopping) or fail (stopping):
  python    the interpreter running hao is the pinned one (data/host.json): 3.14 on a build Mac, the kit's 3.12 on the
            USB installer and the installed Mac; on a build Mac also python3.14 on PATH, which pipeline/ runs
  shebangs  (in a checkout, --for ci or release) Python scripts naming another interpreter than the pinned one
  xcode     Xcode and its Metal toolchain (the Metal rows are made with them); its version is the pinned one for a
            byte-for-byte release proof
  macos     the host macOS against the oldest one a build was made on (older is reported, not refused)
  signature release.json is signed by a key hao carries (release.json.sig, an SSH signature: src/signing.py); for --for release the
            keys themselves must be there
  disk      free space against the release's peak (src/space.py), when there is a release.json
  urls      a HEAD request for each URL release.json downloads from (--offline skips it)
  mirror    each download has a mirror besides Apple's CDN (required for --for release)
  apple-mirror  with HAO_MIRROR=URL (a mirror of Apple's inputs): URL/health answers "ok", and how many of the
            release's inputs (pipeline/mirror-list, else release.json's downloads) URL/index.json holds; missing ones
            are named. A mirror cannot change what is installed: every download stays pinned by its sha256

`hao doctor` exits 0 when nothing fails, 5 otherwise. create-installer and pipeline/ci call checks() first and stop on
a failure (the same code, so the same answer).
"""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parents[1]
PINS = HERE / 'data' / 'host.json'
TOP = HERE.parents[1]
PURPOSES = ('create-installer', 'ci', 'release')


def pins():
    return json.loads(PINS.read_text())


def version_tuple(text):
    return tuple(int(x) for x in re.findall(r'\d+', text or '')[:3])


def check(name, state, detail, fix=None):
    return dict(name=name, state=state, detail=detail, fix=fix)


def check_python(where, p, version=None, which=shutil.which):
    want = p['python']['build' if where == 'build' else 'kit']
    have = '%d.%d' % (version or sys.version_info)[:2]
    out = []
    if have != want:
        out.append(check('python', 'fail', 'hao runs on Python %s; this place needs %s' % (have, want),
                         'run it with python%s (python.org\'s build): python%s -I %s' % (want, want, Path(sys.argv[0]).name)
                         if where == 'build' else 'start hao from the USB installer or /usr/local/bin/hao'))
    else:
        out.append(check('python', 'ok', 'Python %s' % '.'.join(map(str, (version or sys.version_info)[:3]))))
    if where == 'build':
        # what the pipeline's `env python3.14` will run: this run's PATH (as root, the launcher's verified interpreter)
        found = which('python%s' % want)
        if not found:
            out.append(check('python', 'fail', 'python%s is not on PATH (pipeline/ runs python%s -I)' % (want, want),
                             'install Python %s from python.org' % want))
        else:
            out.append(check('python', 'ok', 'pipeline/ runs %s' % found))
    return out


ROOT_PY = Path('/Library/Frameworks/Python.framework/Versions/3.14')


def check_interpreter(root_py=None):
    """What `sudo ./hao` will run (tools/hao/hao's launcher, owner 2026-11-17 "Root-owned Python only"): python.org's
    Python 3.14 only, and only when it, every folder above it and everything in its version folder (site-packages
    aside: hao runs it with -S) are root's alone. The same rule as the launcher, so doctor fails where sudo would."""
    import stat
    root_py = Path(root_py or ROOT_PY)
    fw = root_py.parent.parent
    fix = ('install Python 3.14 from python.org, then (its installer leaves it writable by the admin group): '
           'sudo chown -R root:wheel %s && sudo chmod -R go-w %s' % (fw, fw))
    exe = root_py / 'bin' / 'python3.14'
    if not exe.is_file() or exe.is_symlink():
        return [check('python', 'fail', 'sudo ./hao needs python.org\'s Python 3.14 (%s is missing)' % exe, fix)]

    def bad(st):
        return st.st_uid != 0 or st.st_mode & 0o022
    d = root_py
    while str(d) != '/':
        st = os.lstat(str(d))
        if stat.S_ISLNK(st.st_mode) or bad(st):
            return [check('python', 'fail', '%s is not root\'s alone: sudo ./hao will refuse it' % d, fix)]
        d = d.parent
    skip = root_py / 'lib' / 'python3.14' / 'site-packages'
    errors = []
    for top, dirs, files in os.walk(str(root_py), onerror=errors.append):
        dirs[:] = [x for x in dirs if Path(top, x) != skip]
        for name in dirs + files:
            path = os.path.join(top, name)
            st = os.lstat(path)
            if bad(st):
                return [check('python', 'fail', '%s can be changed by others than root: sudo ./hao will refuse it'
                              % path, fix)]
            if stat.S_ISLNK(st.st_mode):
                real, target = os.path.realpath(path), os.readlink(path)
                inside = real.startswith(str(root_py).rstrip('/') + '/')
                harmless = not os.path.exists(path) and not target.startswith('/') and '..' not in target
                if not (os.path.exists(path) and inside) and not harmless:
                    return [check('python', 'fail', '%s leads out of %s: sudo ./hao will refuse it' % (path, root_py),
                                  fix)]
    if errors:
        return [check('python', 'fail', '%s cannot be read whole (%s): sudo ./hao will refuse it' % (root_py, errors[0]),
                      fix)]
    return [check('python', 'ok', 'sudo ./hao runs %s (root\'s alone)' % exe)]


def check_shebangs(p, top=TOP, run=subprocess.run):
    """Python scripts in the checkout whose shebang names another interpreter than the pinned build one."""
    want = 'python%s' % p['python']['build']
    try:
        files = run(['/usr/bin/git', '-C', str(top), 'ls-files'], capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return [check('shebangs', 'warn', 'not a checkout: not checked')]
    other = {}
    for f in files:
        try:
            with open(top / f, 'rb') as fh:
                first = fh.readline(200).decode('utf-8', 'replace').strip()
        except OSError:
            continue
        m = re.match(r'#!\s*(?:/usr/bin/env\s+)?(\S*python[0-9.]*)', first)
        if m and not m.group(1).endswith(want):
            other.setdefault(m.group(1), []).append(f)
    if not other:
        return [check('shebangs', 'ok', 'every Python script names %s' % want)]
    detail = ', '.join('%d name %s' % (len(v), k) for k, v in sorted(other.items(), key=lambda kv: -len(kv[1])))
    return [check('shebangs', 'warn', '%s (pinned: %s)' % (detail, want),
                  'scripts run as "python%s -I SCRIPT" are unaffected; see each script\'s first line' % p['python']['build'])]


def check_xcode(purpose, p, run=subprocess.run):
    try:
        r = run(['/usr/bin/xcodebuild', '-version'], capture_output=True, text=True, timeout=30)
        have = r.stdout.split('\n')[0].replace('Xcode ', '').strip() if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        have = ''
    need = purpose in ('create-installer', 'release')
    if not have:
        return [check('xcode', 'fail' if need else 'warn', 'no Xcode (xcodebuild -version failed)',
                      'install Xcode %s and its Metal toolchain (Xcode > Settings > Components)' % p['xcode'])]
    out = []
    try:
        metal = run(['/usr/bin/xcrun', '--find', 'metal'], capture_output=True, text=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        metal = False
    if version_tuple(have) != version_tuple(p['xcode']):
        out.append(check('xcode', 'fail' if purpose == 'release' else 'warn',
                         'Xcode %s; the Metal rows are compared byte for byte at %s' % (have, p['xcode']),
                         'install Xcode %s for a release proof' % p['xcode']))
    else:
        out.append(check('xcode', 'ok', 'Xcode %s' % have))
    if not metal:
        out.append(check('xcode', 'fail' if need else 'warn', 'no Metal toolchain (xcrun --find metal failed)',
                         'Xcode > Settings > Components: Metal Toolchain'))
    return out


def check_macos(p, mac=None):
    have = mac if mac is not None else platform.mac_ver()[0]
    if not have:
        return [check('macos', 'warn', 'not macOS: not checked')]
    if version_tuple(have) < version_tuple(p['host_macos_tested']):
        return [check('macos', 'warn', 'macOS %s; builds were made on %s and later' % (have, p['host_macos_tested']))]
    return [check('macos', 'ok', 'macOS %s' % have)]


def check_signature(release, purpose, keys_path=None):
    import signing as SIGN
    keys_path = keys_path or SIGN.KEYS
    if not release:
        if purpose == 'release':
            try:
                SIGN.load_keys(keys_path)
            except SIGN.SignatureError as x:
                if SIGN.waiver('v0.0.1'):     # v0.0.1 ships unsigned (owner waiver): no keys yet is expected
                    return [check('signature', 'warn', '%s (v0.0.1 ships unsigned, owner waiver)' % x)]
                return [check('signature', 'fail', str(x), 'the owner\'s public key goes in tools/hao/data/release-keys.pub')]
        return [check('signature', 'warn', 'no release.json beside hao: no signature to check')]
    try:
        try:
            keys = SIGN.load_keys(keys_path)
        except SIGN.SignatureError:
            keys = {}                                # v0.0.1: no keys; only an owner-waived unsigned release passes
        SIGN.verified(release, keys)
        _, kid, hashed = SIGN._VERIFIED[os.path.abspath(str(release))]
    except SIGN.SignatureError as x:
        return [check('signature', 'fail', 'release.json: %s' % x, 'download the release again from its GitHub page')]
    if SIGN.is_unsigned(kid):
        return [check('signature', 'warn', 'release.json is an UNSIGNED release: %s; %s' % (kid, hashed))]
    return [check('signature', 'ok', 'release.json signed (key %s, %s)' % (kid, hashed))]


def check_disk(release, cache):
    if not release:
        return [check('disk', 'warn', 'no release.json beside hao: the space needed is not known')]
    import space
    pl = space.plan(release, cache)
    need = pl['mac_peak'] + pl.get('margin', 0)
    folder = Path(cache)
    while not folder.exists():
        folder = folder.parent
    free = shutil.disk_usage(str(folder)).free / 1e9
    if free < need:
        return [check('disk', 'fail', '%.0f GB free; the peak needs %.0f GB' % (free, need),
                      'free %.0f GB on this Mac (or point --cache at a bigger volume)' % (need - free + 1))]
    return [check('disk', 'ok', '%.0f GB free; the peak needs %.0f GB' % (free, need))]


def head(url, timeout, opener=None):
    """The HTTP status of a HEAD request, or the error as text."""
    req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'hao-doctor'})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as x:
        return x.code
    except (urllib.error.URLError, OSError, ValueError) as x:
        return str(getattr(x, 'reason', x))


INDEX_LIMIT = 64 << 20


def fetch(url, timeout, limit, opener=None):
    """URL's body (at most LIMIT bytes) by GET."""
    req = urllib.request.Request(url, headers={'User-Agent': 'hao-doctor/1'})
    with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError('more than %d bytes' % limit)
    return data


def mirror_needs(release, release_path=None, run=subprocess.run, top=TOP):
    """[(sha256, size, origin url, name)] the release needs from a mirror: pipeline/mirror-list's lines
    ("SHA256 SIZE ORIGIN_URL NAME", NAME may hold spaces; run with --release FILE) when it is there, else release.json's
    downloads."""
    tool = Path(top) / 'pipeline' / 'mirror-list'
    if tool.is_file() and release_path:
        try:
            r = run([sys.executable, '-I', str(tool), '--release', str(release_path)], capture_output=True, text=True,
                    timeout=120)
            if r.returncode == 0:
                out = []
                for line in r.stdout.splitlines():
                    parts = line.split(None, 3)
                    if len(parts) == 4 and re.fullmatch('[0-9a-f]{64}', parts[0]) and parts[1].isdigit():
                        out.append((parts[0], int(parts[1]), parts[2], parts[3]))
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    return [(d['sha256'], d['size'], d.get('url', ''), d.get('name', '')) for d in (release or {}).get('downloads', [])
            if isinstance(d, dict) and isinstance(d.get('sha256'), str) and isinstance(d.get('size'), int)]


def check_hao_mirror(release, p, offline, env=None, opener=None, release_path=None, needs=None):
    """HAO_MIRROR (a project mirror of Apple's inputs, tools' P6): reachable (URL/health answers "ok"), and how many of
    the release's inputs its URL/index.json holds ({sha256: {size, name, origin, added}}), naming the missing ones."""
    import safety as SAFE
    env = os.environ if env is None else env
    url = (env.get('HAO_MIRROR') or '').rstrip('/')
    if not url:
        return [check('apple-mirror', 'skip', 'mirror: not set (downloads go to the origin)')]
    if not re.match(r'https?://[^\s/]+', url):
        return [check('apple-mirror', 'fail', 'HAO_MIRROR is not an http(s) URL: %s' % SAFE.text(url)[:80],
                      'set HAO_MIRROR=https://HOST[:PORT] or unset it')]
    if offline:
        return [check('apple-mirror', 'warn', 'mirror %s: --offline, not checked' % SAFE.text(url))]
    timeout = p.get('head_timeout_s', 10)
    try:
        ok = fetch(url + '/health', timeout, 1024, opener).strip() == b'ok'
    except (OSError, ValueError) as x:
        ok, why = False, str(getattr(x, 'reason', x))
    else:
        why = 'it did not answer "ok"'
    if not ok:
        return [check('apple-mirror', 'fail', 'mirror %s: not reachable (%s)' % (SAFE.text(url), SAFE.text(why)[:80]),
                      'start the mirror, or unset HAO_MIRROR to download from the origin')]
    try:
        index = json.loads(fetch(url + '/index.json', timeout, INDEX_LIMIT, opener))
        if not isinstance(index, dict):
            raise ValueError('not a JSON object')
    except (OSError, ValueError) as x:
        return [check('apple-mirror', 'fail', 'mirror %s: index.json unreadable (%s)' % (
            SAFE.text(url), SAFE.text(str(getattr(x, 'reason', x)))[:80]))]
    needs = mirror_needs(release, release_path) if needs is None else needs
    if not needs:
        return [check('apple-mirror', 'warn', 'mirror %s: reachable, %d files; no release inputs to compare with (no '
                      'verified release.json beside hao)' % (SAFE.text(url), len(index)))]
    held = [n for n in needs if isinstance(index.get(n[0]), dict) and index[n[0]].get('size') == n[1]]
    missing = [n[3] for n in needs if n not in held]
    detail = 'mirror %s: reachable, %d of %d release inputs held' % (SAFE.text(url), len(held), len(needs))
    if missing:
        return [check('apple-mirror', 'warn', detail + '; missing: ' + ', '.join(SAFE.text(m) for m in missing[:5])
                      + (' and %d more' % (len(missing) - 5) if len(missing) > 5 else ''),
                      'those download from the origin')]
    return [check('apple-mirror', 'ok', detail)]


def check_urls(release, p, offline, opener=None):
    rows = (release or {}).get('downloads', [])
    if (release or {}).get('candidate'):
        # Rehosted inputs are checked by hash from the staged cut or mirror.
        # Their eventual public release URLs must not be contacted pre-release.
        rows = [r for r in rows if not r['url'].startswith(
            'https://github.com/lythka/hao-assets/releases/download/')]
    if not rows:
        return [check('urls', 'warn', 'no release.json downloads to check')]
    if offline:
        return [check('urls', 'warn', '--offline: %d download URLs not checked' % len(rows))]
    bad = []
    for r in rows:
        status = head(r['url'], p.get('head_timeout_s', 10), opener)
        if not (isinstance(status, int) and status < 400):
            bad.append('%s (%s)' % (r['name'], status))
    if bad:
        fix = 'check the network; a URL Apple moved needs a new release.json'
        if any('CERTIFICATE_VERIFY_FAILED' in b for b in bad):
            fix = ('this Python has no CA certificates: run "Install Certificates.command" in /Applications/Python 3.14, '
                   'or set SSL_CERT_FILE=/etc/ssl/cert.pem')
        return [check('urls', 'fail', '%d of %d download URLs do not answer: %s' % (len(bad), len(rows), '; '.join(bad[:3])),
                      fix)]
    return [check('urls', 'ok', 'all %d download URLs answer' % len(rows))]


def check_mirror(release, purpose):
    rows = (release or {}).get('downloads', [])
    if not rows:
        return [check('mirror', 'warn', 'no release.json downloads to check')]
    without = [r['name'] for r in rows if not r.get('mirrors')]
    if without:
        return [check('mirror', 'fail' if purpose == 'release' else 'warn',
                      '%d of %d downloads have no mirror besides their one URL: %s' % (
                          len(without), len(rows), ', '.join(without[:3]) + (' …' if len(without) > 3 else '')),
                      'a release names a hao-held copy of each Apple input in "mirrors"')]
    return [check('mirror', 'ok', 'every download has a mirror')]


def checks(purpose='create-installer', where='build', release=None, cache=None, offline=False, opener=None,
           run=subprocess.run, version=None, mac=None, signature=None, release_path=None):
    """[check] for PURPOSE (create-installer, ci, release) at WHERE (frontdoor.where)."""
    p = pins()
    frozen = (TOP / '.hao-source').is_file() and not (TOP / '.git').exists()
    if frozen and where == 'build':
        p = dict(p, python=dict(p['python'], build=p['python']['kit']))
    out = check_python(where, p, version)
    if where != 'build':
        return out
    if purpose == 'create-installer' and version is None and os.geteuid() == 0:
        out += check_interpreter()      # only a root run starts Python as root (as a user, sudo runs diskutil alone)
    if purpose in ('ci', 'release'):
        out += check_shebangs(p, run=run)
    if frozen and purpose == 'create-installer':
        folder = Path(os.environ.get('HAO_METAL_LLVM', ''))
        need = bool(release and any(x.get('row') == 'metal_llvm' for x in release.get('assets', [])))
        if need:
            good = all((folder/name).is_file() and os.access(folder/name, os.X_OK)
                       for name in ('llvm-as', 'llvm-dis', 'opt', 'assemble'))
            out.append(check('llvm', 'ok' if good else 'fail',
                             'pinned LLVM builder tools' if good else 'pinned LLVM tools are missing',
                             None if good else 'run this release through sh hao'))
    else:
        out += check_xcode(purpose, p, run)
    out += check_macos(p, mac)
    if purpose != 'ci':
        out += signature if signature is not None else check_signature(None, purpose)
        out += check_disk(release, cache or Path.home() / 'Library' / 'Caches' / 'hao')
        out += check_urls(release, p, offline, opener) + check_mirror(release, purpose)
        out += check_hao_mirror(release, p, offline, opener=opener, release_path=release_path)
    return out


MARK = {'ok': 'ok', 'warn': 'warn', 'fail': 'bad', 'skip': 'dim'}


def show(c, results, purpose):
    """The checks as a table on a Rich terminal, else one line each; one event per check."""
    for r in results:
        c.event('doctor.check', **r)
    sym = {'ok': c.ok, 'fail': c.bad, 'warn': c.paint('warn', '!')}
    plain = ['%s  %-9s %s%s' % (sym.get(r['state'], ' '), r['name'], r['detail'],
                                ('\n             ' + c.paint('dim', r['fix'])) if r['fix'] and r['state'] != 'ok' else '')
             for r in results]

    def table():
        from rich import box
        from rich.table import Table
        from rich.text import Text
        t = Table(title='hao doctor  ' + purpose, title_justify='left', title_style='bold', box=box.SIMPLE_HEAD,
                  show_edge=False, header_style=c.style('dim'), padding=(0, 1))
        t.add_column('', no_wrap=True)
        t.add_column('check', no_wrap=True)
        t.add_column('', overflow='fold')
        for r in results:
            detail = Text(r['detail'])
            if r['fix'] and r['state'] != 'ok':
                detail.append('\n' + r['fix'], style=c.style('dim'))
            t.add_row(Text.from_ansi(sym.get(r['state'], ' ')), r['name'], detail)
        return t
    c.block(table, plain, 'quiet')
    return [r for r in results if r['state'] == 'fail']


def doctor(a, c, release_path=None):
    import frontdoor as FD
    from console import Stopped
    release = None
    path = Path(getattr(a, 'release', None) or release_path or HERE / 'release.json')
    signature = check_signature(path if os.path.lexists(path) else None, a.purpose)
    if signature and signature[0]['state'] == 'ok':
        import signing as SIGN
        release = json.loads(SIGN.verified(path).read_bytes())  # the copy check_signature verified, not the path
    verified = None
    if release is not None:
        import signing as SIGN
        verified = SIGN.verified(path)                           # mirror-list reads the verified copy too
    failed = show(c, checks(a.purpose, FD.where(), release, getattr(a, 'cache', None), a.offline,
                            signature=signature, release_path=verified), a.purpose)
    if failed:
        raise Stopped('%d check%s failed: %s' % (len(failed), '' if len(failed) == 1 else 's',
                                                 ', '.join(r['name'] for r in failed)),
                      why='hao %s would stop or make different bytes on this Mac' % a.purpose,
                      untouched='nothing was changed', options=[r['fix'] for r in failed if r['fix']], code=5)
    c.done('this Mac is ready for %s' % a.purpose)
    return 0
