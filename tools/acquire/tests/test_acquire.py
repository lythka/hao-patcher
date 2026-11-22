"""acquire: pinned downloads from a local HTTP server; reuse, resume, mirrors, refusals.
python3.14 -I -B -m unittest discover -s tools/acquire/tests"""
import contextlib
import functools
import hashlib
import io
import http.server
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

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

    def test_a_digest_that_is_not_hex_is_refused_before_anything_is_written(self):
        target = Path(tempfile.mkdtemp()) / 'x'
        path = str(target).ljust(64, 'x')[:64]
        cache = self.cache / 'c'
        m = self.cache / 'm.json'
        for bad in (dict(sha256='/' + path[1:]), dict(sha256='../' + 'a' * 61), dict(sha256='A' * 64),
                    dict(sha256='a' * 63 + '\n'), dict(name='../x'), dict(url='ftp://127.0.0.1/x'),
                    dict(mirrors=['data:,x'])):
            m.write_text(json.dumps({'downloads': [self.row(**bad)]}))
            with self.assertRaises(A['AcquireError']):
                A['load'](m)
            with self.assertRaises(A['AcquireError']):
                A['fetch']([self.row(**bad)], cache)
            self.assertFalse(cache.exists(), bad)
        self.assertEqual(list(target.parent.iterdir()), [])
        self.assertEqual(Handler.hits, [])

    def test_a_partial_that_is_a_link_is_refused_and_its_target_untouched(self):
        victim = Path(tempfile.mkdtemp()) / 'victim'
        victim.write_bytes(b'victim')
        partial = self.cache / 'partial' / self.row()['sha256']
        partial.parent.mkdir()
        partial.symlink_to(victim)
        with self.assertRaisesRegex(A['AcquireError'], 'no source gave the pinned file'):
            A['fetch']([self.row()], self.cache)
        self.assertEqual(victim.read_bytes(), b'victim')
        self.assertTrue(partial.is_symlink())
        partial.unlink()
        partial.hardlink_to(victim)                     # a second name for another file: refused too
        with self.assertRaisesRegex(A['AcquireError'], 'not a regular file owned'):
            A['fetch']([self.row()], self.cache)
        self.assertEqual(victim.read_bytes(), b'victim')
        self.assertEqual(Handler.hits, [])

    def test_a_cached_file_that_is_a_link_is_not_reused(self):
        elsewhere = Path(tempfile.mkdtemp()) / 'f'
        elsewhere.write_bytes(self.data)
        final = A['final_path'](self.cache, self.row())
        final.parent.mkdir()
        final.symlink_to(elsewhere)
        self.assertFalse(A['intact'](self.cache, self.row()))
        with self.assertRaisesRegex(A['AcquireError'], 'a link or not a file'):
            A['fetch']([self.row()], self.cache)

    def test_a_cache_writable_by_others_is_refused(self):
        for where in ('', 'partial', 'sha256'):
            cache = Path(tempfile.mkdtemp())
            (cache / where).mkdir(exist_ok=True)
            (cache / where).chmod(0o775)
            with self.assertRaisesRegex(A['AcquireError'], 'not writable by group or others'):
                A['fetch']([self.row()], cache)
        link = Path(tempfile.mkdtemp())
        (link / 'partial').symlink_to(tempfile.mkdtemp())
        with self.assertRaisesRegex(A['AcquireError'], 'not a link'):
            A['fetch']([self.row()], link)
        self.assertEqual(Handler.hits, [])

    def test_wrong_bytes_leave_no_partial(self):
        for row in (self.row(size=1000), self.row(url=self.base + '/wrong.bin'), self.row(url=self.base + '/no.bin')):
            with self.assertRaises(A['AcquireError']):
                A['fetch']([row], self.cache)
            self.assertEqual(list((self.cache / 'partial').iterdir()), [])
            self.assertEqual(list((self.cache / 'sha256').iterdir()), [])

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
        self.assertTrue(shim['mirrors'][0].endswith('/external/shim.tar'))   # an asset another repository hosts
        self.assertEqual(shim['mirrors'][1:], [asset['url']])       # its own URL only when the folder lacks it
        events = []
        A['fetch'](rows, self.cache, on_event=events.append)
        self.assertNotIn('mirror-failed', [e['ev'] for e in events])
        self.assertEqual(A['final_path'](self.cache, shim).read_bytes(), (stage / 'shim.tar').read_bytes())
        self.assertEqual(Handler.hits, ['/installer.bin'])           # the asset never left the folder

    def test_candidate_missing_or_corrupt_asset_never_tries_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            manifest, asset = self.release(stage)
            release = json.loads(manifest.read_text())
            release['candidate'] = True
            manifest.write_text(json.dumps(release))
            rows = A['load'](manifest)
            self.assertEqual(rows[1]['mirrors'], [])
            for data in (None, b'bad bytes'):
                (stage/'shim.tar').unlink()
                if data is not None:
                    (stage/'shim.tar').write_bytes(data)
                with self.assertRaises(A['AcquireError']):
                    A['fetch'](rows[1:], self.cache)
                self.assertFalse(A['intact'](self.cache, rows[1]))
                self.assertEqual(Handler.hits, [])
                if data is None:
                    (stage/'shim.tar').write_bytes(b'temporary')

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


