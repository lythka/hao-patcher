"""The code identity carrier (src/codeid_carrier.py, src/codeid/carrier.c).

RunTests link the carrier against this Mac's own x86_64 shared cache (Rosetta's) and run it in an x86_64 process:
the linker places it in memory beside the mapped cache instead of in the cache's free tail, and the hooks are called
directly instead of through the entry jumps; the relocated prologues run Apple's real Security bodies. They need
hao-runtime's sources (HAO_RUNTIME=checkout, for the test-switch build that reads lists from a test folder).

PlanTests plan the edit of a real image cache read-only (CODEID_CACHE=folder holding dyld_shared_cache_x86_64*, e.g.
26B131's RosettaOS System/Library/dyld). Only generated test programs run.
"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE/'src'))
import codeid_carrier as carrier

HOST_CACHE = Path('/System/Cryptexes/Rosetta/System/Library/dyld')
APPLE = Path('/usr/bin/true')


def clang(*args):
    subprocess.run(['clang', '-arch', 'x86_64', '-O2', '-Wall', '-Wno-deprecated-declarations', *map(str, args)],
                   check=True, capture_output=True)


def sign(path, *extra):
    subprocess.run(['codesign', '-f', '-s', '-', *extra, str(path)], check=True, capture_output=True)


def cdhash(path, arch):
    out = subprocess.run(['codesign', '-dvvv', '-a', arch, str(path)], capture_output=True, text=True).stderr
    return next(l.split('=', 1)[1] for l in out.splitlines() if l.startswith('CDHash='))


def host_cache():
    if not (HOST_CACHE/'dyld_shared_cache_x86_64').is_file():
        raise unittest.SkipTest('needs this Mac\'s x86_64 shared cache (Rosetta)')
    return HOST_CACHE


class ShapeTests(unittest.TestCase):
    def test_prologue_takes_whole_instructions(self):
        self.assertEqual(carrier._prologue(bytes.fromhex('554889e54157415641554154')), 6)
        self.assertEqual(carrier._prologue(bytes.fromhex('554889e553')), 5)
        with self.assertRaises(carrier.Unsupported):
            carrier._prologue(bytes.fromhex('554889e5e800000000'))   # a call cannot move
        with self.assertRaises(carrier.Unsupported):
            carrier._prologue(bytes.fromhex('488d05000000004889'))   # rip-relative cannot move

    def test_archive_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'a_long_member_name.c').write_text('int a(void) { return 1; }\n')
            clang('-c', tmp/'a_long_member_name.c', '-o', tmp/'a_long_member_name.o')
            subprocess.run(['libtool', '-static', '-o', str(tmp/'lib.a'), str(tmp/'a_long_member_name.o')],
                           check=True, capture_output=True)
            members = carrier.read_archive((tmp/'lib.a').read_bytes())
            self.assertEqual([name for name, _ in members], ['a_long_member_name.o'])
            self.assertEqual(carrier.Object(*members[0]).sections[0]['section'], '__text')


class ArtifactTests(unittest.TestCase):
    def test_objects_must_match_build_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'h.c').write_text('int hao_codeid_decide(void) { return 0; }\n')
            clang('-c', tmp/'h.c', '-o', tmp/'carrier.o')
            subprocess.run(['libtool', '-static', '-o', str(tmp/'libhao_codeid.a'), str(tmp/'carrier.o')],
                           check=True, capture_output=True)
            digest = lambda p: __import__('hashlib').sha256(p.read_bytes()).hexdigest()
            (tmp/'build.json').write_text(__import__('json').dumps(dict(
                carrier_o=digest(tmp/'carrier.o'), hao_runtime=dict(libhao_codeid_a=digest(tmp/'libhao_codeid.a')))))
            self.assertEqual(len(carrier.load_objects(tmp)), 2)
            (tmp/'carrier.o').write_bytes((tmp/'carrier.o').read_bytes() + b'\0')
            with self.assertRaisesRegex(carrier.Unsupported, 'build.json'):
                carrier.load_objects(tmp)


class RefusalTests(unittest.TestCase):
    """Objects the carrier cannot place safely stop the link."""
    @classmethod
    def setUpClass(cls):
        cls.cache = carrier.Cache(host_cache())
        cls.tmp = tempfile.TemporaryDirectory(prefix='codeid-refuse-')

    @classmethod
    def tearDownClass(cls):
        cls.cache.close(); cls.tmp.cleanup()

    def link(self, source):
        tmp = Path(self.tmp.name)
        (tmp/'x.c').write_text(source)
        clang('-c', tmp/'x.c', '-o', tmp/'x.o')
        return carrier.link(self.cache, [carrier.Object('x.o', (tmp/'x.o').read_bytes())])

    def test_absolute_pointer_needs_a_rebase(self):
        with self.assertRaisesRegex(carrier.Unsupported, 'absolute pointer|hao_carrier'):
            self.link('int v = 1; int *const p = &v; int *get(void) { return p; }\n')

    def test_common_symbol(self):
        with self.assertRaisesRegex(carrier.Unsupported, 'common'):
            self.link('int v; int get(void) { return v; }\n')

    def test_unknown_symbol(self):
        with self.assertRaisesRegex(carrier.Unsupported, 'no_such_function'):
            self.link('int no_such_function(void); int f(void) { return no_such_function(); }\n')

    def test_hooks_must_be_defined(self):
        with self.assertRaisesRegex(carrier.Unsupported, 'objects do not define'):
            self.link('int f(void) { return 0; }\n')


class RunTests(unittest.TestCase):
    """The linked carrier, run: listed code passes `anchor apple`, unlisted or modified code does not."""
    @classmethod
    def setUpClass(cls):
        cache = host_cache()
        runtime = Path(os.environ.get('HAO_RUNTIME', '/nonexistent'))
        if not (runtime/'src/code_identity.c').is_file():
            raise unittest.SkipTest('set HAO_RUNTIME to a hao-runtime checkout (src/code_identity.c)')
        if 'arm64e' not in subprocess.run(['lipo', '-archs', str(APPLE)], capture_output=True, text=True).stdout:
            raise unittest.SkipTest('needs an Apple-signed arm64e slice at /usr/bin/true')
        cls.tmp = tempfile.TemporaryDirectory(prefix='codeid-run-')
        root = cls.root = Path(cls.tmp.name).resolve()
        include = ['-I', runtime/'include']
        objects = []
        for name, source in (('carrier.o', HERE/'src/codeid/carrier.c'),
                             ('code_identity.o', runtime/'src/code_identity.c'),
                             ('idlist.o', runtime/'src/idlist.c')):
            clang('-c', '-DHAO_TEST_SWITCHES', *include, source, '-o', root/name)
            objects.append(carrier.Object(name, (root/name).read_bytes()))
        cls.objects = objects
        clang('-framework', 'CoreFoundation', '-framework', 'Security', HERE/'tests/codeid_harness.c',
              '-o', root/'harness')
        sign(root/'harness')
        # static: ad hoc re-signed x86_64 copies of /usr/bin/true
        subprocess.run(['lipo', str(APPLE), '-thin', 'x86_64', '-output', str(root/'true.x86')], check=True)
        sign(root/'true.x86', '-i', 'com.apple.true')
        shutil.copy(root/'true.x86', root/'other.x86'); sign(root/'other.x86', '-i', 'com.example.other')
        data = bytearray((root/'true.x86').read_bytes())
        header = data.index(b'__text\0')                         # its first code byte, then signed again
        data[struct.unpack_from('<I', data, header + 48)[0]] ^= 0xff
        (root/'modified.x86').write_bytes(data); sign(root/'modified.x86', '-i', 'com.apple.true')
        # running: a native ad hoc program beside the host's Apple-signed arm64e /usr/bin/true slice
        subprocess.run(['lipo', str(APPLE), '-thin', 'arm64e', '-output', str(root/'apple-arm64e')], check=True)
        for variant in ('a', 'b'):
            clang(f'-DVARIANT="{variant}"', HERE/'tests/codeid_guest.c', '-o', root/f'guest-{variant}.x86')
            sign(root/f'guest-{variant}.x86')
            subprocess.run(['lipo', '-create', str(root/f'guest-{variant}.x86'), str(root/'apple-arm64e'),
                            '-output', str(root/f'guest-{variant}')], check=True)
        lists = root/'lists'
        lists.mkdir(mode=0o755)
        rel = lambda p: str(p).lstrip('/')
        (lists/'native-identities').write_text(
            '# test\n'
            f'{cdhash(root/"true.x86", "x86_64")}  {cdhash(APPLE, "x86_64")}  x86_64  {rel(APPLE)}\n'
            f'{cdhash(root/"guest-a", "x86_64")}  {cdhash(root/"guest-a", "arm64e")}  arm64e  {rel(root/"guest-a")}\n')
        (lists/'stub-identities').write_text('# test\n')
        for f in lists.iterdir():
            f.chmod(0o644)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cases(self, cases):
        root = self.root
        (root/'cases').write_text(''.join(f'{kind} {path} {req}\n' for kind, path, req in cases))
        process = subprocess.Popen([str(root/'harness'), str(root/'lists'), str(root/'cases')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        text_at, data_at = (int(v, 16) for v in process.stdout.readline().split())
        cache = carrier.Cache(HOST_CACHE)
        try:
            result = carrier.link(cache, self.objects, text=(text_at, text_at + 0x10000),
                                  data=(data_at, data_at + 0x10000))
        finally:
            cache.close()
        (root/'text.bin').write_bytes(result['text'][1])
        (root/'data.bin').write_bytes(result['data'][1])
        s = result['symbols']
        out, _ = process.communicate(f'{root/"text.bin"} {root/"data.bin"} '
                                     f'{s["_hao_carrier_SecCodeCheckValidityWithErrors"]:x} '
                                     f'{s["_hao_carrier_SecStaticCodeCheckValidityWithErrors"]:x} '
                                     f'{s["_hao_idlist_use_dir_for_test"]:x}\n', timeout=120)
        self.assertEqual(process.returncode, 0, out)
        return out.splitlines()

    def test_static_and_running(self):
        r = self.root
        aa = 'anchor apple'
        got = self.run_cases([
            ('static', r/'true.x86', aa),
            ('static', r/'true.x86', f'identifier "com.apple.true" and {aa}'),
            ('static', r/'true.x86', f'identifier "com.example.x" and {aa}'),
            ('static', r/'other.x86', aa),
            ('static', r/'modified.x86', aa),
            ('static', APPLE, aa),
            ('running', r/'guest-a', aa),
            ('running', r/'guest-a', f'identifier "com.apple.true" and {aa}'),
            ('running', r/'guest-a', f'identifier "com.example.x" and {aa}'),
            ('running', r/'guest-b', aa),
        ])
        listed, failed, untouched = 'original=-67050 carried=0', 'original=-67050 carried=-67050', \
            'original=0 carried=0'
        self.assertEqual(got, [
            listed,          # a native copy re-signed ad hoc, listed with its Apple original
            listed,          # ... and its Apple identifier
            failed,          # a requirement the original does not meet
            failed,          # unlisted ad hoc code
            failed,          # the listed copy modified and re-signed (another cdhash)
            untouched,       # Apple's own program
            listed,          # a running fat guest by audit token, listed with its Apple arm64e slice
            listed,
            failed,
            failed,          # another guest beside the same Apple slice, unlisted
        ])


class PlanTests(unittest.TestCase):
    """The edit planned for an image cache (read-only)."""
    @classmethod
    def setUpClass(cls):
        folder = os.environ.get('CODEID_CACHE')
        if not folder:
            raise unittest.SkipTest('set CODEID_CACHE to a folder holding an image\'s dyld_shared_cache_x86_64*')
        cls.folder = Path(folder)
        runtime = Path(os.environ.get('HAO_RUNTIME', '/nonexistent'))
        if not (runtime/'include/hao/codeid.h').is_file():
            raise unittest.SkipTest('set HAO_RUNTIME to a hao-runtime checkout')
        cls.tmp = tempfile.TemporaryDirectory(prefix='codeid-plan-')
        root = Path(cls.tmp.name)
        objects = []
        for name, source in (('carrier.o', HERE/'src/codeid/carrier.c'),
                             ('code_identity.o', runtime/'src/code_identity.c'), ('idlist.o', runtime/'src/idlist.c')):
            clang('-c', '-I', runtime/'include', source, '-o', root/name)
            objects.append(carrier.Object(name, (root/name).read_bytes()))
        cls.plan, cls.summary = carrier.plan_cache_family(cls.folder, objects=objects)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_edits(self):
        (path, edits), = self.plan.items()
        self.assertEqual(path.name, 'dyld_shared_cache_x86_64')
        reasons = [reason for _, _, _, reason in edits]
        self.assertEqual(reasons[:2], ['code identity carrier text', 'code identity carrier data'])
        text_offset, _, text, _ = edits[0]
        self.assertTrue(text.startswith(carrier.MARK))
        for offset, before, after, reason in edits[2:]:
            self.assertEqual(after[0], 0xe9, reason)
            self.assertEqual(len(before), len(after))
            # the jump lands inside the carrier's text (file offsets equal addresses minus one base in __TEXT)
            target = offset + 5 + struct.unpack_from('<i', after, 1)[0]
            self.assertTrue(text_offset <= target < text_offset + len(text), reason)
        self.assertEqual(set(self.summary['hooks']),
                         {'SecCodeCheckValidityWithErrors', 'SecStaticCodeCheckValidityWithErrors'})


if __name__ == '__main__':
    unittest.main()
