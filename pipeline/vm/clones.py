#!/usr/bin/python3 -I
"""Named APFS builder clones and per-bundle startup exclusion. No host mounts or sudo."""
import argparse
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import socket
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
DEFAULT_BASE = Path(__file__).resolve().parents[2] / 'store/vm/base'
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / 'store/vm'
FILES = ('disk.img', 'aux.img', 'hw.bin', 'machine.bin')
MARKER = '.ssdvm-clone.json'


def need(value, message):
    if not value:
        raise ValueError(message)


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    path.chmod(0o600)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('VM or clone registry operation already in progress: ' + str(path))
        yield


def process_rows():
    result = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, check=True)
    return [line.strip().split(None, 1) for line in result.stdout.splitlines() if line.strip()]


def running(vm):
    # ps emits paths containing spaces without shell quoting. Match the complete canonical operand.
    result = []
    for pid, command in process_rows():
        match = re.search(r'(?:^|/)ssdvm run (.*?)(?: --|$)', command)
        if match and Path(match[1]).resolve() == vm.resolve():
            result.append(int(pid))
    return result


@contextmanager
def idle(vm):
    need(not running(vm), 'VM is running; refusing to copy, remove or start it: ' + str(vm))
    # The maintained launcher also locks direct invocations before opening any disks.
    with locked(Path(str(vm) + '.run') / 'instance.lock'):
        yield


def clonefile(source, target):
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    operation = libc.clonefile
    operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    operation.restype = ctypes.c_int
    if operation(os.fsencode(source), os.fsencode(target), 0):
        error = ctypes.get_errno()
        raise OSError(error, 'APFS clonefile failed (no full-copy fallback): ' + os.strerror(error))


def records(root):
    path = root / 'registry.json'
    if not path.exists():
        return {}
    result = json.loads(path.read_text())
    need(type(result) is dict, 'invalid clone registry')
    return result


def save_records(root, value):
    temporary = root / ('registry.' + uuid.uuid4().hex + '.tmp')
    write(temporary, value)
    os.replace(temporary, root / 'registry.json')


def owned(vm):
    vm = vm.resolve()
    marker = json.loads((vm / MARKER).read_text())
    need(marker.get('schema') == 1 and marker.get('vm') == str(vm), 'copied or invalid clone marker')
    root = Path(marker['root'])
    need(root.is_absolute() and root.resolve() == root and vm.parent.parent == root, 'clone outside recorded root')
    need(marker == records(root).get(marker['name']), 'clone marker does not match its creation registry')
    need(vm != Path(marker['base']).resolve() and vm != DEFAULT_BASE.resolve(), 'refusing the base VM')
    need(Path(marker['share']).resolve() == vm.parent / 'share', 'clone share moved or symlinked')
    need(vm.name == 'vm' and vm.parent.name == marker['name'], 'clone path/name mismatch')
    return marker


def create(name, base=DEFAULT_BASE, root=DEFAULT_ROOT, port=None):
    need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name), 'invalid clone name')
    base, root = Path(base).resolve(), Path(root).resolve()
    need(base.is_dir() and root != base and not root.is_relative_to(base), 'invalid base or clone root')
    root.mkdir(parents=True, exist_ok=True)
    with locked(root / '.registry.lock'), locked(Path(str(base) + '.run') / 'operation.lock'), idle(base):
        registry = records(root)
        home = root / name
        need(name not in registry and not home.exists(), 'clone name already exists')
        for entry in FILES:
            item = base / entry
            need(item.is_file() and not item.is_symlink() and item.stat().st_nlink == 1, 'invalid base VM file: ' + entry)
        agent = json.loads((base / 'agent.json').read_text())
        used = {item['port'] for item in registry.values()} | {agent['port'], 8765, 8766}
        if port is None:
            port = next((p for p in range(20000, 50000) if p not in used and port_free(p)), None)
        need(type(port) is int and 1024 <= port <= 65535 and port not in used and port_free(port), 'agent port unavailable')
        mac = '02:' + ':'.join(f'{b:02x}' for b in uuid.uuid4().bytes[:5])
        need(mac not in {item['mac'] for item in registry.values()}, 'MAC collision')
        vm = home / 'vm'
        identity = uuid.uuid4().hex
        before = {f: (base / f).stat() for f in FILES}
        home.mkdir()
        try:
            vm.mkdir()
            for entry in FILES:
                clonefile(base / entry, vm / entry)
            # Reject a source changed during cloning, even if an old external launcher bypassed our lock.
            for f, old in before.items():
                new = (base / f).stat()
                need((old.st_ino, old.st_size, old.st_mtime_ns, old.st_ctime_ns) ==
                     (new.st_ino, new.st_size, new.st_mtime_ns, new.st_ctime_ns), 'base changed while cloning')
            need(not running(base), 'base started while cloning')
            share = home / 'share'
            share.mkdir()
            config = dict(schema=1, vm=str(vm), port=port, identity=identity)
            for key in ('host_link_local', 'guest_interface'):
                if key in agent:
                    config[key] = agent[key]
            write(vm / 'agent.json', config)
            marker = dict(schema=1, name=name, root=str(root), base=str(base), vm=str(vm),
                          share=str(share), port=port, identity=identity, mac=mac)
            write(vm / MARKER, marker)
            registry[name] = marker
            save_records(root, registry)
        except BaseException:
            # Only the new directory created by this invocation is removed on failure.
            shutil.rmtree(home)
            raise
    return marker