class CertificateStore(unittest.TestCase):
    """An empty default store (python.org's Python, certificates not installed): macOS's own bundle is loaded."""

    class Context:
        def __init__(self, certs):
            self.certs, self.loaded = certs, []

        def get_ca_certs(self):
            return self.certs

        def load_verify_locations(self, cafile=None):
            self.loaded.append(cafile)

    def test_an_empty_store_gets_the_system_bundle(self):
        system = Path(tempfile.mkdtemp()) / 'cert.pem'
        system.write_text('x')
        env = {k: v for k, v in os.environ.items() if k != 'SSL_CERT_FILE'}
        for certs, extra, want in (([], {}, [str(system)]), ([{'subject': 'x'}], {}, []),
                                   ([], {'SSL_CERT_FILE': '/mine.pem'}, [])):
            ctx = self.Context(certs)
            with mock.patch.dict(os.environ, dict(env, **extra), clear=True):
                self.assertIs(A['https_context'](lambda: ctx, str(system)), ctx)
            self.assertEqual(ctx.loaded, want, (certs, extra))
        ctx = self.Context([])
        with mock.patch.dict(os.environ, env, clear=True):
            A['https_context'](lambda: ctx, str(system) + '.missing')
        self.assertEqual(ctx.loaded, [])

    def test_downloads_use_that_context(self):
        made = []
        real = A['https_context']
        A['opener'].__globals__['https_context'] = lambda: made.append(1) or real()
        try:
            o = A['opener'](direct=True)
        finally:
            A['opener'].__globals__['https_context'] = real
        self.assertEqual(made, [1])
        kinds = [type(h).__name__ for h in o.handlers]
        self.assertIn('HTTPSHandler', kinds)
        self.assertNotIn('ProxyHandler', kinds)                    # direct: no proxy handler at all


