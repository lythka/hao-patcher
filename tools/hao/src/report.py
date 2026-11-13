"""hao report: one text file a developer needs to diagnose a failed run, made to attach to a GitHub issue.

It holds: hao's release and commit, this Mac (sw_vers, model, CPU, memory, SIP, boot arguments), the `hao detect`
profile, the log of the last hao command (create-installer, build-disk and install keep their console events in
LOGS: /Library/Logs/hao when run as root, else ~/Library/Logs/hao), the install record
(/private/var/db/hao/install-record.json) and the live patch records, when there are any.

No private data: the serial number and hardware UUID, the computer and host names, user names (home folders become
USER), Wi-Fi network names, e-mail and hardware (MAC) addresses are replaced before anything is written.
"""
import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess

import detect as DETECT
import livepatch as LP

TOP = Path(__file__).resolve().parents[3]
SYSTEM_LOGS = Path('/Library/Logs/hao')
STATE = Path('/private/var/db/hao')
KEEP = 30                                     # command logs kept per folder
USER = 'USER'
HOME = re.compile(r'(/Users/)(?!Shared\b)([^/\s"\'\\]+)')
LABELLED = [
    # key or label, then its value: serials, UUIDs, Wi-Fi names and the like as plists, JSON and tool output show them
    (re.compile(r'((?:IOPlatformSerialNumber|serial[ _-]?number|SerialNumber|IOPlatformUUID|Hardware UUID|'
                r'Provisioning UDID)["\']?\s*(?:\([^)]*\))?\s*[:=]\s*["\']?)([^"\'\s,<]+)', re.I), '[removed]'),
    (re.compile(r'((?:SSID(?:_STR)?|Network ?Name|Current Network Information)["\']?\s*[:=]\s*["\']?)([^"\'\n,<]+)',
                re.I), '[wifi]'),
]
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
MAC_ADDRESS = re.compile(r'\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b')


def log_dir(env=None, euid=None):
    """Where this run keeps its command log: HAO_LOGS, else /Library/Logs/hao as root, else ~/Library/Logs/hao."""
    env = os.environ if env is None else env
    if env.get('HAO_LOGS'):
        return Path(env['HAO_LOGS'])
    if (os.geteuid() if euid is None else euid) == 0:
        return SYSTEM_LOGS
    return Path(env.get('HOME') or Path.home()) / 'Library' / 'Logs' / 'hao'


def log_dirs(env=None):
    """Every folder a command log may be in, newest first is decided by the files themselves."""
    env = os.environ if env is None else env
    if env.get('HAO_LOGS'):
        return [Path(env['HAO_LOGS'])]
    out = [SYSTEM_LOGS, Path(env.get('HOME') or Path.home()) / 'Library' / 'Logs' / 'hao']
    if env.get('SUDO_USER'):
        out.append(Path('/Users') / env['SUDO_USER'] / 'Library' / 'Logs' / 'hao')
    return list(dict.fromkeys(out))


def version():
    """{'release', 'commit'}: release.json beside hao names the release and the commit it was cut from; a checkout
    names its own commit."""
    out = {}
    rel = TOP / 'tools' / 'hao' / 'release.json'
    if rel.is_file():
        try:
            r = json.loads(rel.read_text())
            out['release'], out['release_commit'] = r.get('release'), r.get('hao_patcher')
        except ValueError:
            out['release'] = 'release.json unreadable'
    if (TOP / '.git').exists():
        r = subprocess.run(['git', '-C', str(TOP), 'describe', '--always', '--dirty', '--abbrev=12'],
                           capture_output=True, text=True)
        if r.returncode == 0:
            out['commit'] = r.stdout.strip()
    return out


