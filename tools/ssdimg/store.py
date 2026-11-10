"""Content-addressed chunk store + git-tracked manifests.

Repository layout:
    image.json        image geometry (size, chunk size, sector size)   [git]
    image.manifest    "<chunk-index> <hash>" per non-zero chunk         [git]
    layout.txt        human-readable partition table                   [git]
    objects/xx/...    zstd-compressed chunks, named by BLAKE2b-160     [ignored]
    work/disk.img     sparse raw working image you attach and edit      [ignored]

A commit is just a git commit of the three small text files, so history,
branches, tags, diffs and reverts are all plain git. Chunk data shared by
any number of commits is stored once.
"""
import errno
import fcntl
import hashlib
import json
import os
import struct
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from compression import zstd

DEFAULT_CHUNK = 256 * 1024
WORKERS = min(16, os.cpu_count() or 4)
_HEX = frozenset("0123456789abcdef")
F_PUNCHHOLE = 99  # macOS fcntl


class StoreError(Exception):
    pass


def chunk_hash(data):
    return hashlib.blake2b(data, digest_size=20).hexdigest()


class Repo:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.objects = os.path.join(self.root, "objects")
        self.work = os.path.join(self.root, "work", "disk.img")
        self.meta_path = os.path.join(self.root, "image.json")
        self.manifest_path = os.path.join(self.root, "image.manifest")
        self.layout_path = os.path.join(self.root, "layout.txt")

    # ---------- discovery / init ----------
    @classmethod
    def find(cls, start=None):
        d = os.path.abspath(start or os.getcwd())
        while True:
            if os.path.exists(os.path.join(d, "image.json")) or os.path.isdir(os.path.join(d, "objects")):
                return cls(d)
            parent = os.path.dirname(d)
            if parent == d:
                raise StoreError("not inside an ssdimg repository (run `ssdimg init <dir>`)")
            d = parent

    @classmethod
    def init(cls, root, chunk_size=DEFAULT_CHUNK):
        r = cls(root)
        os.makedirs(r.objects, exist_ok=True)
        os.makedirs(os.path.dirname(r.work), exist_ok=True)
        with open(os.path.join(r.root, ".gitignore"), "w") as f:
            f.write("objects/\nwork/\n")
        if not os.path.isdir(os.path.join(r.root, ".git")):
            r.git("init", "-q")
        r.save_meta({"version": 1, "chunk_size": chunk_size, "size": 0, "sector_size": 512})
        return r

    # ---------- git ----------
    def git(self, *args, capture=False, check=True):
        cmd = ["git", "-c", f"safe.directory={self.root}", "-C", self.root, *args]
        if capture:
            return subprocess.run(cmd, check=check, capture_output=True, text=True, env=git_env()).stdout
        return subprocess.run(cmd, check=check, env=git_env())

    def git_show(self, rev, path):
        p = subprocess.run(["git", "-c", f"safe.directory={self.root}", "-C", self.root,
                            "show", f"{rev}:{path}"], capture_output=True, text=True, env=git_env())
        if p.returncode:
            raise StoreError(f"cannot read {path} at {rev}: {p.stderr.strip()}")
        return p.stdout

    # ---------- metadata ----------
    def meta(self, rev=None):
        text = self.git_show(rev, "image.json") if rev else open(self.meta_path).read()
        return json.loads(text)

    def save_meta(self, m):
        with open(self.meta_path, "w") as f:
            json.dump(m, f, indent=2)
            f.write("\n")

    def manifest(self, rev=None):
        if rev:
            text = self.git_show(rev, "image.manifest")
        elif os.path.exists(self.manifest_path):
            text = open(self.manifest_path).read()
        else:
            text = ""
        out = {}
        for line in text.splitlines():
            if line and not line.startswith("#"):
                i, h = line.split()
                out[int(i)] = h
        return out

    def save_manifest(self, man, layout=None):
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w") as f:
            for i in sorted(man):
                f.write(f"{i} {man[i]}\n")
        os.replace(tmp, self.manifest_path)
        if layout is not None:
            with open(self.layout_path, "w") as f:
                f.write(layout)

    # ---------- objects ----------
    def obj_path(self, h):
        return os.path.join(self.objects, h[:2], h[2:])

    def has(self, h):
        return os.path.exists(self.obj_path(h))

    def put(self, h, data):
        """Store a chunk unless it is already present. Returns the bytes added,
        0 if an earlier commit or a parallel worker stored it first."""
        p = self.obj_path(h)
        if os.path.exists(p):
            return 0
        os.makedirs(os.path.dirname(p), exist_ok=True)
        comp = zstd.compress(data, level=3)
        tmp = f"{p}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(comp)
        try:
            os.link(tmp, p)  # atomic, and fails if another worker won the race
        except FileExistsError:
            os.unlink(tmp)
            return 0
        os.unlink(tmp)
        return len(comp)

    def objects_on_disk(self):
        """(hash, path) of every object file in the store. Strays such as a
        Finder .DS_Store or a .tmp left by an interrupted commit are skipped."""
        for d in sorted(os.listdir(self.objects)):
            dp = os.path.join(self.objects, d)
            if len(d) != 2 or not _HEX.issuperset(d) or not os.path.isdir(dp):
                continue
            for f in os.listdir(dp):
                if len(f) == 38 and _HEX.issuperset(f):
                    yield d + f, os.path.join(dp, f)

    def get(self, h):
        try:
            with open(self.obj_path(h), "rb") as f:
                data = zstd.decompress(f.read())
        except FileNotFoundError:
            raise StoreError(f"missing object {h} (store corrupt or garbage-collected)")
        if chunk_hash(data) != h:
            raise StoreError(f"object {h} is corrupt")
        return data

    # ---------- scanning the working image ----------
    def scan(self, path=None, store=False, progress=None):
        """Hash every non-zero chunk of the working image.
        Returns (manifest, new_object_count, new_bytes)."""
        path = path or self.work
        cs = self.meta()["chunk_size"]
        size = os.path.getsize(path)
        indices = list(_data_chunks(path, size, cs))
        man = {}
        stats = {"new": 0, "bytes": 0, "done": 0}
        lock = threading.Lock()
        zero = bytes(cs)
        fd = os.open(path, os.O_RDONLY)

        def work(batch):
            local = []
            for i in batch:
                data = os.pread(fd, cs, i * cs)
                if len(data) < cs:
                    data = data.ljust(cs, b"\0")
                if data == zero:
                    continue
                h = chunk_hash(data)
                n = self.put(h, data) if store else 0
                local.append((i, h, n))
            with lock:
                for i, h, n in local:
                    man[i] = h
                    if n:
                        stats["new"] += 1
                        stats["bytes"] += n
                stats["done"] += len(batch)
                if progress:
                    progress(stats["done"], len(indices))

        try:
            batches = [indices[k:k + 64] for k in range(0, len(indices), 64)]
            with ThreadPoolExecutor(WORKERS) as ex:
                list(ex.map(work, batches))
        finally:
            os.close(fd)
        return man, stats["new"], stats["bytes"]

    # ---------- materialising a manifest ----------
    def materialize(self, man, size, path=None, current=None, progress=None, *, chunk_size=None):
        """Make `path` (default work image) contain exactly `man`.
        If `current` (the manifest the file currently holds) is given, only
        differing chunks are rewritten. Pass the revision's chunk_size when restoring
        a committed manifest whose geometry may differ from the worktree metadata."""
        path = path or self.work
        cs = chunk_size if chunk_size is not None else self.meta()["chunk_size"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fresh = current is None or not os.path.exists(path)
        if fresh:
            if os.path.exists(path):
                os.unlink(path)
            current = {}
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            os.ftruncate(fd, size)
            nchunks = (size + cs - 1) // cs
            todo = [i for i in man if current.get(i) != man[i] and i < nchunks]
            clear = [i for i in current if i not in man and i < nchunks]
            for i in clear:
                _punch(fd, i * cs, min(cs, size - i * cs))
            done = [0]
            lock = threading.Lock()

            def work(i):
                data = self.get(man[i])
                os.pwrite(fd, data[:size - i * cs], i * cs)
                with lock:
                    done[0] += 1
                    if progress:
                        progress(done[0], len(todo))

            with ThreadPoolExecutor(WORKERS) as ex:
                list(ex.map(work, todo))
            os.fsync(fd)
        finally:
            os.close(fd)
        return len(todo), len(clear)

    def reader(self, man, size):
        """read(off, n) over a manifest: chunks come from the store, holes read
        as zeros. Small LRU so metadata walks (GPT, APFS bitmaps) stay cheap."""
        cs = self.meta()["chunk_size"]
        zero = bytes(cs)
        cache = {}

        def chunk(i):
            if i not in cache:
                if len(cache) >= 256:
                    cache.pop(next(iter(cache)))
                cache[i] = self.get(man[i]) if i in man else zero
            return cache[i]

        def read(off, n):
            n = max(0, min(n, size - off))
            out = bytearray()
            while n > 0:
                i, lo = divmod(off, cs)
                take = min(n, cs - lo)
                out += chunk(i)[lo:lo + take]
                off += take
                n -= take
            return bytes(out)
        return read

    def referenced(self):
        """All object hashes referenced by any commit or the worktree."""
        refs = set(self.manifest().values())
        revs = self.git("rev-list", "--all", capture=True, check=False).split()
        for rev in revs:
            try:
                refs.update(self.manifest(rev).values())
            except StoreError:
                pass
        return refs


def git_env():
    """Under sudo, run git as the invoking user's identity (their ~/.gitconfig)."""
    env = dict(os.environ)
    user = env.get("SUDO_USER")
    if os.geteuid() == 0 and user:
        import pwd
        try:
            env["HOME"] = pwd.getpwnam(user).pw_dir
        except KeyError:
            pass
    return env


def give_back_tree(root):
    """Files created under sudo belong to the invoking user (whole tree)."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if os.geteuid() != 0 or not uid or not os.path.exists(root):
        return
    uid, gid = int(uid), int(gid)
    os.lchown(root, uid, gid)
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            try:
                os.lchown(os.path.join(d, n), uid, gid)
            except FileNotFoundError:
                pass


def _data_chunks(path, size, cs):
    """Indices of chunks that contain any allocated (non-hole) data."""
    fd = os.open(path, os.O_RDONLY)
    try:
        off = 0
        last = -1
        while off < size:
            try:
                d = os.lseek(fd, off, os.SEEK_DATA)
            except OSError as e:
                if e.errno == errno.ENXIO:
                    return
                raise
            h = os.lseek(fd, d, os.SEEK_HOLE)
            for i in range(d // cs, (h - 1) // cs + 1):
                if i > last:
                    yield i
                    last = i
            off = h
    finally:
        os.close(fd)


def _punch(fd, off, length):
    """Deallocate a range (APFS F_PUNCHHOLE); fall back to writing zeros."""
    try:
        fcntl.fcntl(fd, F_PUNCHHOLE, struct.pack("<IIqq", 0, 0, off, length))
    except OSError:
        os.pwrite(fd, bytes(length), off)


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1000


class Progress:
    def __init__(self, label):
        self.label = label
        self.last = -1

    def __call__(self, done, total):
        pct = int(done * 100 / total) if total else 100
        if pct != self.last:
            self.last = pct
            sys.stderr.write(f"\r{self.label}: {pct:3d}% ({done}/{total})")
            sys.stderr.flush()
            if done >= total:
                sys.stderr.write("\n")
