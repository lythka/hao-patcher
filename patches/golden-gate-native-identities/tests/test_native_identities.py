"""native-identities generator and installer, on fixtures made from this Mac's Apple-signed /usr/bin/true and
/usr/bin/false (their x86_64 slices). Nothing is installed outside a temporary folder; only codesign and lipo run."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE/'src'))
import native_identities as ni

TRUE, FALSE = Path('/usr/bin/true'), Path('/usr/bin/false')


def cdhash(path):
    out = subprocess.run(['codesign', '-dvvv', '-a', 'x86_64', str(path)], capture_output=True, text=True).stderr
    return next(l.split('=', 1)[1] for l in out.splitlines() if l.startswith('CDHash='))


def resign(path, identifier):
    subprocess.run(['codesign', '-f', '-s', '-', '-i', identifier, str(path)], check=True, capture_output=True)


class NativeIdentitiesTests(unittest.TestCase):
    def setUp(self):
        for program in (TRUE, FALSE):
            if 'x86_64' not in subprocess.run(['lipo', '-archs', str(program)], capture_output=True, text=True).stdout:
                self.skipTest(f'needs an Apple-signed x86_64 slice in {program}')
        self.tmp = tempfile.TemporaryDirectory(prefix='native-identities-')
        root = Path(self.tmp.name).resolve()
        self.system, self.source = root/'system', root/'source'
        for folder in (self.system/'usr/bin', self.system/'usr/lib/hao', self.source/'usr/bin'):
            folder.mkdir(parents=True)
        # re-signed ad hoc from the Apple original at the same path: a row
        shutil.copy(TRUE, self.source/'usr/bin/kept')
        shutil.copy(TRUE, self.system/'usr/bin/kept')
        resign(self.system/'usr/bin/kept', 'com.apple.true')
        # the identifier changed: not the same program
        shutil.copy(TRUE, self.source/'usr/bin/renamed')
        shutil.copy(TRUE, self.system/'usr/bin/renamed')
        resign(self.system/'usr/bin/renamed', 'com.example.renamed')
        # another program under the original's identifier at its path
        shutil.copy(TRUE, self.source/'usr/bin/other')
        (root/'other.c').write_text('static const char pad[16384] = {1};\n'
                                    'int main(int c, char **v) { return pad[c] + (v != 0); }\n')
        subprocess.run(['clang', '-arch', 'x86_64', '-O1', str(root/'other.c'), '-o', str(self.system/'usr/bin/other')],
                       check=True, capture_output=True)
        resign(self.system/'usr/bin/other', 'com.apple.true')
        # no original anywhere
        shutil.copy(FALSE, self.system/'usr/bin/orphan')
        resign(self.system/'usr/bin/orphan', 'com.apple.false')
        # still Apple-signed: not a candidate
        shutil.copy(TRUE, self.system/'usr/bin/apple')
        # listed by hao as a stub
        shutil.copy(TRUE, self.source/'usr/bin/stub')
        shutil.copy(TRUE, self.system/'usr/bin/stub')
        resign(self.system/'usr/bin/stub', 'com.apple.stub')
        (self.system/ni.STUBS).write_text(f'# test\n{cdhash(self.system/"usr/bin/stub")}  {"0" * 40}  arm64e  x\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_cdhash_matches_codesign(self):
        for path in (TRUE, self.system/'usr/bin/kept'):
            sig, _ = ni.x86_64(path)
            self.assertEqual(sig['cdhash'], cdhash(path), path)
        self.assertFalse(ni.x86_64(TRUE)[0]['adhoc'])
        self.assertTrue(ni.x86_64(self.system/'usr/bin/kept')[0]['adhoc'])

    def test_scan(self):
        rows, skipped = ni.scan(self.system, [self.source])
        self.assertEqual([(r['path'], r['observed'], r['reference']) for r in rows],
                         [('usr/bin/kept', cdhash(self.system/'usr/bin/kept'), cdhash(TRUE))])
        self.assertEqual(sorted(skipped), ['usr/bin/orphan', 'usr/bin/other', 'usr/bin/renamed', 'usr/bin/stub'])
        self.assertIn('identifier', skipped['usr/bin/renamed'])
        self.assertIn('another program', skipped['usr/bin/other'])
        self.assertIn('stub-identities', skipped['usr/bin/stub'])
        self.assertIn('no original', skipped['usr/bin/orphan'])

    def test_first_source_with_the_original_wins(self):
        empty = Path(self.tmp.name)/'empty'
        empty.mkdir()
        rows, _ = ni.scan(self.system, [empty, self.source])
        self.assertEqual([r['source'] for r in rows], [str(self.source/'usr/bin/kept')])

    def test_install(self):
        owner = (os.getuid(), os.getgid())
        record = ni.install(self.system, [self.source], owner=owner)
        copy = self.system/ni.ORIGINALS/'usr/bin/kept'
        self.assertEqual(copy.read_bytes(), TRUE.read_bytes())
        self.assertEqual(copy.stat().st_mode & 0o7777, 0o644)
        self.assertEqual((self.system/ni.ORIGINALS).stat().st_mode & 0o7777, 0o755)
        listed = (self.system/ni.LIST).read_text().splitlines()
        rows = [line for line in listed if not line.startswith('#')]
        self.assertEqual(rows, [f'{cdhash(self.system/"usr/bin/kept")}  {cdhash(TRUE)}  x86_64  '
                                f'usr/lib/hao/originals/usr/bin/kept'])
        self.assertEqual((self.system/ni.LIST).stat().st_mode & 0o7777, 0o644)
        self.assertEqual(len(record['rows']), 1)
        # an installed original is not itself scanned, so a second run gives the same list
        before = (self.system/ni.LIST).read_bytes()
        ni.install(self.system, [self.source], owner=owner)
        self.assertEqual((self.system/ni.LIST).read_bytes(), before)

    def test_original_that_fails_anchor_apple_is_dropped(self):
        # a source whose slice carries a signature blob that is not Apple's: re-signed with a CMS-less but
        # non-adhoc-looking signature is not constructible here, so check the verifier directly.
        self.assertTrue(ni.verify(TRUE)[0])
        self.assertFalse(ni.verify(self.system/'usr/bin/kept')[0])


def whole_file(path):
    """x86_64_signature's result computed the way r1-r3 did, from the whole file's bytes."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data[:4] not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        return None
    for cpu, offset, size in ni.slices(data):
        if cpu == ni.CPU_X86_64:
            return ni.signature(data, offset, size), offset, size
    return None


