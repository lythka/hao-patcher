"""metal-adapter: the compiler stand-in's request rewriting, built natively against src/standin.c (no compiler loaded).
python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests

Synthetic requests only, unless METAL_ADAPTER_R2_RESULTS names a laptop results folder of r2 or later (its
adapter-*/dump/ requests): then the stand-in must rebuild the laptop's own type-7 and spliced type-1 requests byte
for byte from the laptop's type-1 request and 31001 reply, with the template built from inputs.json (the r2 bundle
carried the captured one). Optional METAL_ADAPTER_TEMPLATE: the captured template, which must give the same bytes.
Needs Xcode (clang)."""
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
MAKE = runpy.run_path(str(HERE.parent / 'make.py'))


def have_clang():
    return shutil.which('xcrun') and subprocess.run(['xcrun', '--find', 'clang'], capture_output=True).returncode == 0


def request(name, module, tail=b'', header_words=None):
    """A type-1 request in 27's framing: 0x128-byte header, name at 0x128, module 8-aligned after it, then TAIL."""
    no = 0x128
    mo = (no + len(name) + 7) & ~7
    size = mo + len(module) + len(tail)
    w = [0] * (0x128 // 4)
    w[0], w[1], w[2] = 0x86000000, 0x500844, 0x128
    w[22], w[23], w[24], w[25] = no, len(name), mo, len(module)
    w[26] = mo + len(module)                     # a word pointing at the module end
    w[30] = size                                 # a word holding the total size
    for k, v in (header_words or {}).items():
        w[k] = v
    b = bytearray(struct.pack('<%dI' % len(w), *w))
    b += name + bytes(mo - no - len(name)) + module + tail
    return bytes(b)


def words(b, n=31):
    return struct.unpack_from('<%dI' % n, b)


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.h = cls.tmp / 'standin_harness'
        subprocess.run(['xcrun', 'clang', '-O1', '-Wall', '-Wno-unused-function', str(HERE / 'standin_harness.c'),
                        '-o', str(cls.h)], check=True)
        subprocess.run(['codesign', '-f', '-s', '-', str(cls.h)], check=True, capture_output=True)
        cls.template = cls.tmp / 'template.req7'
        cls.template.write_bytes(MAKE['template_bytes']())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_h(self, *args, env=None):
        out = self.tmp / 'out'
        if out.exists():
            out.unlink()
        r = subprocess.run([str(self.h), *map(str, args), str(out)], capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        return r.returncode, out.read_bytes() if out.exists() else None, r.stderr

    def put(self, name, data):
        p = self.tmp / name
        p.write_bytes(data)
        return p


@unittest.skipUnless(have_clang(), 'needs Xcode')
class Standin(Harness):
    def test_req7_carries_name_and_module(self):
        mod = bytes(range(256)) * 13 + b'\x01\x02\x03'
        req1 = self.put('a.req1', request(b'tri_vertex\0', mod, tail=b'AIRP' + bytes(396)))
        rc, q, err = self.run_h('req7', self.template, req1)
        self.assertEqual(rc, 0, err)
        w = words(q, 62)
        no, nl, mo, ms = w[22:26]
        self.assertEqual(q[no:no + nl], b'tri_vertex\0')
        self.assertEqual(q[mo:mo + ms], mod)
        self.assertEqual(mo, (no + nl + 7) & ~7)
        self.assertEqual(len(q), mo + ms)
        # the five header words that pointed at the template's module end now point at the new end
        self.assertEqual([w[k] for k in (26, 29, 33, 41, 52)], [mo + ms] * 5)
        self.assertEqual(w[55], 0xeda)

    def test_template_framing_gives_what_a_full_template_gives(self):
        # a "captured" template: the framing with a real-sized module, as r2 shipped; the request must not depend on it
        full = self.put('full.req7', self.run_h('splice', self.template, self.put('m', bytes(3264)))[1])
        req1 = self.put('b.req1', request(b'add1\0', b'\xde\xc0\x17\x0b' + bytes(3436)))
        a = self.run_h('req7', self.template, req1)[1]
        b = self.run_h('req7', full, req1)[1]
        self.assertIsNotNone(a)
        self.assertEqual(a, b)

    def test_splice_shifts_end_and_size_words(self):
        mod, new = bytes(4000), bytes(2784)
        req1 = self.put('c.req1', request(b'solid_fragment\0', mod, tail=b'T' * 400))
        rc, s, err = self.run_h('splice', req1, self.put('new', new))
        self.assertEqual(rc, 0, err)
        w = words(s)
        self.assertEqual(w[25], len(new))
        self.assertEqual(w[26], w[24] + len(new))
        self.assertEqual(w[30], len(s))
        self.assertEqual(s[-400:], b'T' * 400)
        old = words(request(b'solid_fragment\0', mod, tail=b'T' * 400))
        self.assertEqual([x for k, x in enumerate(w) if k not in (25, 26, 30)],
                         [x for k, x in enumerate(old) if k not in (25, 26, 30)])

    def test_splice_refuses_an_unknown_layout(self):
        bad = bytearray(request(b'x\0', bytes(64)))
        struct.pack_into('<I', bad, 24 * 4, 0x200)  # module not right after the name
        rc, _, err = self.run_h('splice', self.put('bad', bytes(bad)), self.put('n2', bytes(8)))
        self.assertEqual(rc, 3)
        self.assertIn('unexpected layout', err)

    def req13(self, opts):
        src = b'#include <metal_stdlib>\nkernel void k() {}\n'
        o = opts.encode() + b'\0'
        pad = (16 + len(src) + 7) & ~7
        return struct.pack('<QQ', len(src), len(o)) + src + bytes(pad - 16 - len(src)) + o

    def test_req13_options_and_module_cache(self):
        opts = ('-fmetal-math-mode=fast -fmetal-math-fp32-functions=fast -std=metal4.1 '
                '-fmodules-cache-path="/var/folders/xx/C/com.apple.metalfe" -O2')
        r = self.put('d.req13', self.req13(opts))
        rc, s, err = self.run_h('req13', r, env={'HAO_FE_MODULE_CACHE': '/tmp/run/metalfe'})
        self.assertEqual(rc, 0, err)
        sl, ol = struct.unpack_from('<QQ', s)
        oo = (16 + sl + 7) & ~7
        o = s[oo:oo + ol]
        self.assertEqual(oo + ol, len(s))
        self.assertTrue(o.endswith(b'\0'))
        self.assertIn(b'-ffast-math', o)
        self.assertIn(b'-std=metal3.0', o)
        self.assertNotIn(b'fp32-functions', o)
        self.assertIn(b'-fmodules-cache-path="/tmp/run/metalfe"', o)
        self.assertNotIn(b'/var/folders', o)

    def test_req13_without_module_cache_keeps_its_size(self):
        r = self.put('e.req13', self.req13('-std=metal4.1 -fmodules-cache-path="/x/com.apple.metalfe"'))
        rc, s, err = self.run_h('req13', r, env={'HAO_FE_MODULE_CACHE': ''})
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(s), r.stat().st_size)
        self.assertIn(b'-std=metal3.0', s)
        self.assertIn(b'/x/com.apple.metalfe', s)


@unittest.skipUnless(have_clang() and os.environ.get('METAL_ADAPTER_R2_RESULTS'), 'set METAL_ADAPTER_R2_RESULTS')
class LaptopDumps(Harness):
    """The laptop's own requests (r2 run, 2026-11-12 16:42): the repo's stand-in rebuilds them byte for byte."""
    def test_laptop_requests(self):
        res = Path(os.environ['METAL_ADAPTER_R2_RESULTS'])
        templates = [self.template] + ([Path(os.environ['METAL_ADAPTER_TEMPLATE'])] if os.environ.get('METAL_ADAPTER_TEMPLATE') else [])
        seen = 0
        for d in sorted(res.glob('adapter-*/dump')):
            for req1 in sorted(d.glob('*.req1')):
                i = req1.name.split('.')[0]
                for t in templates:
                    with self.subTest(run=d.parent.name, req=i, template=t.name):
                        self.assertEqual(self.run_h('req7', t, req1)[1], (d / f'{i}.req7').read_bytes())
                reply7 = (d / f'{i}.reply7').read_bytes()
                bo = struct.unpack_from('<I', reply7, 40)[0]
                n = struct.unpack_from('<I', reply7, bo)[0]
                mod = self.put('mod', reply7[bo + 4:bo + 4 + n])
                with self.subTest(run=d.parent.name, req=i, what='sent'):
                    self.assertEqual(self.run_h('splice', req1, mod)[1], (d / f'{i}.sent').read_bytes())
                seen += 1
        self.assertGreater(seen, 0)


if __name__ == '__main__':
    unittest.main()
