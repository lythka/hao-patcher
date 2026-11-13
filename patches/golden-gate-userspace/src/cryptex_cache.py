"""Reuse the sealed OS and App cryptex images between builds (golden-gate-userspace; install.py --cryptex-cache).

Building and sealing the two images (src/cryptex.py) is most of this recipe's time and depends only on:
- the input rows cryptex.py reads (ROWS) and the assembly's DriverKit donor row,
- the code that builds them: cryptex.py, every module of this folder it imports, and build/ (the native cache repairs
  make.sh copies in), source_files(),
- tools/tahoe-donors (install-driverkit puts Tahoe's x86_64 DriverKit cache into the image),
- the builder's tools: its macOS build, newfs_apfs, apfs_prepare_cryptex and Python (guest_lines()).
A key over all of them names one image pair. The host (pipeline/golden-run) writes the first three as `host_lines`
into SHARE/cryptex-cache/key.json and clones each stored entry whose host key matches into in/KEY/; here the guest
adds its own lines, so the key is complete. On a match the images are copied to Preboot and their sha256 checked
against the entry; anything else builds them, and the new pair goes to out/KEY/ for the host to keep in
store/cache/cryptex/KEY/. A reused image is byte for byte the one stored (a sealed image is not reproducible: each
build has its own volume UUID and seal). The outcome goes to result.json, not into the image.

  cryptex_cache.py key FOLDER       print the source files and their sha256 lines (what the host key hashes)
"""
import ast
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SCHEMA = 1
# Rows cryptex.py reads, as this recipe's inputs/NAME: the IPSW's images, hao's stubs, the code identity carrier.
ROWS = ('golden_gate_userspace', 'codeid_carrier', 'hao_runtime')
APFS_RESOURCES = '/System/Library/Filesystems/apfs.fs/Contents/Resources'
CHUNK = 1 << 20
ZERO = bytes(CHUNK)
SMALL = 1 << 20      # a folder row's top-level files up to this size (manifests, build records) count by content


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def local_imports(path, folder):
    """Names of FOLDER's modules (NAME.py) that the module at PATH imports."""
    names = set()
    for node in ast.walk(ast.parse(Path(path).read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split('.')[0])
    return {name for name in names if (Path(folder)/(name + '.py')).is_file()}


def source_files(folder):
    """The code that decides the images' bytes, relative to the recipe FOLDER (src/ in the repository, the staged hook
    folder in a build): cryptex.py, the local modules it imports (transitively), this module (it checks a reuse), and
    every file under build/."""
    folder = Path(folder)
    todo, seen = ['cryptex', 'cryptex_cache'], set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(local_imports(folder/(name + '.py'), folder) - seen)
    files = [name + '.py' for name in seen]
    build = folder/'build'
    if build.is_dir():
        for top, dirs, names in os.walk(build):
            dirs[:] = sorted(d for d in dirs if d != '__pycache__')
            files += [os.path.relpath(os.path.join(top, n), folder) for n in names
                      if not n.endswith('.pyc') and n != '.DS_Store']
    return sorted(files)


def source_lines(folder):
    return [f'source {rel} {sha256(Path(folder)/rel)}' for rel in source_files(folder)]


def folder_lines(label, path):
    """A folder row's small top-level files (its manifest and build record) by content, beside the row's identity."""
    path = Path(path)
    if not path.is_dir():
        return []
    return [f'{label} file {item.name} {sha256(item)}' for item in sorted(path.iterdir())
            if item.is_file() and not item.is_symlink() and item.stat().st_size <= SMALL]


def tree_digest(top):
    """sha256 over a folder's relative names and file contents (as pipeline/lock.py tree_digest), caches left out."""
    h = hashlib.sha256()
    for folder, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for name in sorted(n for n in files if not n.endswith('.pyc')):
            path = os.path.join(folder, name)
            body = 'link ' + os.readlink(path) if os.path.islink(path) else sha256(path)
            h.update(('%s %s\n' % (os.path.relpath(path, top), body)).encode())
    return h.hexdigest()


def host_lines(folder, rows, driverkit, donor_tool):
    """The host part of the key. FOLDER: the staged recipe; ROWS: {name: (identity, path)} for ROWS (identity as
    pipeline/lock.py key_line gives it); DRIVERKIT: (row line, path) of the assembly's DriverKit donor row;
    DONOR_TOOL: the staged tools/tahoe-donors folder."""
    lines = [f'schema golden-gate-userspace cryptex {SCHEMA}']
    for name in ROWS:
        identity, path = rows[name]
        lines.append(identity)
        lines += folder_lines(name, path)
    lines.append(driverkit[0])
    lines += folder_lines('driverkit', driverkit[1])
    lines.append(f'tool tahoe-donors {tree_digest(donor_tool)}')
    lines += source_lines(folder)
    return lines


def guest_lines():
    """The builder's tools that format, fill and seal the images."""
    build = subprocess.run(['sw_vers', '-buildVersion'], capture_output=True, text=True, check=True).stdout.strip()
    lines = [f'builder macOS {build}', f'builder python {sys.version.split()[0]}']
    for tool in ('newfs_apfs', 'apfs_prepare_cryptex'):
        lines.append(f'builder {tool} {sha256(Path(APFS_RESOURCES)/tool)}')
    return lines


def key_of(lines):
    return hashlib.sha256(('\n'.join(lines) + '\n').encode()).hexdigest()[:24]


def _data_at(fd, at, size):
    """The next data offset at or after AT (SIZE when only a hole is left); AT when holes cannot be found."""
    try:
        return os.lseek(fd, at, os.SEEK_DATA)
    except OSError as error:
        return size if error.errno == errno.ENXIO else at


def _hole_at(fd, at, size):
    try:
        return min(os.lseek(fd, at, os.SEEK_HOLE), size)
    except OSError:
        return size


def copy_file(source, target):
    """Copy SOURCE to the new file TARGET, keeping holes (the images are sparse raw images), and return
    dict(sha256, size) of the bytes read: SOURCE's full logical content, holes hashed as zeros."""
    h = hashlib.sha256()
    src = os.open(source, os.O_RDONLY)
    try:
        dst = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            size, at = os.fstat(src).st_size, 0
            while at < size:
                data = _data_at(src, at, size)
                while at < data:                                  # a hole: zeros, nothing written
                    n = min(CHUNK, data - at)
                    h.update(ZERO[:n])
                    at += n
                if at >= size:
                    break
                end = _hole_at(src, at, size)
                while at < end:
                    chunk = os.pread(src, min(CHUNK, end - at), at)
                    if not chunk:
                        raise OSError(f'{source}: short read at {at} of {size}')
                    h.update(chunk)
                    if chunk != ZERO[:len(chunk)]:
                        view, offset = memoryview(chunk), at
                        while view:
                            written = os.pwrite(dst, view, offset)
                            view, offset = view[written:], offset + written
                    at += len(chunk)
            os.ftruncate(dst, size)
        finally:
            os.close(dst)
    finally:
        os.close(src)
    return dict(sha256=h.hexdigest(), size=size)


class Cache:
    """FOLDER (SHARE/cryptex-cache): key.json from the host, in/KEY/ entries it staged, out/ for a new one."""

    def __init__(self, folder, guest=None):
        self.folder = Path(folder)
        host = json.loads((self.folder/'key.json').read_text())
        self.lines = host['host_lines'] + (guest_lines() if guest is None else guest)
        self.key = key_of(self.lines)

    def result(self, outcome, **details):
        record = dict(key=self.key, outcome=outcome, **details)
        (self.folder/'result.json').write_text(json.dumps(record, indent=2) + '\n')
        print('cryptex cache:', outcome, self.key, json.dumps(details) if details else '', flush=True)
        return record

    def restore(self, os_work, app_work, payload):
        """(counts, app) of the stored entry, its images copied to OS_WORK and APP_WORK and checked, or None (miss,
        or a stored image that does not match its sha256: then nothing is left at OS_WORK/APP_WORK)."""
        entry_dir = self.folder/'in'/self.key
        if not (entry_dir/'entry.json').is_file():
            self.result('miss')
            return None
        started = time.monotonic()
        entry = json.loads((entry_dir/'entry.json').read_text())
        if entry.get('schema') != SCHEMA or entry.get('key') != self.key or entry.get('lines') != self.lines:
            self.result('miss', reason='entry does not match the key lines')
            return None
        targets = {'os.dmg': Path(os_work)}
        if entry['app_from_payload']:
            subprocess.run(['cp', str(Path(payload)/'app.dmg'), str(app_work)], check=True)
        else:
            targets['app.dmg'] = Path(app_work)
        for name, target in targets.items():
            got = copy_file(entry_dir/name, target)
            if got != entry['files'][name]:
                for path in (Path(os_work), Path(app_work)):
                    if path.exists():
                        path.unlink()
                self.result('corrupt', file=name, want=entry['files'][name], got=got)
                return None
        self.result('hit', seconds=round(time.monotonic() - started, 1), files=entry['files'])
        return entry['counts'], entry['app']

    def store(self, os_work, app_work, counts, app):
        """Put the images just built into out/KEY/ with their record (the host moves it into store/cache)."""
        started = time.monotonic()
        out = self.folder/'out'
        out.mkdir(mode=0o755, exist_ok=True)
        partial = out/('.partial-' + self.key)
        partial.mkdir(mode=0o755)
        app_from_payload = app.get('image') == "Apple's app.dmg"
        files = {'os.dmg': copy_file(os_work, partial/'os.dmg')}
        if not app_from_payload:
            files['app.dmg'] = copy_file(app_work, partial/'app.dmg')
        entry = dict(schema=SCHEMA, key=self.key, lines=self.lines, files=files, app_from_payload=app_from_payload,
                     counts=json.loads(json.dumps(counts)), app=json.loads(json.dumps(app)))
        (partial/'entry.json').write_text(json.dumps(entry, indent=2) + '\n')
        os.rename(partial, out/self.key)
        self.result('stored', seconds=round(time.monotonic() - started, 1), files=files)


def main():
    if len(sys.argv) != 3 or sys.argv[1] != 'key':
        sys.exit(__doc__)
    print('\n'.join(source_lines(sys.argv[2])))


if __name__ == '__main__':
    main()