def whole_scan(system, sources):
    """scan() reading every candidate whole, as before the header-only read."""
    with mock.patch.object(ni, 'x86_64_signature', whole_file), \
            mock.patch.object(ni, 'slice_bytes', lambda path, found: Path(path).read_bytes()[found[1]:found[1] + found[2]]):
        return ni.scan(system, sources)


class HeaderOnlyRead(unittest.TestCase):
    """The scan reads each executable's headers and code signature only; its results are those of reading the whole
    file. With NATIVE_IDENTITIES_SYSTEM=ROOT and NATIVE_IDENTITIES_SOURCES=ROOT:ROOT (a built image's System mounted
    read-only and the sources its record names: the pristine 27.x System, the native donors' payload/system), the
    whole scan of a real System gives the same rows and skips both ways."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-identities-read-')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_signature_as_the_whole_file(self):
        thin = self.root/'thin'
        subprocess.run(['lipo', str(TRUE), '-thin', 'x86_64', '-output', str(thin)], check=True)
        adhoc = self.root/'adhoc'
        shutil.copy(TRUE, adhoc)
        resign(adhoc, 'com.example.adhoc')
        files = [TRUE, FALSE, thin, adhoc]
        data = thin.read_bytes()
        for n, cut in enumerate((16, 31, 32, 200, len(data) // 2, len(data) - 1)):
            (self.root/f'cut{n}').write_bytes(data[:cut])          # truncated: header, load commands, signature
            files.append(self.root/f'cut{n}')
        (self.root/'text').write_bytes(b'#!/bin/sh\n' * 1000)
        files.append(self.root/'text')
        for path in files:
            with self.subTest(path=path.name):
                try:
                    want = whole_file(path)
                except Exception as error:          # the whole-file read raised: the header-only read raises too
                    with self.assertRaises(type(error)):
                        ni.x86_64_signature(path)
                    continue
                self.assertEqual(ni.x86_64_signature(path), want)
                if want:
                    self.assertEqual(ni.x86_64(path)[1], path.read_bytes()[want[1]:want[1] + want[2]])

    def test_reads_headers_and_signature_only(self):
        big = self.root/'big'
        (self.root/'big.c').write_text('static const char pad[4 << 20] = {1};\n'
                                       'int main(int c, char **v) { return pad[c] + (v != 0); }\n')
        subprocess.run(['clang', '-arch', 'x86_64', '-O1', str(self.root/'big.c'), '-o', str(big)],
                       check=True, capture_output=True)
        resign(big, 'com.example.big')
        data = big.read_bytes()
        self.assertGreater(len(data), 4 << 20)
        sig = ni.x86_64_signature(big)[0]
        read = []
        real = open

        def counting(path, mode='r', *args, **kwargs):
            stream = real(path, mode, *args, **kwargs)
            original = stream.read
            stream.read = lambda n=-1: read.append(len(chunk := original(n))) or chunk
            return stream
        with mock.patch('builtins.open', counting):
            self.assertEqual(ni.x86_64_signature(big)[0], sig)
        # 4 KiB of headers, the signature blob (32 bytes of hashes per 4 KiB page, so under 1% of the file)
        self.assertLess(sum(read), 4096 + 4096 + len(data) // 100)

    def test_scan_matches_the_whole_file_scan(self):
        system, source = self.root/'system', self.root/'source'
        for folder in (system/'usr/bin', source/'usr/bin'):
            folder.mkdir(parents=True)
        shutil.copy(TRUE, source/'usr/bin/kept')
        shutil.copy(TRUE, system/'usr/bin/kept')
        resign(system/'usr/bin/kept', 'com.apple.true')
        shutil.copy(FALSE, system/'usr/bin/orphan')
        resign(system/'usr/bin/orphan', 'com.apple.false')
        (system/'usr/bin/link').symlink_to('kept')
        self.assertEqual(ni.scan(system, [source]), whole_scan(system, [source]))
        self.assertEqual(len(ni.scan(system, [source])[0]), 1)

    @unittest.skipUnless(os.environ.get('NATIVE_IDENTITIES_SYSTEM') and os.environ.get('NATIVE_IDENTITIES_SOURCES'),
                         'set NATIVE_IDENTITIES_SYSTEM and NATIVE_IDENTITIES_SOURCES (a built image, read-only)')
    def test_real_system(self):
        system = Path(os.environ['NATIVE_IDENTITIES_SYSTEM'])
        sources = [Path(p) for p in os.environ['NATIVE_IDENTITIES_SOURCES'].split(':')]
        rows, skipped = ni.scan(system, sources)
        self.assertEqual((rows, skipped), whole_scan(system, sources))
        self.assertTrue(rows)


if __name__ == '__main__':
    unittest.main()
