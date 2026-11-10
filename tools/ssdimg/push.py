"""Stream images to / from a target Mac's internal disk through ssdpush-agent, from a Recovery it loads over the network.

    ssdimg push TARGET [--rev R] [--delta | --full] [--no-verify] [--yes]
    ssdimg verify TARGET [--rev R]
    ssdimg pull TARGET [--force]

TARGET is `rec@ADDR:/dev/diskN`: a macOS Recovery on the target Mac answering root SSH at ADDR, and the target's
internal disk in it. `local:/path/to/file` runs the agent locally
against a pre-sized file (tests).

push sends each data chunk exactly as stored (zstd) with its BLAKE2b hash; the agent decompresses, checks the hash,
writes, and reads back to verify. Unallocated space is skipped; allocated all-zero chunks are sent as zero frames
(TRIM). By default the agent first hashes what the target already holds and only differing chunks are sent. Every
allocated chunk and the relocated GPT are verified after writing, including skipped chunks. --full sends all
allocated chunks without the initial comparison. Afterwards OpenCore's launcher is set Disabled, so the firmware's
default (NetBoot) stays first, and the APFS container is grown to the disk.

pull is `capture` over the link: the allocation map is read remotely, then only the allocated chunks are copied into
the working image (commit afterwards as usual).
"""
import collections
import os
import shlex
import struct
import subprocess
import time

from . import gpt
from .store import Progress, human, chunk_hash

HERE = os.path.dirname(os.path.abspath(__file__))
SSDPUSH = os.path.join(os.path.dirname(HERE), "ssdpush")       # tools/ssdpush: source + build.sh (binaries untracked)
LOCAL_AGENT = os.path.join(SSDPUSH, "ssdpush-agent-macos")
REC_AGENT = os.path.join(SSDPUSH, "ssdpush-agent-macos-x86_64")
REC_TOOLS = "/usr/local/ssdtools/bin"              # the Recovery's tools: ssdpush-agent, oc-launcher
REC_BUILTIN_AGENT = REC_TOOLS + "/ssdpush-agent"
REC_RUN_AGENT = "/private/var/tmp/ssdpush-agent"   # RAM (tmpfs) in the Recovery
TRANSPORT = os.environ.get("SSDIMG_TRANSPORT", "tcp")   # tcp: frames over a direct TCP connection (ssh starts
                                                        # the agent); ssh: frames through the ssh session
# root on the target's Recovery with ~/.ssh/mbp92_test; SSDIMG_TEST_KEY names another key
REC_SSH = ["-i", os.path.expanduser(os.environ.get("SSDIMG_TEST_KEY", "~/.ssh/mbp92_test")),
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=2",
           "-o", "ServerAliveCountMax=3"]   # a rebooted peer is noticed in ~6 s
MAX_READ = 8 << 20


class PushError(Exception):
    pass


def parse_target(spec):
    """-> (kind, addr, path): ("rec", ADDR, /dev/diskN) or ("local", None, path)"""
    if spec.startswith("local:"):
        return "local", None, spec[6:]
    head, sep, path = spec.rpartition(":")
    if not head.startswith("rec@") or len(head) == 4:
        raise PushError(f"target must be rec@ADDR:/dev/diskN (the Recovery loaded via NetBoot; your Recovery reports "
                        f"ADDR) or local:/path (got {spec!r})")
    path = path.replace("/dev/rdisk", "/dev/disk", 1)
    if not path.startswith("/dev/disk"):
        raise PushError(f"target must be rec@ADDR:/dev/diskN (got {spec!r})")
    return "rec", head[4:], path


def rec_ssh(addr, cmd, check=True, stdin=None):
    """Run cmd as root in the target's Recovery."""
    r = subprocess.run(["ssh", *REC_SSH, f"root@{addr}", cmd], capture_output=True, input=stdin,
                       text=stdin is None)
    if check and r.returncode:
        err = r.stderr if stdin is None else r.stderr.decode(errors="replace")
        raise PushError(f"Recovery ssh {addr} {cmd[:60]!r} failed: {err.strip()}")
    return r.stdout


