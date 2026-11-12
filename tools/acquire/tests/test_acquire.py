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
