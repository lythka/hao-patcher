#!/usr/bin/python3 -I
"""walk.py ROOT OUT.tsv : one line per entry:
path type mode uid gid flags(hex) size nlink sha256|linktarget xattrnames(;)  (path relative to ROOT, '' for root -> '.')"""
import os, sys, stat, hashlib, ctypes
_libc = ctypes.CDLL(None, use_errno=True)
_libc.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
_libc.listxattr.restype = ctypes.c_ssize_t
def listxattr(p):
    b = os.fsencode(p)
    n = _libc.listxattr(b, None, 0, 1)  # XATTR_NOFOLLOW
    if n < 0: raise OSError(ctypes.get_errno(), p)
    if n == 0: return []
    buf = ctypes.create_string_buffer(n + 1)
    n = _libc.listxattr(b, buf, n + 1, 1)
    if n < 0: raise OSError(ctypes.get_errno(), p)
    return [x.decode('utf-8', 'surrogateescape') for x in buf.raw[:n].split(b'\0') if x]
from concurrent.futures import ThreadPoolExecutor

def sha(p):
    h = hashlib.sha256()
    try:
        with open(p, 'rb') as f:
            while True:
                b = f.read(1 << 20)
                if not b: break
                h.update(b)
        return h.hexdigest()
    except OSError as e:
        return 'ERR:%d' % e.errno

def entries(root):
    yield ''
    for dp, dns, fns, in ((a, b, c) for a, b, c in os.walk(root)):
        for n in sorted(dns + fns):
            yield os.path.relpath(os.path.join(dp, n), root)
        dns.sort()

def info(args):
    try: return _info(args)
    except OSError as e: return '\t'.join([args[1] or '.', 'ERR', '0', '-1', '-1', '0', '0', '0', 'ERR:%d' % e.errno, 'ERR'])

def _info(args):
    root, rel = args
    p = os.path.join(root, rel) if rel else root
    st = os.lstat(p)
    m = st.st_mode
    t = 'd' if stat.S_ISDIR(m) else 'l' if stat.S_ISLNK(m) else 'f' if stat.S_ISREG(m) else 'o'
    if t == 'f': val = sha(p)
    elif t == 'l': val = os.readlink(p)
    else: val = ''
    try: xa = ';'.join(sorted(listxattr(p)))
    except OSError: xa = 'ERR'
    return '\t'.join([rel or '.', t, '%o' % stat.S_IMODE(m), str(st.st_uid), str(st.st_gid),
                      '%x' % st.st_flags, str(st.st_size if t != 'd' else 0), str(st.st_nlink if t=='f' else 1), val, xa])

if __name__ == '__main__':
    root, out = sys.argv[1], sys.argv[2]
    items = [(root, r) for r in entries(root)]
    with open(out, 'w') as o, ThreadPoolExecutor(max_workers=16) as ex:
        for line in ex.map(info, items):
            o.write(line.replace('\n', '\\n') + '\n')