def rec_disk_info(addr, dev):
    """diskutil facts for a whole disk in the Recovery."""
    import plistlib
    info = plistlib.loads(rec_ssh(addr, f"diskutil info -plist {shlex.quote(dev)}").encode())
    me = info["DeviceIdentifier"]
    # APFS volumes are mounted from a synthesized disk (disk3s1 ...) whose physical store is a partition of me
    owned = [me] + [c["ContainerReference"] for c in
                     plistlib.loads(rec_ssh(addr, "diskutil apfs list -plist").encode()).get("Containers", [])
                     if any(p["DeviceIdentifier"].startswith(me + "s") for p in c.get("PhysicalStores", []))]
    mounts = [l.split(" on ")[1].split(" (")[0] for l in rec_ssh(addr, "mount").splitlines()
              if any(l.startswith(f"/dev/{d}s") for d in owned)]
    return {"NAME": info["DeviceIdentifier"], "SIZE": str(info.get("TotalSize") or info.get("Size")),
            "MODEL": info.get("MediaName", "?"), "TRAN": info.get("BusProtocol", "?").lower(),
            "TYPE": "disk" if info.get("WholeDisk") else "part", "LOG-SEC": str(info.get("DeviceBlockSize")),
            "Internal": info.get("Internal"), "Virtual": info.get("VirtualOrPhysical") == "Virtual",
            "Removable": info.get("Removable") or info.get("RemovableMedia"), "mounts": mounts}


def rec_agent(addr):
    """Path of an agent in the Recovery identical to this host's x86_64 build (md5: the Recovery has no sha256
    tool), uploading it to RAM when the Recovery's own copy differs. Without a host build (tools/ssdpush/build.sh
    not run), the Recovery's own copy."""
    import hashlib
    if not os.path.isfile(REC_AGENT):
        if rec_ssh(addr, f"[ -x {REC_BUILTIN_AGENT} ] && echo yes", check=False).strip() != "yes":
            raise PushError(f"no {REC_AGENT} (run tools/ssdpush/build.sh) and no {REC_BUILTIN_AGENT} in the Recovery")
        print(f"agent: the Recovery's own {REC_BUILTIN_AGENT} (no host build at {REC_AGENT})")
        return REC_BUILTIN_AGENT
    data = open(REC_AGENT, "rb").read()
    want = hashlib.md5(data).hexdigest()
    have = rec_ssh(addr, " ".join(f"[ -x {p} ] && echo $(md5 -q {p}) {p};" for p in (REC_BUILTIN_AGENT, REC_RUN_AGENT)),
                   check=False)
    for line in have.splitlines():
        h, p = line.split()
        if h == want:
            return p
    rec_ssh(addr, f"cat > {REC_RUN_AGENT}.part && chmod 755 {REC_RUN_AGENT}.part && mv {REC_RUN_AGENT}.part {REC_RUN_AGENT}",
            stdin=data)
    if rec_ssh(addr, f"md5 -q {REC_RUN_AGENT}").strip() != want:
        raise PushError("could not install the agent in the Recovery (md5 mismatch after upload)")
    print(f"agent: installed this host's build in the Recovery:{REC_RUN_AGENT}")
    return REC_RUN_AGENT