def save_log(command, argv, events, folder=None, ver=None, now=None):
    """Keep COMMAND's console events as FOLDER/COMMAND-DATE.json (readable by everyone, no secrets: hao report
    still scrubs it); the oldest beyond KEEP go. Returns the path, or None when the folder cannot be written."""
    folder = Path(folder) if folder else log_dir()
    stamp = (now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ('%s-%s.json' % (command, stamp))
        path.write_text(json.dumps({'command': command, 'argv': list(argv), 'hao': ver if ver is not None
                                    else version(), 'events': events}, indent=1, default=str) + '\n')
        path.chmod(0o644)
        for old in sorted(folder.glob('*-*.json'), key=lambda p: p.stat().st_mtime)[:-KEEP]:
            old.unlink()
        return path
    except OSError:
        return None


def last_log(folders):
    found = [p for f in folders if Path(f).is_dir() for p in Path(f).glob('*-*.json')]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _out(host, *argv):
    r = host.run(list(argv), check=False)
    return r.stdout.decode(errors='replace').strip() if r.returncode == 0 else ''


def secrets(host, env):
    """{text: replacement} of what identifies this Mac or its owner."""
    found = {}
    pe = {}
    try:
        p = plistlib.loads(host.run(['ioreg', '-a', '-r', '-d', '1', '-c', 'IOPlatformExpertDevice'],
                                    check=False).stdout or b'')
        pe = p[0] if isinstance(p, list) and p else p if isinstance(p, dict) else {}
    except Exception:
        pass
    for key, label in (('IOPlatformSerialNumber', '[serial]'), ('IOPlatformUUID', '[uuid]')):
        if isinstance(pe.get(key), str):
            found[pe[key]] = label
    for argv in (['scutil', '--get', 'ComputerName'], ['scutil', '--get', 'LocalHostName'], ['hostname']):
        name = _out(host, *argv)
        if name:
            found[name] = '[computer name]'
            found[name.split('.')[0]] = '[computer name]'
    for key in ('SUDO_USER', 'USER', 'LOGNAME'):
        if env.get(key) and env[key] != 'root':
            found[env[key]] = USER
            full = _out(host, 'id', '-F', env[key])
            if full:
                found[full] = '[name]'
    home = env.get('HOME', '')
    if home.startswith('/Users/') and home.count('/') == 2:
        found[home.rsplit('/', 1)[1]] = USER
    return {k: v for k, v in found.items() if len(k) >= 3}


def scrub(text, private):
    """TEXT with PRIVATE's strings ({text: replacement}) and every labelled serial, UUID, Wi-Fi name, e-mail and
    hardware address replaced; the name of each home folder becomes USER."""
    text = HOME.sub(lambda m: m.group(1) + USER, text)
    for pattern, label in LABELLED:
        text = pattern.sub(lambda m: m.group(1) + label, text)
    text = EMAIL.sub('[email]', text)
    text = MAC_ADDRESS.sub('[hardware address]', text)
    for secret in sorted(private, key=len, reverse=True):
        text = re.sub(r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % re.escape(secret), private[secret], text)
    return text


def _json(path):
    try:
        return json.dumps(json.loads(Path(path).read_text()), indent=1, default=str)
    except (OSError, ValueError) as x:
        return 'unreadable: %s' % x


def collect(host, env=None, folders=None, state=STATE, ver=None, now=None):
    """The report's text, scrubbed."""
    env = dict(os.environ) if env is None else env
    folders = log_dirs(env) if folders is None else folders
    when = now or datetime.datetime.now()
    parts = ['hao report  %s' % when.strftime('%Y-%m-%d %H:%M:%S'),
             'Attach this file to a GitHub issue. Removed before writing: serial number, hardware UUID, computer '
             'name, user names, Wi-Fi names, e-mail and hardware addresses.']

    def section(title, body):
        parts.extend(['', '== ' + title, body.rstrip() or '(none)'])

    v = ver if ver is not None else version()
    section('hao', '\n'.join('%-15s %s' % (k, v[k]) for k in ('release', 'release_commit', 'commit') if v.get(k))
            or 'version unknown (no release.json and no git checkout)')
    csr = host.csr_config()
    boot_args = _out(host, 'nvram', 'boot-args').partition('\t')[2]
    mem = _out(host, 'sysctl', '-n', 'hw.memsize')
    section('This Mac', '\n'.join([
        _out(host, 'sw_vers'),
        'model          %s' % _out(host, 'sysctl', '-n', 'hw.model'),
        'cpu            %s' % _out(host, 'sysctl', '-n', 'machdep.cpu.brand_string'),
        'memory         %s' % ('%d GB' % (int(mem) // (1 << 30)) if mem.isdigit() else 'unknown'),
        'machine        %s' % _out(host, 'uname', '-m'),
        'kernel         %s' % _out(host, 'uname', '-v'),
        'SIP (csr)      %s' % ('unknown' if csr is None else '0x%x' % csr),
        'boot-args      %s' % (boot_args or '(none)'),
        'running as     %s' % ('root' if host.euid() == 0 else 'a user')]))
    try:
        p = DETECT.profile(DETECT.facts(host))
        p['target'] = DETECT.target_for_model(p['model'])[0]
        section('hao detect', json.dumps(p, indent=1))
    except (DETECT.DetectError, LP.PatchError, OSError, KeyError, ValueError) as x:
        section('hao detect', 'failed: %s' % x)
    log = last_log(folders)
    section('Last command' + (' (%s)' % log.name if log else ''), _json(log) if log else
            'no hao command log (looked in %s)' % ', '.join(str(f) for f in folders))
    others = sorted((p.name for f in folders if Path(f).is_dir() for p in Path(f).glob('*-*.json')
                     if log is None or p != log), reverse=True)
    if others:
        section('Earlier command logs', '\n'.join(others[:KEEP]))
    record = Path(state) / 'install-record.json'
    section('Install record', _json(record) if record.is_file() else 'none (%s)' % record)
    try:
        recs = LP.records(state)
    except (OSError, ValueError):
        recs = []
    if recs:
        section('Live patches', '\n'.join('%s %s packs %s snapshot %s' % (r.get('id'), r.get('state'),
                                                                          ','.join(r.get('packs', [])), r.get('snapshot'))
                                          for r in recs))
    return scrub('\n'.join(parts) + '\n', secrets(host, env))


def default_output(now=None, cwd=None):
    """hao-report-DATE.txt in the current folder (the release folder the user ran hao from), else /private/tmp."""
    name = 'hao-report-%s.txt' % (now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
    cwd = Path(cwd or os.getcwd())
    return (cwd if os.access(cwd, os.W_OK) else Path('/private/tmp')) / name


def write(path, text, env=None):
    """Write the report; under sudo, give it to the user who ran sudo so they can attach and delete it."""
    env = os.environ if env is None else env
    path = Path(path)
    path.write_text(text)
    path.chmod(0o644)
    if os.geteuid() == 0 and env.get('SUDO_UID', '').isdigit() and env.get('SUDO_GID', '').isdigit():
        try:
            os.chown(path, int(env['SUDO_UID']), int(env['SUDO_GID']))
        except OSError:
            pass
    return path


def report(a, c, host, env=None, folders=None, state=STATE, ver=None):
    path = Path(a.output).expanduser() if getattr(a, 'output', None) else default_output()
    text = collect(host, env, folders, state, ver)
    write(path, text, env)
    c.event('report', path=str(path), bytes=len(text))
    c.done('report written to %s' % path.resolve(), path=str(path))
    c.print('Attach it to a GitHub issue: it holds no serial number, user names or Wi-Fi names.', 'quiet')
    return 0