def port_free(port):
    with socket.socket(socket.AF_INET6) as listener:
        try:
            listener.bind(('::', port))
            return True
        except OSError:
            return False


def remove(name, root=DEFAULT_ROOT):
    root = Path(root).resolve()
    with locked(root / '.registry.lock'):
        registry = records(root)
        need(name in registry, 'not a clone created by this tool')
        vm = Path(registry[name]['vm'])
        need(not vm.is_symlink() and not vm.parent.is_symlink(), 'symlinked clone refused')
        marker = owned(vm)
        with locked(Path(str(vm) + '.run') / 'operation.lock'), idle(vm):
            # Servers/brokers may outlive a guest; never remove their files beneath a live process.
            for entry in (Path(str(vm) + '.run')).glob('*.pid'):
                text = entry.read_text().strip()
                if text.isdigit():
                    try:
                        os.kill(int(text), 0)
                    except ProcessLookupError:
                        continue
                    raise ValueError('clone still has a live process: ' + str(entry))
            need(marker['name'] == name, 'registry name mismatch')
            shutil.rmtree(vm.parent)
            registry.pop(name)
            save_records(root, registry)


def recovery_install(config, agent_script):
    # The boot disk is the only attached disk during provisioning. Resolve its Data role,
    # never a volume name, and write only Data paths; no SSV change or host mount is needed.
    return r'''set -eu
P=/tmp/ssdvm-clone-apfs.plist
/usr/sbin/diskutil apfs list -plist > "$P"
DATA=""
for c in $(seq 0 16); do
  store=$(/usr/libexec/PlistBuddy -c "Print :Containers:$c:PhysicalStores:0:DeviceIdentifier" "$P" 2>/dev/null) || continue
  case "$store" in disk0s*) ;; *) continue ;; esac
  for v in $(seq 0 32); do
    role=$(/usr/libexec/PlistBuddy -c "Print :Containers:$c:Volumes:$v:Roles:0" "$P" 2>/dev/null) || continue
    [ "$role" = Data ] || continue
    [ -z "$DATA" ] || { echo 'ambiguous boot Data volume'; exit 1; }
    DATA=$(/usr/libexec/PlistBuddy -c "Print :Containers:$c:Volumes:$v:DeviceIdentifier" "$P")
  done
done
[ -n "$DATA" ] || { echo 'boot Data volume absent'; exit 1; }
/usr/sbin/diskutil mount "$DATA"
/usr/sbin/diskutil info -plist "$DATA" > /tmp/ssdvm-clone-data.plist
D=$(/usr/libexec/PlistBuddy -c 'Print :MountPoint' /tmp/ssdvm-clone-data.plist)
case "$D" in /Volumes/*) ;; *) echo 'unexpected Data mount'; exit 1 ;; esac
/bin/mkdir -p "$D/usr/local/ssdvm" "$D/Library/LaunchDaemons"
/bin/chmod 755 "$D/usr/local/ssdvm" "$D/Library/LaunchDaemons"
cat > "$D/usr/local/ssdvm/http-agent.sh" <<'SSDVM_CLONE_AGENT'
''' + agent_script + r'''SSDVM_CLONE_AGENT
chmod 755 "$D/usr/local/ssdvm/http-agent.sh"
cat > "$D/Library/LaunchDaemons/local.ssdvm.agent.plist" <<'SSDVM_CLONE_PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>local.ssdvm.agent</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>/usr/local/ssdvm/http-agent.sh</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>/var/log/ssdvm-http-agent.log</string>
<key>StandardErrorPath</key><string>/var/log/ssdvm-http-agent.log</string></dict></plist>
SSDVM_CLONE_PLIST
chmod 644 "$D/Library/LaunchDaemons/local.ssdvm.agent.plist"
chown root:wheel "$D/usr/local/ssdvm/http-agent.sh" "$D/Library/LaunchDaemons/local.ssdvm.agent.plist"
sync
''' + 'echo CLONE-AGENT-INSTALLED-' + config['identity'] + '\n'


# Durable Python with Pillow and cryptography for vmctl's VNC commands (build/vm/make-vmctl-venv.sh); /private/tmp is wiped on restart.
DEFAULT_VMCTL_PYTHON=Path(__file__).resolve().parents[2]/'store/cache/vmctl-venv/bin/python3'


def ctl_argv(vm,*args):
    python=Path(os.environ.get('VMCTL_PYTHON') or (DEFAULT_VMCTL_PYTHON if DEFAULT_VMCTL_PYTHON.is_file() else sys.executable))
    need(python.is_absolute() and python.is_file(),'VMCTL_PYTHON must name an existing absolute Python executable')
    return [str(python),'-I',str(HERE/'vmctl'),str(vm),*args]


def fresh_vnc(vm,started_ns):
    path=Path(vm)/'vnc.json'
    return path.is_file() and path.stat().st_mtime_ns>=started_ns


