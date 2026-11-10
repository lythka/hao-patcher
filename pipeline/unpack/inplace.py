#!/usr/bin/env python3
"""inplace.py: read a macOS InstallAssistant.pkg in place and extract only selected items.

The flat package stores SharedSupport.dmg uncompressed, and that image's volume uses raw
UDIF chunks, so every byte of the asset zip inside it sits at a computable offset of the
.pkg. This reads the zip directly from the .pkg: no SharedSupport.dmg copy, no attach.

  inplace.py members PKG                         list the asset zip's members
  inplace.py cat PKG MEMBER [OUT]                copy one member (stdout or OUT)
  inplace.py payload PKG INCLUDE_LIST OUT.aa     decode the payload chunks and keep only
                                                 entries under INCLUDE_LIST (aa convert)
  inplace.py pbzx PKG MEMBER OUT                 decode one pbzx member to a file
  inplace.py verify PKG                          check SharedSupport.dmg against the TOC SHA-1
"""
import hashlib, io, lzma, os, plistlib, re, struct, subprocess, sys, zipfile
from concurrent.futures import ProcessPoolExecutor

ASSET_DIR = 'com_apple_MobileAsset_MacSoftwareUpdate'
SECTOR = 512


def xar_member(pkg, name='SharedSupport.dmg'):
    toc = subprocess.run(['/usr/bin/xar', '--dump-toc=/dev/stdout', '-f', pkg], capture_output=True, text=True, check=True).stdout
    with open(pkg, 'rb') as f:
        magic, hsize, _, tcomp, _, _ = struct.unpack('>IHHQQI', f.read(28))
    if magic != 0x78617221:
        raise SystemExit('not a xar package: ' + pkg)
    for entry in re.findall(r'<file id=.*?</file>', toc, re.S):
        if '<name>%s</name>' % name in entry:
            data = re.search(r'<data>(.*?)</data>', entry, re.S).group(1)
            enc = re.search(r'<encoding style="([^"]+)"', data).group(1)
            if enc != 'application/octet-stream':
                raise SystemExit('%s is stored with %s; in-place reading needs it uncompressed' % (name, enc))
            ext = re.search(r'<extracted-checksum style="([^"]+)">([0-9a-f]+)<', data)
            return dict(start=hsize + tcomp + int(re.search(r'<offset>(\d+)', data).group(1)),
                        length=int(re.search(r'<length>(\d+)', data).group(1)),
                        checksum=(ext.group(1), ext.group(2)) if ext else None)
    raise SystemExit('%s not found in %s' % (name, pkg))


class Partition:
    """The Apple_HFS partition of a raw-chunk UDIF image embedded at `start` in `path`."""
    def __init__(self, path, start, length):
        self.f = open(path, 'rb'); self.f.seek(start + length - 512); koly = self.f.read(512)
        if koly[:4] != b'koly':
            raise SystemExit('no UDIF trailer')
        data_fork = struct.unpack('>Q', koly[0x18:0x20])[0]
        xml_off, xml_len = struct.unpack('>QQ', koly[0xD8:0xE8])
        self.f.seek(start + xml_off); plist = plistlib.loads(self.f.read(xml_len))
        parts = [b for b in plist['resource-fork']['blkx'] if 'Apple_HFS' in b.get('Name', '')]
        if len(parts) != 1:
            raise SystemExit('expected one Apple_HFS partition, found %d' % len(parts))
        mish = parts[0]['Data']; data_off = struct.unpack('>Q', mish[24:32])[0]
        count = struct.unpack('>I', mish[200:204])[0]
        self.runs = []
        for i in range(count):
            typ, _, sec, nsec, coff, clen = struct.unpack('>IIQQQQ', mish[204 + 40 * i:244 + 40 * i])
            if typ == 0xFFFFFFFF:
                break
            if typ in (0x7FFFFFFE,):
                continue  # comment
            if typ not in (0, 1, 2):
                raise SystemExit('chunk type %#x is compressed; in-place reading needs raw chunks' % typ)
            self.runs.append((sec * SECTOR, nsec * SECTOR, typ, start + data_fork + data_off + coff))
        self.size = sum(r[1] for r in self.runs)

    def read(self, off, n):
        out = bytearray()
        for base, ln, typ, foff in self.runs:
            if n <= 0:
                break
            if off >= base + ln or off + n <= base:
                continue
            a = max(off, base); b = min(off + n, base + ln)
            if typ == 1:
                self.f.seek(foff + (a - base)); out += self.f.read(b - a)
            else:
                out += bytes(b - a)
        return bytes(out)


