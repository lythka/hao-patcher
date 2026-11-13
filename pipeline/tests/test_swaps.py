"""pipeline/swaps.py: a path's newest live swap needs a resolution; hao payload bytes resolve /usr/lib/hao files."""
import hashlib, io, contextlib, os, runpy, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
S = runpy.run_path(str(TOP / 'pipeline/swaps.py'))
SHA = lambda b: hashlib.sha256(b).hexdigest()


class Swaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.ledger = self.d / 'ledger.tsv'
        self.res = self.d / 'live-swaps.tsv'
        self.res.write_text('# header\n')

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, date, path, data):
        with self.ledger.open('a') as f:
            f.write('%s\t%s\t%s\t755\t/src/x\tdev\n' % (date, path, SHA(data)))

    def test_newest_swap_of_a_path_decides(self):
        self.put('2026-11-12 10:00:00', '/usr/libexec/airportd', b'v1')
        self.put('2026-11-12 11:00:00', '/usr/libexec/airportd', b'v2')
        rows = S['status'](S['ledger'](self.ledger), S['resolutions'](self.res))
        self.assertEqual([(r['sha256'], r['swaps'], r['resolution']) for r in rows], [(SHA(b'v2'), 2, None)])
        self.res.write_text('%s\t/usr/libexec/airportd\trow tahoe_native_donors\n' % SHA(b'v1'))
        self.assertEqual(len(S['unresolved'](self.ledger, self.res, payload=self.d)), 1)    # v1's line is not v2's
        self.res.write_text('%s\t/usr/libexec/airportd\trow tahoe_native_donors\n' % SHA(b'v2'))
        self.assertEqual(S['unresolved'](self.ledger, self.res, payload=self.d), [])

    def test_hao_files_resolve_by_payload_bytes(self):
        self.put('2026-11-12 10:00:00', '/usr/lib/hao/libhao.dylib', b'hao')
        payload = self.d / 'payload'
        (payload / 'root/usr/lib/hao').mkdir(parents=True)
        (payload / 'root/usr/lib/hao/libhao.dylib').write_bytes(b'other')
        self.assertEqual(len(S['unresolved'](self.ledger, self.res, payload=payload)), 1)
        (payload / 'root/usr/lib/hao/libhao.dylib').write_bytes(b'hao')
        self.assertEqual(S['unresolved'](self.ledger, self.res, payload=payload), [])

    def test_bad_resolution_lines_refused(self):
        for line in ('abc\t/x\trow y', '%s\tx\trow y' % SHA(b''), '%s\t/x\tfixed it' % SHA(b'')):
            self.res.write_text(line + '\n')
            with self.assertRaises(ValueError):
                S['resolutions'](self.res)
        self.res.write_text('%s\t/x\tdropped: experiment only\n' % SHA(b''))
        self.assertEqual(S['resolutions'](self.res), {(SHA(b''), '/x'): 'dropped: experiment only'})

    def test_no_ledger_is_clean(self):
        self.assertEqual(S['unresolved'](self.d / 'none.tsv', self.res, payload=self.d), [])

    def test_repository_resolution_file_parses(self):
        S['resolutions']()


if __name__ == '__main__':
    unittest.main()