def provision(vm, marker, env):
    ctl = HERE / 'vmctl'
    subprocess.run(ctl_argv(vm)[:2]+['-c','from PIL import Image; import cryptography.hazmat.decrepit.ciphers.algorithms'],check=True,capture_output=True)
    api = runpy.run_path(str(ctl))
    config = api['agent_config'](vm)
    # A failed recovery session leaves a broker bound to its old VNC connection.
    # The exact command/PID guard in close_vnc protects other VMs.
    api['close_vnc'](vm)
    share = vm.parent / 'provision-share'
    share.mkdir(exist_ok=True)
    run = Path(str(vm) + '.run')
    with (run / 'provision.log').open('ab') as log:
        started_ns=time.time_ns()
        child = subprocess.Popen([str(Path(env['VMBIN']) / 'ssdvm'), 'run', str(vm), '--mac', marker['mac'],
                                  '--recovery', '--share', str(share)], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 60
            while not fresh_vnc(vm,started_ns):
                need(child.poll() is None and time.monotonic() < deadline, 'clone recovery VNC did not become ready')
                time.sleep(.2)
            subprocess.run(ctl_argv(vm,'up'), check=True, timeout=360, stdout=log, stderr=log)
            script = recovery_install(config, api['guest_agent'](config))
            result = subprocess.run(ctl_argv(vm,'exec',script), check=False, timeout=180,
                                    capture_output=True, text=True)
            log.write(result.stdout.encode());log.write(result.stderr.encode());log.flush()
            need(result.returncode==0,'clone recovery install failed (exit '+str(result.returncode)+'); see retained provision.log')
            need('CLONE-AGENT-INSTALLED-' + config['identity'] in result.stdout, 'clone agent install receipt absent')
            subprocess.run(ctl_argv(vm,'exec','(sleep 2; shutdown -h now) >/dev/null 2>&1 & echo stopping'),
                           check=True, timeout=30, stdout=log, stderr=log)
            child.wait(timeout=120)
            need(child.returncode == 0, 'clone recovery shutdown failed')
            api['close_vnc'](vm)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=120)
            api['close_vnc'](vm)
        # Only a successful install and clean recovery shutdown authorize full macOS startup.
        (vm / 'agent-ready').write_text(config['identity'] + '\n')


def start(vm, share, options):
    vm, share = Path(vm).resolve(), Path(share).resolve()
    need(vm.is_dir() and share.is_dir(), 'VM/share directory absent')
    with locked(Path(str(vm) + '.run') / 'operation.lock'):
        with idle(vm):
            pass
        need(not os.environ.get('VM_START_PID_FILE') or not Path(os.environ['VM_START_PID_FILE']).exists(),
             'VM_START_PID_FILE already exists')
        for pid, command in process_rows():
            match = re.search(r'(?:^|/)ssdvm run .* --share (.*?)(?: --|$)', command)
            need(not match or Path(match[1]).resolve() != share, 'share already belongs to a running VM')
        marker = owned(vm) if (vm / MARKER).exists() else None
        env = dict(os.environ, SSDVM_START_LOCKED=str(vm))
        if marker:
            need(not any(o in options for o in ('--mac', '--share')), 'clone MAC/share override refused')
            api = runpy.run_path(str(HERE / 'vmctl'))
            config = api['agent_config'](vm)
            need((config['port'], config['identity']) == (marker['port'], marker['identity']), 'clone agent identity drift')
            capability = subprocess.check_output([str(Path(env['VMBIN']) / 'ssdvm'), 'capabilities'], text=True).strip()
            need(capability == 'ssdvm-clone-mac-v1', 'compile maintained launcher with build/vm/build-ssdvm.sh first')
            if not (vm / 'agent-ready').exists() or (vm / 'agent-ready').read_text().strip() != marker['identity']:
                provision(vm, marker, env)
            options = ['--mac', marker['mac'], *options]
        return subprocess.run(['zsh', str(HERE / 'start.sh'), str(vm), str(share), *options], env=env).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for op in ('clone', 'list', 'remove-clone'):
        p = commands.add_parser(op)
        p.add_argument('--root', type=Path, default=DEFAULT_ROOT)
        if op != 'list':
            p.add_argument('name')
        if op == 'clone':
            p.add_argument('--base', type=Path, default=DEFAULT_BASE)
            p.add_argument('--port', type=int)
    p = commands.add_parser('start', help=argparse.SUPPRESS)
    p.add_argument('vm', type=Path)
    p.add_argument('share', type=Path)
    p.add_argument('options', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'clone':
        print(json.dumps(create(args.name, args.base, args.root, args.port), indent=2))
    elif args.command == 'remove-clone':
        remove(args.name, args.root)
        print('removed clone ' + args.name)
    elif args.command == 'list':
        root = args.root.resolve()
        with locked(root / '.registry.lock'):
            print(json.dumps([dict(owned(Path(item['vm'])), running_pids=running(Path(item['vm'])))
                              for item in records(root).values()], indent=2))
    else:
        return start(args.vm, args.share, args.options)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit('builder clones: ' + str(error))