class Link:
    """One agent session: ssh to the Recovery (or a local process) speaking the frame protocol."""

    def __init__(self, kind, host, path, image_size, chunk_size, verify, trim=False):
        self.sock = None
        self.p = None
        self.r = self.w = None
        try:
            if kind == "local":
                if not os.path.exists(LOCAL_AGENT):
                    raise PushError(f"{LOCAL_AGENT} missing; run tools/ssdpush/build.sh")
                cmd = [LOCAL_AGENT, path]
            else:
                argv, name, agent = ["ssh", *REC_SSH, f"root@{host}"], host, rec_agent(host)
                path = path.replace("/dev/disk", "/dev/rdisk", 1)   # raw device: uncached, no buffer cache copy
                if TRANSPORT == "tcp":
                    try:
                        self._open_tcp(argv, name, agent, path)
                    except (PushError, OSError) as e:
                        print(f"note: no TCP data channel ({e}); using the ssh session")
                cmd = argv + [f"{agent} {shlex.quote(path)}"]
            if not self.sock:
                self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
                self.w = self.p.stdin
                self.r = self.p.stdout
            self.send(b"SSDPUSH1" + struct.pack("<QII", image_size, chunk_size, (1 if verify else 0) | (2 if trim else 0)))
            line = self.line()
            if not line.startswith("READY"):
                self.fail(line)
            f = dict(kv.split("=") for kv in line.split()[1:])
            self.size, self.bsize = int(f["size"]), int(f["bsize"])
            self.trim = f.get("trim") == "1"
        except BaseException:
            self.abort()
            raise

    def abort(self):
        """No image frames were accepted: close every transport and reap startup."""
        if self.sock is not None:
            try:
                self.sock.shutdown(2)
            except OSError:
                pass
        streams = [self.r, self.w]
        if self.p is not None:
            streams.extend([self.p.stdin, self.p.stdout, self.p.stderr])
        for stream in streams:
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if self.sock is not None:
            self.sock.close()
        if self.p is not None:
            if self.p.poll() is None:
                self.p.terminate()
            try:
                self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()

    def _open_tcp(self, argv, name, agent, path):
        """Frame stream over a direct TCP connection; ssh only starts the agent and hands it a one-time
        token (agent --tcp). Integrity does not depend on the channel: every chunk is hash-checked and
        read back by the agent."""
        import secrets
        import socket
        token = secrets.token_hex(32)
        p = subprocess.Popen(argv + [f"{agent} {shlex.quote(path)} --tcp"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        try:
            p.stdin.write(token.encode() + b"\n")
            p.stdin.flush()
            line = b""
            while not line.endswith(b"\n"):
                c = p.stdout.read(1)
                if not c:
                    raise PushError(f"agent exited (status {p.wait()})")
                line += c
            line = line.decode().strip()
            if not line.startswith("LISTEN "):
                raise PushError(line)
            port = int(line.split()[1])
            infos = sorted(socket.getaddrinfo(name, port, type=socket.SOCK_STREAM), key=lambda ai: ai[0] != socket.AF_INET6)
            scope = next((ai[4][3] for ai in infos if ai[0] == socket.AF_INET6 and ai[4][3]), 0)
            err = None
            for fam, _, _, _, sa in infos:
                s = socket.socket(fam, socket.SOCK_STREAM)
                try:
                    if fam == socket.AF_INET and scope:
                        s.setsockopt(socket.IPPROTO_IP, 25, scope)   # IP_BOUND_IF: the cable, not Wi-Fi
                    s.settimeout(3)
                    s.connect(sa)
                    s.settimeout(None)
                    break
                except OSError as e:
                    err = e
                    s.close()
                    s = None
            if s is None:
                raise PushError(f"cannot connect to {name} port {port}: {err}")
            for opt in (socket.SO_SNDBUF, socket.SO_RCVBUF):
                s.setsockopt(socket.SOL_SOCKET, opt, 4 << 20)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.sendall(token.encode())
        except BaseException:
            p.kill()
            p.wait()
            raise
        self.sock, self.p = s, p
        self.r = s.makefile("rb", buffering=0)

    def send(self, data):
        try:
            if self.sock:
                self.sock.sendall(data)
            else:
                self.w.write(data)
        except (BrokenPipeError, ConnectionResetError):
            self.fail(self.line())

    def line(self):
        out = b""
        while not out.endswith(b"\n"):
            c = self.r.read(1)
            if not c:
                err = self.p.stderr.read().decode() if self.p.stderr else ""
                raise PushError(f"agent exited (status {self.p.wait()}) {err}".strip())
            out += c
        return out.decode().strip()

    def exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            c = self.r.read(n - len(buf))
            if not c:
                raise PushError(f"agent exited during read (status {self.p.wait()})")
            buf += c
        return bytes(buf)

    def fail(self, line):
        raise PushError(f"agent: {line}")

    def close(self):
        try:
            self.send(b"X")
            if self.sock:
                self.sock.shutdown(1)          # SHUT_WR
            else:
                self.w.close()
        except (BrokenPipeError, OSError, PushError):
            pass
        rc = self.p.wait()                     # the agent exits after X; with --tcp, its ssh session too
        if self.sock:
            self.sock.close()
            self.p.stdin.close()
        if rc:
            raise PushError(f"agent exited with status {rc}")

    def read_at(self, off, n):
        """Unaligned read via aligned 'P' requests."""
        a = max(self.bsize, 4096)
        start, end = off // a * a, min((off + n + a - 1) // a * a, self.size)
        out = bytearray()
        pos = start
        while pos < end:
            ln = min(MAX_READ, end - pos)
            self.send(b"P" + struct.pack("<QI", pos, ln))
            out += self.exact(ln)
            pos += ln
        return bytes(out[off - start:off - start + n])


class RemoteReader:
    """disk.Reader-compatible: read(off, n) over a Link. After plan(ranges), reads of exactly those
    ranges, in that order, are pipelined: up to WINDOW bytes of requests stay in flight, so the agent
    reads the disk while earlier data is still on the wire."""

    WINDOW = 32 << 20

    def __init__(self, link, size):
        self.link, self.size = link, size
        self._plan = self._inflight = None
        self._bytes = 0

    def plan(self, ranges):
        self._plan = collections.deque(ranges)
        self._inflight = collections.deque()   # (off, n, start, [(pos, ln)...])
        self._bytes = 0
        self._fill()

    def _fill(self):
        while self._plan and self._bytes < self.WINDOW:
            off, n = self._plan.popleft()
            n = max(0, min(n, self.size - off))
            a = max(self.link.bsize, 4096)
            start, end = off // a * a, min((off + n + a - 1) // a * a, self.link.size)
            reqs, pos = [], start
            while pos < end:
                ln = min(MAX_READ, end - pos)
                self.link.send(b"P" + struct.pack("<QI", pos, ln))
                reqs.append((pos, ln))
                pos += ln
            self._inflight.append((off, n, start, reqs))
            self._bytes += end - start

    def __call__(self, off, n):
        n = max(0, min(n, self.size - off))
        if not self._inflight:
            if self._plan:
                raise PushError("read plan out of step")
            self._plan = self._inflight = None
            return self.link.read_at(off, n)
        poff, pn, start, reqs = self._inflight.popleft()
        if (poff, pn) != (off, n):
            raise PushError(f"pipelined read out of order: asked {off}+{n}, planned {poff}+{pn}")
        out = bytearray()
        for pos, ln in reqs:
            out += self.link.exact(ln)
        self._bytes -= sum(ln for _, ln in reqs)
        self._fill()
        return bytes(out[off - start:off - start + n])

    def close(self):
        pass


def _confirm(text, want):
    ans = input(f"type {want!r} to continue: ").strip()
    if ans != want:
        raise PushError("aborted")


def guard(kind, host, path):
    """Print what is about to be overwritten / read; refuse anything but the target's internal physical disk."""
    if kind == "local":
        print(f"target: local file {path}  {human(os.path.getsize(path))}")
        return path
    return guard_rec(host, path)


def guard_rec(addr, path):
    """Recovery target: a physical, internal, non-removable whole disk (never a USB disk, a disk image or the RAM
    disk the Recovery runs from). The Recovery mounts the internal volumes at boot, so they are unmounted first
    (`diskutil unmountDisk`); anything still mounted afterwards is refused."""
    i = rec_disk_info(addr, path)
    print(f"target: Recovery {addr}:{path}  {i['MODEL']}  {human(int(i['SIZE']))}  {i['TRAN']}  "
          f"{i['LOG-SEC']}-byte sectors")
    if i["TYPE"] != "disk":
        raise PushError(f"{path} is a partition, not a whole disk")
    if i["Virtual"] or not i["Internal"] or i["Removable"]:
        raise PushError(f"{path} is not a physical internal fixed disk (virtual={i['Virtual']} internal={i['Internal']} "
                        f"removable={i['Removable']}): refusing (a USB disk or the Recovery's own RAM disk?)")
    if i["mounts"]:
        print(f"unmounting {len(i['mounts'])} volume(s) the Recovery mounted: {', '.join(i['mounts'])}")
        rec_ssh(addr, f"diskutil unmountDisk {shlex.quote(path)}", check=False)
        i = rec_disk_info(addr, path)
        if i["mounts"]:
            raise PushError(f"{path} still has mounted volumes: {', '.join(i['mounts'])}")
    return i["NAME"]


# ---------------------------------------------------------------- push

def delta_chunks(link, man, present, cs, size):
    """Compare target bytes (agent 'Q'); -> the chunks that differ."""
    zero_hash = bytes.fromhex(chunk_hash(bytes(cs)))
    tail = (size + cs - 1) // cs - 1 if size % cs else None
    q = [i for i in sorted(present) if i != tail]
    differ = {tail} & set(present)
    for start in range(0, len(q), 4096):
        batch = q[start:start + 4096]
        body = bytearray(b"Q" + struct.pack("<I", len(batch)))
        for i in batch:
            body += struct.pack("<QI", i * cs, cs)
            body += bytes.fromhex(man[i]) if i in man else zero_hash
        link.send(bytes(body))
        count = struct.unpack("<I", link.exact(4))[0]
        if count > len(batch):
            raise PushError("invalid delta reply: mismatch count exceeds query")
        bad = struct.unpack(f"<{count}I", link.exact(4 * count)) if count else ()
        if len(set(bad)) != len(bad) or any(i >= len(batch) for i in bad):
            raise PushError("invalid delta reply: duplicate or out-of-range mismatch index")
        differ.update(batch[i] for i in bad)
    return sorted(differ)


def push(repo, man, meta, present, a):
    """`present`: required image chunks (data + allocated zero chunks)."""
    delta = getattr(a, "delta", True)
    if delta and a.no_verify:
        raise PushError("delta push requires full readback; --no-verify is only available with --full")
    kind, host, path = parse_target(a.target)
    size, cs, sector = meta["size"], meta["chunk_size"], meta["sector_size"]
    pin = guard(kind, host, path)
    if not a.yes:
        print(f"\nALL DATA ON {a.target} WILL BE DESTROYED.")
        _confirm(a.target, pin)

    mode = "none" if a.no_verify else getattr(a, "verify", None) or "end"
    link = Link(kind, host, path, size, cs, verify=mode == "inline", trim=not getattr(a, "no_trim", False))
    try:
        print(f"agent ready: target {human(link.size)}, {link.bsize}-byte sectors; verify: {mode}; "
              f"zero chunks: {'TRIM' if link.trim else 'written'}")
        if link.bsize != sector:
            raise PushError(f"target sector size {link.bsize} != image sector size {sector}")
        nchunks = (size + cs - 1) // cs
        tail = nchunks - 1 if size % cs else None

        t_start = time.time()
        todo = sorted(present)
        if delta:
            t0 = time.time()
            print(f"delta: asking the agent to hash {len(present)} allocated chunks already on the target ...")
            todo = delta_chunks(link, man, present, cs, size)
            print(f"delta: {len(todo)} of {len(present)} chunks need writing ({time.time() - t0:.0f}s)")

        sent = wire = 0
        prog = Progress("push")
        t0 = time.time()
        for n, i in enumerate(todo):
            off = i * cs
            wlen = min(cs, size - off)
            if i in man:
                h = man[i]
                with open(repo.obj_path(h), "rb") as f:
                    blob = f.read()
                frame = b"D" + struct.pack("<QI", off, wlen) + bytes.fromhex(h) + struct.pack("<I", len(blob)) + blob
            else:
                frame = b"Z" + struct.pack("<QI", off, wlen)
            link.send(frame)
            sent += wlen
            wire += len(frame)
            if n % 256 == 0:
                prog(n, len(todo))
        prog(len(todo), len(todo))

        # Backup GPT at the end of the target, primary header updated to match.
        g = gpt.GPT(repo.reader(man, size), sector)
        raw = g.relocated(link.size // sector)
        for off, data in raw:
            link.send(b"R" + struct.pack("<QI", off, len(data)) + data)
        link.send(b"F")
        ok = link.line()
        if not ok.startswith("OK"):
            link.fail(ok)
        dt = time.time() - t0
        print(f"pushed {human(sent)} ({len(todo)} chunks, {human(wire)} on the wire) in {dt:.0f}s "
              f"({human(sent / max(dt, 1e-3))}/s); agent: {ok[3:]}")
        if mode == "inline" and not delta:
            print("verify: every chunk hash-checked and read back from the target right after writing: OK")
        elif mode == "end" or delta:
            read = repo.reader(man, size)
            extra = list(raw) + ([(tail * cs, read(tail * cs, size - tail * cs))]
                                 if tail is not None and tail in present else [])
            # Recheck skipped chunks after writes too: initial equality is not final readback.
            verify_pass(link, man, present, cs, size, extra, overlay=raw, read=read)
            print(f"push + verify: {time.time() - t_start:.0f}s in total{' (with the delta check)' if delta else ''}")
    finally:
        link.close()
    if kind != "rec":
        return
    if not getattr(a, "keep_launcher", False):
        # OCLP's LauncherOption=Full makes OpenCore the firmware's first boot entry on every boot, so restarts would
        # stay in macOS; Disabled keeps the firmware's default (NetBoot) first.
        disable_launcher(host)
    if link.size > size and not getattr(a, "no_grow", False):
        # a smaller image would leave the rest of the disk unused; the Recovery grows the container in place
        out = rec_ssh(host, f"p=$(diskutil list {shlex.quote(path)} | awk '/Apple_APFS/{{print $NF; exit}}'); "
                            "[ -n \"$p\" ] && diskutil apfs resizeContainer \"$p\" 0 >/dev/null 2>&1 && "
                            "echo \"GROWN $p $(diskutil info \"$p\" | awk -F': *' '/Disk Size/{print $2}')\"",
                      check=False)
        grown = [l for l in out.splitlines() if l.startswith("GROWN ")]
        if not grown:
            raise PushError("image written and verified, but growing the APFS container failed")
        print(f"grow: APFS container {grown[0][6:]}")
    elif link.size > size:
        print(f"note: target is {human(link.size - size)} larger than the image; the APFS container "
              "keeps the image's size until grown (diskutil apfs resizeContainer <part> 0)")


def disable_launcher(addr):
    """OpenCore's launcher Disabled on the internal ESP (the Recovery's oc-launcher). A written image is not safe to
    boot if this failed: restarts would stay in it instead of returning to NetBoot."""
    try:
        result = subprocess.run(["ssh", *REC_SSH, "root@" + addr, REC_TOOLS + "/oc-launcher Disabled"],
                                capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as error:
        raise PushError("image written, launcher state unverified; do not boot: " + str(error)) from error
    message = (result.stdout or result.stderr).strip()
    if result.returncode:
        raise PushError("image written, launcher preparation failed; do not boot: " +
                        (message or str(result.returncode)))
    print(message or "launcher helper completed: Disabled")


# ---------------------------------------------------------------- verify (whole image, after the fact)

def _overlay(off, data, pieces):
    """data (read at off) with the parts of `pieces` [(off, bytes)] that fall inside it written over it"""
    out = None
    for po, pd in pieces:
        lo, hi = max(off, po), min(off + len(data), po + len(pd))
        if lo < hi:
            out = out or bytearray(data)
            out[lo - off:hi - off] = pd[lo - po:hi - po]
    return bytes(out) if out is not None else data


def verify_pass(link, man, present, cs, size, extra=(), overlay=(), read=None):
    """Re-read every chunk the image puts on the target, uncached, and compare its BLAKE2b with the
    image's (agent 'Q', 4 threads, next batch queued while one runs); `extra` = [(off, bytes)] pieces
    compared byte for byte (relocated GPT, a partial last chunk). `overlay` = the relocated GPT writes that
    went over the image's data after it (MBR, primary header and entries in chunk 0, the backup at the end
    of an equal-size disk): chunks and pieces they touch are expected with them applied (read = the
    image's reader, needed for those chunks). Raises PushError on any difference."""
    zero_hash = bytes.fromhex(chunk_hash(bytes(cs)))
    tail = (size + cs - 1) // cs - 1 if size % cs else None
    q = [i for i in sorted(present) if i != tail]
    expect = {}                                           # chunk -> hash after the GPT overlay
    for i in q:
        if any(po < (i + 1) * cs and po + len(pd) > i * cs for po, pd in overlay):
            expect[i] = bytes.fromhex(chunk_hash(_overlay(i * cs, read(i * cs, cs), overlay)))
    extra = [(off, _overlay(off, data, overlay)) for off, data in extra]
    B = 4096
    batches = [q[k:k + B] for k in range(0, len(q), B)]

    def ask(batch):
        body = bytearray(b"Q" + struct.pack("<I", len(batch)))
        for i in batch:
            body += struct.pack("<QI", i * cs, cs) + expect.get(i, bytes.fromhex(man[i]) if i in man else zero_hash)
        link.send(bytes(body))

    t0, done, bad = time.time(), 0, []
    prog = Progress("verify")
    if batches:
        ask(batches[0])
    for k, batch in enumerate(batches):
        if k + 1 < len(batches):
            ask(batches[k + 1])
        m = struct.unpack("<I", link.exact(4))[0]
        bad += [batch[j] for j in (struct.unpack(f"<{m}I", link.exact(4 * m)) if m else ())]
        done += len(batch)
        prog(done, len(q))
    bad_extra = []
    for off, data in extra:
        if link.read_at(off, len(data)) != data:
            bad_extra.append(off)
    dt = time.time() - t0
    if bad or bad_extra:
        where = ", ".join(f"chunk {i} (offset {i * cs})" for i in bad[:5]) or ", ".join(map(str, bad_extra[:5]))
        raise PushError(f"VERIFY FAILED: {len(bad)} chunks and {len(bad_extra)} other ranges differ from the image "
                        f"(first: {where})")
    print(f"verify: {len(q)} chunks ({human(len(q) * cs)}) re-read from the target and hash-checked, "
          f"{len(extra)} other ranges compared, in {dt:.0f}s ({human(len(q) * cs / max(dt, 1e-3))}/s): all match")


def verify(repo, man, meta, present, a):
    """`ssdimg verify`: read-only check that the target holds the commit."""
    kind, host, path = parse_target(a.target)
    size, cs, sector = meta["size"], meta["chunk_size"], meta["sector_size"]
    guard(kind, host, path)
    link = Link(kind, host, path, 0, cs, verify=False)
    if link.size < size:
        raise PushError(f"target ({human(link.size)}) is smaller than the image ({human(size)})")
    read = repo.reader(man, size)
    g = gpt.GPT(read, sector)
    tail = (size + cs - 1) // cs - 1 if size % cs else None
    raw = list(g.relocated(link.size // sector))
    extra = raw + ([(tail * cs, read(tail * cs, size - tail * cs))] if tail is not None and tail in present else [])
    verify_pass(link, man, present, cs, size, extra, overlay=raw, read=read)
    link.close()


# ---------------------------------------------------------------- pull

def pull_open(a):
    kind, host, path = parse_target(a.target)
    guard(kind, host, path)
    link = Link(kind, host, path, 0, 1 << 18, verify=False)
    print(f"agent ready: source {human(link.size)}, {link.bsize}-byte sectors")
    return link