class Mirror(unittest.TestCase):
    """HAO_MIRROR: URL/sha256/HEX first, then the origin; HAO_MIRROR_STRICT=1 never falls back."""
    setUpClass, tearDownClass = Acquire.__dict__['setUpClass'], Acquire.__dict__['tearDownClass']
    setUp, row = Acquire.setUp, Acquire.row

    def env(self, strict=False, **more):
        e = {k: v for k, v in os.environ.items() if not k.startswith('HAO_MIRROR')}   # proxies as they are (ci: no_proxy)
        e.pop('HAO_MIRROR_STRICT', None)
        e.update(HAO_MIRROR=self.base + '/mirror/', **more)
        if strict:
            e['HAO_MIRROR_STRICT'] = '1'
        return mock.patch.dict(os.environ, e, clear=True)

    def served(self, data, content=None):
        """A row for DATA whose mirror copy (www/HEX) holds CONTENT (DATA when not given)."""
        hexd = hashlib.sha256(data).hexdigest()
        (self.www / hexd).write_bytes(data if content is None else content)
        self.addCleanup((self.www / hexd).unlink)
        return self.row(name='served', url=self.base + '/origin-missing.bin', size=len(data), sha256=hexd)

    def fetch(self, rows, **kw):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = A['fetch'](rows, self.cache, **kw)
        return got, err.getvalue()

    def test_mirror_is_tried_first_and_hit(self):
        data = b'from the mirror ' * 4000
        with self.env():
            got, err = self.fetch([self.served(data)])
        self.assertEqual(Path(got['served']).read_bytes(), data)
        self.assertEqual((Handler.hits, err), (['/mirror/sha256/' + hashlib.sha256(data).hexdigest()], ''))

    def test_candidate_python_from_hash_mirror_without_staged_copy(self):
        data = b'candidate Python'
        row = self.served(data)
        row['url'] = 'https://github.com/lythka/hao-assets/releases/download/unpublished/python.tar.gz'
        with tempfile.TemporaryDirectory() as folder, self.env(strict=True):
            manifest = Path(folder)/'release.json'
            manifest.write_text(json.dumps(dict(candidate=True, downloads=[row])))
            got, err = self.fetch(A['load'](manifest))
        self.assertEqual(Path(got['served']).read_bytes(), data)
        self.assertEqual(Handler.hits, ['/mirror/sha256/'+row['sha256']])
        self.assertEqual(err, '')

    def test_a_miss_falls_back_with_exactly_one_line(self):
        row = self.row()
        with self.env():
            got, err = self.fetch([row])
        self.assertEqual(Path(got['installer']).read_bytes(), self.data)
        self.assertEqual(err, 'mirror miss installer %s\n' % row['sha256'])
        self.assertEqual(Handler.hits, ['/mirror/sha256/' + row['sha256'], '/installer.bin'])

    def test_a_short_mirror_copy_is_a_miss(self):
        data = b'short on the mirror ' * 3000
        row = dict(self.served(data, data[:100]), url=self.base + '/short.bin')
        (self.www / 'short.bin').write_bytes(data)
        self.addCleanup((self.www / 'short.bin').unlink)
        with self.env():
            got, err = self.fetch([row])
        self.assertEqual((Path(got['served']).read_bytes(), err), (data, 'mirror miss served %s\n' % row['sha256']))

    def test_a_strict_miss_fails_and_writes_nothing(self):
        row = self.row()
        with self.env(strict=True):
            with self.assertRaisesRegex(A['AcquireError'], 'installer \\(sha256 %s\\).*HAO_MIRROR_STRICT=1'
                                        % row['sha256']):
                self.fetch([row])
        self.assertEqual(Handler.hits, ['/mirror/sha256/' + row['sha256']])
        self.assertEqual([p for p in self.cache.rglob('*') if p.is_file()], [])

    def test_wrong_bytes_from_the_mirror_are_refused_by_the_hash(self):
        data = b'pinned bytes ' * 3000
        row = self.served(data, bytes(len(data)))            # same size, other bytes
        events = []
        with self.env(strict=True):
            with self.assertRaisesRegex(A['AcquireError'], 'wrong bytes from the mirror'):
                self.fetch([row], on_event=events.append)
        self.assertEqual([p for p in self.cache.rglob('*') if p.is_file()], [])
        row = dict(row, url=self.base + '/good.bin')         # without strict: refused, then the origin
        (self.www / 'good.bin').write_bytes(data)
        self.addCleanup((self.www / 'good.bin').unlink)
        with self.env():
            got, err = self.fetch([row], on_event=events.append)
        self.assertEqual(Path(got['served']).read_bytes(), data)
        self.assertNotIn('mirror miss', err)
        self.assertIn('mirror-failed', [e['ev'] for e in events])

    def test_no_proxy_applies_to_the_mirror(self):
        """A process whose environment names a dead proxy (as pipeline/ci sets them): the origin fails through it,
        the mirror is reached directly. A fresh process: urlopen keeps the proxies it saw first."""
        data = b'past the proxy ' * 3000
        row = self.served(data)
        m = self.cache / 'release.json'
        dead = 'http://127.0.0.1:9'
        env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower() and not k.startswith('HAO_MIRROR')}
        env.update(http_proxy=dead, HTTP_PROXY=dead, https_proxy=dead, HTTPS_PROXY=dead, all_proxy=dead)
        run = lambda env, cache: subprocess.run([sys.executable, '-I', str(TOOL), 'fetch', str(m), '--cache',
                                                 str(cache), '--quiet'], capture_output=True, text=True, env=env)
        m.write_text(json.dumps({'downloads': [dict(row, url=self.base + '/installer.bin', sha256=self.row()['sha256'],
                                                    size=len(self.data))]}))
        self.assertEqual(run(env, Path(tempfile.mkdtemp()) / 'c').returncode, 1)     # the proxy does apply
        m.write_text(json.dumps({'downloads': [row]}))
        r = run(dict(env, HAO_MIRROR=self.base + '/mirror', HAO_MIRROR_STRICT='1'), self.cache)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(A['final_path'](self.cache, row).read_bytes(), data)

    def test_an_ipv6_mirror_is_refused(self):
        with mock.patch.dict(os.environ, {'HAO_MIRROR': 'http://[fe80::1%25en0]:8080'}):
            with self.assertRaisesRegex(A['AcquireError'], 'IPv4'):
                A['fetch']([self.row()], self.cache)

    def test_command_line_prints_one_miss_line(self):
        m = self.cache / 'release.json'
        m.write_text(json.dumps({'downloads': [self.row()]}))
        env = dict(os.environ, HAO_MIRROR=self.base + '/mirror')
        env.pop('HAO_MIRROR_STRICT', None)
        r = subprocess.run([sys.executable, '-I', str(TOOL), 'fetch', str(m), '--cache', str(self.cache), '--quiet'],
                           capture_output=True, text=True, env=env)
        self.assertEqual((r.returncode, r.stderr), (0, 'mirror miss installer %s\n' % self.row()['sha256']))


if __name__ == '__main__':
    unittest.main()