class HFS:
    def __init__(self, part):
        self.p = part; vh = part.read(1024, 512)
        if vh[:2] not in (b'H+', b'HX'):
            raise SystemExit('not an HFS+ volume')
        self.bs = struct.unpack('>I', vh[0x28:0x2C])[0]
        self.extents_fork = self.fork(vh[0xC0:0x110]); self.catalog_fork = self.fork(vh[0x110:0x160])

    @staticmethod
    def fork(b):
        size, _, blocks = struct.unpack('>QII', b[:16])
        ext = [struct.unpack('>II', b[16 + 8 * i:24 + 8 * i]) for i in range(8)]
        return dict(size=size, blocks=blocks, extents=[e for e in ext if e[1]])

    def fork_read(self, extents, off, n):
        out = bytearray(); pos = 0
        for start, count in extents:
            ln = count * self.bs
            if off < pos + ln and off + n > pos:
                a = max(off, pos); b = min(off + n, pos + ln)
                out += self.p.read(start * self.bs + (a - pos), b - a)
            pos += ln
        return bytes(out)

    def leaves(self, fork):
        ext = fork['extents']; head = self.fork_read(ext, 0, 512)
        node_size = struct.unpack('>H', head[32:34])[0]; leaf = struct.unpack('>I', head[24:28])[0]
        while leaf:
            node = self.fork_read(ext, leaf * node_size, node_size)
            flink, _, kind, _, nrec = struct.unpack('>IIbBH', node[:12])
            offs = [struct.unpack('>H', node[node_size - 2 * (i + 1):node_size - 2 * i])[0] for i in range(nrec)]
            for o in offs:
                yield node, o
            leaf = flink

    def catalog(self):
        for node, o in self.leaves(self.catalog_fork):
            klen, parent, nlen = struct.unpack('>HIH', node[o:o + 8])
            name = node[o + 8:o + 8 + 2 * nlen].decode('utf-16-be')
            rec = node[o + 2 + klen:]
            yield parent, name, struct.unpack('>h', rec[:2])[0], rec

    def find_file(self, folder, suffix):
        folder_id = None; files = []
        for parent, name, typ, rec in self.catalog():
            if typ == 1 and parent == 2 and name == folder:
                folder_id = struct.unpack('>I', rec[8:12])[0]
            elif typ == 2:
                files.append((parent, name, rec))
        hits = [(n, r) for p, n, r in files if p == folder_id and n.endswith(suffix)]
        if len(hits) != 1:
            raise SystemExit('expected one %s/*%s, found %d' % (folder, suffix, len(hits)))
        name, rec = hits[0]; file_id = struct.unpack('>I', rec[8:12])[0]; fork = self.fork(rec[88:168])
        have = sum(c for _, c in fork['extents'])
        if have < fork['blocks']:
            more = []
            for node, o in self.leaves(self.extents_fork):
                _, ftype, _, fid, sb = struct.unpack('>HBBII', node[o:o + 12])
                if fid == file_id and ftype == 0:
                    more.append((sb, [struct.unpack('>II', node[o + 12 + 8 * i:o + 20 + 8 * i]) for i in range(8)]))
            for _, ext in sorted(more):
                fork['extents'] += [e for e in ext if e[1]]
        if sum(c for _, c in fork['extents']) < fork['blocks']:
            raise SystemExit('incomplete extents for ' + name)
        return name, fork


class ForkFile(io.RawIOBase):
    def __init__(self, hfs, fork):
        self.h, self.fork, self.pos = hfs, fork, 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.fork['size'] + off
        return self.pos

    def readinto(self, b):
        n = min(len(b), max(0, self.fork['size'] - self.pos))
        data = self.h.fork_read(self.fork['extents'], self.pos, n); b[:len(data)] = data; self.pos += len(data)
        return len(data)


def open_zip(pkg):
    m = xar_member(pkg); hfs = HFS(Partition(pkg, m['start'], m['length']))
    name, fork = hfs.find_file(ASSET_DIR, '.zip')
    return name, zipfile.ZipFile(io.BufferedReader(ForkFile(hfs, fork), 1 << 20))


def pbzx_bytes(stream):
    if stream.read(4) != b'pbzx':
        raise SystemExit('not pbzx')
    stream.read(8); out = []
    while True:
        hdr = stream.read(16)
        if not hdr:
            break
        usize, csize = struct.unpack('>QQ', hdr); data = stream.read(csize)
        out.append(data if csize == usize else lzma.decompress(data, format=lzma.FORMAT_XZ))
    return b''.join(out)


def _decode_member(args):
    pkg, member = args
    _, z = open_zip(pkg)
    with z.open(member) as s:
        return pbzx_bytes(s)


def main():
    cmd, pkg = sys.argv[1], sys.argv[2]
    if cmd == 'verify':
        m = xar_member(pkg); algo, want = m['checksum']; h = hashlib.new(algo.replace('-', ''))
        with open(pkg, 'rb') as f:
            f.seek(m['start']); left = m['length']
            while left:
                b = f.read(min(left, 64 << 20)); h.update(b); left -= len(b)
        print('SharedSupport.dmg', algo, h.hexdigest(), 'OK' if h.hexdigest() == want else 'MISMATCH (TOC %s)' % want)
        sys.exit(0 if h.hexdigest() == want else 1)
    name, z = open_zip(pkg)
    if cmd == 'members':
        print('#', name)
        for i in z.infolist():
            print('%d\t%d\t%s' % (i.file_size, i.compress_type, i.filename))
    elif cmd == 'cat':
        out = open(sys.argv[4], 'xb') if len(sys.argv) > 4 else sys.stdout.buffer
        with z.open(sys.argv[3]) as s:
            while chunk := s.read(16 << 20):
                out.write(chunk)
    elif cmd == 'pbzx':
        with z.open(sys.argv[3]) as s:
            data = pbzx_bytes(s)
        with open(sys.argv[4], 'xb') as o:
            o.write(data)
    elif cmd == 'payload':
        members = sorted(n for n in z.namelist() if re.fullmatch(r'AssetData/payloadv2/payload\.\d{3}', n))
        conv = subprocess.Popen(['/usr/bin/aa', 'convert', '-a', 'raw', '-include-path-list', sys.argv[3],
                                 '-o', sys.argv[4]], stdin=subprocess.PIPE)
        window = 10
        with ProcessPoolExecutor(max_workers=window) as ex:
            futures = [ex.submit(_decode_member, (pkg, m)) for m in members[:window]]
            nxt = window
            for i in range(len(members)):
                conv.stdin.write(futures[i].result()); futures[i] = None
                if nxt < len(members):
                    futures.append(ex.submit(_decode_member, (pkg, members[nxt]))); nxt += 1
                print('payload chunk %d/%d' % (i + 1, len(members)), file=sys.stderr, flush=True)
        conv.stdin.close()
        if conv.wait():
            raise SystemExit('aa convert failed')
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
