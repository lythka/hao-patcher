"""acquire: pinned downloads from a local HTTP server; reuse, resume, mirrors, refusals.
python3.14 -I -B -m unittest discover -s tools/acquire/tests"""
import functools
import hashlib
import http.server
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import threading
import unittest

TOOL = Path(__file__).resolve().parents[1] / 'acquire'
A = runpy.run_path(str(TOOL))


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the folder; honours one-range requests unless the path starts with /norange/."""
    hits = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Handler.hits.append(self.path)
        path = Path(self.directory) / self.path.split('/')[-1]
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        rng = self.headers.get('Range')
        if rng and not self.path.startswith('/norange/'):
            start = int(rng.split('=')[1].rstrip('-'))
            self.send_response(206)
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, len(data) - 1, len(data)))
            self.send_header('Content-Length', str(len(data) - start))
            self.end_headers()
            self.wfile.write(data[start:])
        else:
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)


class Acquire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.www = Path(tempfile.mkdtemp())
        cls.data = bytes(range(256)) * 5000                            # 1.28 MB: more than one chunk
        (cls.www / 'installer.bin').write_bytes(cls.data)
        (cls.www / 'wrong.bin').write_bytes(b'not it')
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0),
                                                     functools.partial(Handler, directory=str(cls.www)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.cache = Path(tempfile.mkdtemp())
        Handler.hits.clear()

    def row(self, **over):
        r = {'name': 'installer', 'url': self.base + '/installer.bin', 'size': len(self.data),
             'sha256': hashlib.sha256(self.data).hexdigest()}
        r.update(over)
        return r

    def test_fetch_then_reuse(self):
        got = A['fetch']([self.row()], self.cache)
        self.assertEqual(Path(got['installer']).read_bytes(), self.data)
        Handler.hits.clear()
        events = []
        A['fetch']([self.row()], self.cache, on_event=events.append)
        self.assertEqual((Handler.hits, [e['ev'] for e in events]), ([], ['reused']))

    def test_resume_from_partial(self):
        partial = self.cache / 'partial' / self.row()['sha256']
        partial.parent.mkdir(parents=True)
        partial.write_bytes(self.data[:1000])
        A['fetch']([self.row()], self.cache)
        self.assertEqual(A['final_path'](self.cache, self.row()).read_bytes(), self.data)
        self.assertFalse(partial.exists())

    def test_server_without_ranges_restarts_cleanly(self):
        partial = self.cache / 'partial' / self.row()['sha256']
        partial.parent.mkdir(parents=True)
        partial.write_bytes(self.data[:1000])
        A['fetch']([self.row(url=self.base + '/norange/installer.bin')], self.cache)
        self.assertEqual(A['final_path'](self.cache, self.row()).read_bytes(), self.data)

    def test_wrong_file_refused_then_mirror(self):
        bad = self.row(url=self.base + '/wrong.bin', mirrors=[self.base + '/installer.bin'])
        events = []
        A['fetch']([bad], self.cache, on_event=events.append)
        self.assertIn('mirror-failed', [e['ev'] for e in events])
        self.assertTrue(A['intact'](self.cache, bad))
        with self.assertRaisesRegex(A['AcquireError'], 'no source gave the pinned file'):
            A['fetch']([self.row(url=self.base + '/wrong.bin')], tempfile.mkdtemp())

    def test_missing_url_and_bad_manifest(self):
        with self.assertRaisesRegex(A['AcquireError'], 'no source gave the pinned file'):
            A['fetch']([self.row(url=self.base + '/missing.bin')], self.cache)
        m = self.cache / 'm.json'
        m.write_text(json.dumps({'downloads': [self.row(), self.row()]}))
        with self.assertRaisesRegex(A['AcquireError'], 'listed twice'):
            A['load'](m)

    def release(self, folder, **asset_over):
        """A release.json in FOLDER: one download from the server, one asset (an archive staged in FOLDER)."""
        tar = b'ours' * 1000
        (folder / 'shim.tar').write_bytes(tar)
        asset = {'row': 'shim', 'file': 'shim.tar', 'url': 'http://127.0.0.1:9/releases/shim.tar', 'size': len(tar),
                 'sha256': hashlib.sha256(tar).hexdigest(), 'commit': '0' * 40, 'format': 'tar'}
        asset.update(asset_over)
        m = folder / 'release.json'
        m.write_text(json.dumps({'downloads': [dict(self.row(), row='installer')], 'assets': [asset],
                                 'made_on_mac': [{'row': 'kernel', 'producer': 'x', 'inputs': ['installer']}]}))
        return m, asset

    def test_release_downloads_and_assets(self):
        stage = Path(tempfile.mkdtemp())
        m, asset = self.release(stage, url=self.base + '/shim.tar')
        (self.www / 'shim.tar').write_bytes((stage / 'shim.tar').read_bytes())
        rows = A['load'](m)
        self.assertEqual([(r['kind'], r['name']) for r in rows], [('download', 'installer'), ('asset', 'shim.tar')])
        got = A['fetch'](rows, self.cache)
        self.assertEqual(Path(got['shim.tar']).read_bytes(), (stage / 'shim.tar').read_bytes())
        self.assertTrue(all(A['intact'](self.cache, r) for r in rows))
        self.assertEqual(sorted(Handler.hits), ['/installer.bin', '/shim.tar'])

    def test_assets_from_a_staged_folder(self):
        stage = Path(tempfile.mkdtemp())
        m, asset = self.release(stage)
        rows = A['load'](m, assets_from=stage)
        shim = rows[1]
        self.assertTrue(shim['url'].startswith('file://') and shim['url'].endswith('/shim.tar'))
        self.assertEqual(shim['mirrors'], [asset['url']])           # its own URL only when the folder lacks it
        events = []
        A['fetch'](rows, self.cache, on_event=events.append)
        self.assertNotIn('mirror-failed', [e['ev'] for e in events])
        self.assertEqual(A['final_path'](self.cache, shim).read_bytes(), (stage / 'shim.tar').read_bytes())
        self.assertEqual(Handler.hits, ['/installer.bin'])           # the asset never left the folder

    def test_a_staged_asset_that_differs_is_refused(self):
        stage = Path(tempfile.mkdtemp())
        m, _ = self.release(stage)
        (stage / 'shim.tar').write_bytes(b'tampered')
        rows = A['load'](m, assets_from=stage)
        with self.assertRaisesRegex(A['AcquireError'], 'shim.tar: no source gave the pinned file'):
            A['fetch'](rows[1:], self.cache)
        self.assertFalse(A['intact'](self.cache, rows[1]))

    def test_an_entry_without_url_is_refused(self):
        stage = Path(tempfile.mkdtemp())
        m, _ = self.release(stage, url=None)
        with self.assertRaisesRegex(A['AcquireError'], "asset 'shim.tar' needs url"):
            A['load'](m)
        m.write_text(json.dumps({'downloads': [dict(self.row(), url=None)]}))
        with self.assertRaisesRegex(A['AcquireError'], "download 'installer' needs url"):
            A['load'](m)
        m.write_text(json.dumps({'downloads': [], 'assets': []}))
        with self.assertRaisesRegex(A['AcquireError'], 'no downloads or assets'):
            A['load'](m)

    def test_command_line_assets_from(self):
        stage = Path(tempfile.mkdtemp())
        m, _ = self.release(stage)
        run = lambda *a: subprocess.run([sys.executable, '-I', str(TOOL), *map(str, a)], capture_output=True, text=True)
        r = run('fetch', m, '--cache', self.cache, '--assets-from', stage)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(run('verify', m, '--cache', self.cache).returncode, 0)
        self.assertTrue(run('path', m, 'shim.tar', '--cache', self.cache).stdout.strip().endswith(
            hashlib.sha256((stage / 'shim.tar').read_bytes()).hexdigest()))

    def test_command_line(self):
        m = self.cache / 'release.json'
        m.write_text(json.dumps({'downloads': [self.row()]}))
        run = lambda *a: subprocess.run([sys.executable, '-I', str(TOOL), *map(str, a)], capture_output=True, text=True)
        self.assertEqual(run('verify', m, '--cache', self.cache).returncode, 1)
        r = run('fetch', m, '--cache', self.cache, '--log', 'json')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.splitlines()[-1])['ev'], 'ok')
        self.assertEqual(run('verify', m, '--cache', self.cache).returncode, 0)
        self.assertEqual(run('path', m, 'installer', '--cache', self.cache).stdout.strip(),
                         str(A['final_path'](self.cache, self.row())))


if __name__ == '__main__':
    unittest.main()
