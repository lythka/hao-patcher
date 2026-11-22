"""src/doctor.py: each check against fakes; a wrong Python stops (the R11 acceptance)."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import doctor as D        # noqa: E402

P = D.pins()


def state(results, name):
    return [r['state'] for r in results if r['name'] == name]


def runner(xcode='Xcode 27.2\nBuild version 27C5\n', metal=True):
    def run(argv, **kw):
        if argv[0].endswith('xcodebuild'):
            return subprocess.CompletedProcess(argv, 0 if xcode else 1, xcode or '', '')
        if argv[0].endswith('xcrun'):
            return subprocess.CompletedProcess(argv, 0 if metal else 1, '', '')
        raise OSError('unexpected %s' % argv)
    return run


class Python(unittest.TestCase):
    def test_the_root_python_rule_applies_only_to_a_root_run(self):
        from unittest import mock
        with mock.patch.object(D, 'check_interpreter', lambda: [D.check('python', 'fail', 'root rule')]):
            with mock.patch.object(D.os, 'geteuid', lambda: 501):
                self.assertFalse([x for x in D.checks(offline=True) if 'root rule' in str(x)])
            with mock.patch.object(D.os, 'geteuid', lambda: 0):
                self.assertTrue([x for x in D.checks(offline=True) if 'root rule' in str(x)])

    def test_wrong_python_on_a_build_mac_fails(self):
        r = D.check_python('build', P, version=(3, 12, 14), which=lambda n: '/x/' + n)
        self.assertEqual(state(r, 'python'), ['fail', 'ok'])       # the interpreter, then what the pipeline runs
        self.assertIn('pipeline/ runs /x/python3.14', r[1]['detail'])
        self.assertIn('needs 3.14', r[0]['detail'])

    def test_kit_python_is_right_on_the_usb_installer(self):
        self.assertEqual(state(D.check_python('installer', P, version=(3, 12, 14)), 'python'), ['ok'])

    def test_python314_missing_from_path(self):
        r = D.check_python('build', P, version=(3, 14, 7), which=lambda n: None)
        self.assertEqual(state(r, 'python'), ['ok', 'fail'])


class Xcode(unittest.TestCase):
    def test_needed_for_create_installer_not_for_ci(self):
        self.assertEqual(state(D.check_xcode('create-installer', P, runner(xcode='')), 'xcode'), ['fail'])
        self.assertEqual(state(D.check_xcode('ci', P, runner(xcode='')), 'xcode'), ['warn'])

    def test_version_matters_for_a_release_proof(self):
        run = runner(xcode='Xcode 26.4\n')
        self.assertEqual(state(D.check_xcode('release', P, run), 'xcode'), ['fail'])
        self.assertEqual(state(D.check_xcode('create-installer', P, run), 'xcode'), ['warn'])

    def test_no_metal_toolchain(self):
        self.assertEqual(state(D.check_xcode('create-installer', P, runner(metal=False)), 'xcode'), ['ok', 'fail'])


class Host(unittest.TestCase):
    def test_older_macos_is_reported_not_refused(self):
        self.assertEqual(state(D.check_macos(P, mac='15.6'), 'macos'), ['warn'])
        self.assertEqual(state(D.check_macos(P, mac='26.7'), 'macos'), ['ok'])

    def test_disk_against_the_release_peak(self):
        with tempfile.TemporaryDirectory() as d:
            huge = {'downloads': [{'name': 'x', 'size': 10 ** 16, 'sha256': '0' * 64, 'url': 'https://x'}]}
            self.assertEqual(state(D.check_disk(huge, d), 'disk'), ['fail'])
            saved = D.shutil.disk_usage
            D.shutil.disk_usage = lambda path: saved(path)._replace(free=10 ** 13)   # 10 TB free, whatever this Mac has
            try:
                self.assertEqual(state(D.check_disk({'downloads': []}, d), 'disk'), ['ok'])
            finally:
                D.shutil.disk_usage = saved


class Downloads(unittest.TestCase):
    def test_only_candidate_hosted_urls_are_exempt(self):
        from unittest import mock
        hosted = 'https://github.com/lythka/hao-assets/releases/download/unpublished/python.tar.gz'
        rel = dict(candidate=True, downloads=[dict(name='python', url=hosted),
                                              dict(name='Apple', url='https://swcdn.apple.com/input')])
        with mock.patch.object(D, 'head', return_value=404) as head:
            self.assertEqual(state(D.check_urls(rel, P, False), 'urls'), ['fail'])
            self.assertEqual([c.args[0] for c in head.call_args_list], ['https://swcdn.apple.com/input'])
            head.reset_mock()
            rel['candidate'] = False
            D.check_urls(rel, P, False)
            self.assertEqual(len(head.call_args_list), 2)

    REL = {'downloads': [{'name': 'a.pkg', 'url': 'https://swcdn.apple.com/a', 'mirrors': ['https://m/a']},
                         {'name': 'b.dmg', 'url': 'https://updates.cdn-apple.com/b'}]}

    def test_urls(self):
        class Resp:
            def __init__(self, status): self.status = status
            def __enter__(self): return self
            def __exit__(self, *a): return False
        ok = lambda req, timeout: Resp(200)
        gone = lambda req, timeout: Resp(404 if req.full_url.endswith('/b') else 200)
        self.assertEqual(state(D.check_urls(self.REL, P, False, ok), 'urls'), ['ok'])
        r = D.check_urls(self.REL, P, False, gone)
        self.assertEqual(state(r, 'urls'), ['fail'])
        self.assertIn('b.dmg (404)', r[0]['detail'])
        self.assertEqual(state(D.check_urls(self.REL, P, True, gone), 'urls'), ['warn'])

    def test_mirror_required_for_a_release(self):
        self.assertEqual(state(D.check_mirror(self.REL, 'create-installer'), 'mirror'), ['warn'])
        self.assertEqual(state(D.check_mirror(self.REL, 'release'), 'mirror'), ['fail'])


class Shebangs(unittest.TestCase):
    def test_counts_other_interpreters(self):
        with tempfile.TemporaryDirectory() as d:
            for name, first in (('a.py', '#!/usr/bin/env python3.14'), ('b.py', '#!/usr/bin/env python3'),
                                ('c.py', '#!/usr/bin/python3'), ('d.sh', '#!/bin/sh')):
                (Path(d) / name).write_text(first + '\n')
            files = 'a.py\nb.py\nc.py\nd.sh\n'
            run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, files, '')
            r = D.check_shebangs(P, top=Path(d), run=run)
        self.assertEqual(r[0]['state'], 'warn')
        self.assertIn('1 name python3', r[0]['detail'])
        self.assertIn('1 name /usr/bin/python3', r[0]['detail'])


class RootPython(unittest.TestCase):
    """The launcher's rule (sudo runs only python.org's 3.14, root's alone), checked the same way by doctor."""
    def test_missing_or_user_owned_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = D.check_interpreter(Path(d) / 'Versions' / '3.14')
            self.assertEqual((r[0]['state'], 'missing' in r[0]['detail']), ('fail', True))
            py = Path(d) / 'Versions' / '3.14' / 'bin'
            py.mkdir(parents=True)
            (py / 'python3.14').write_text('')
            r = D.check_interpreter(Path(d) / 'Versions' / '3.14')
            self.assertEqual(r[0]['state'], 'fail')
            self.assertIn("not root's alone", r[0]['detail'])
            self.assertIn('chmod -R go-w', r[0]['fix'])



class AppleMirror(unittest.TestCase):
    """HAO_MIRROR: URL/health answers "ok", URL/index.json holds {sha256: {size, ...}}; N of M release inputs held."""
    NEEDS = [('a' * 64, 10, 'https://apple/a.dmg', 'a.dmg'), ('b' * 64, 20, 'https://apple/b.pkg', 'b.pkg')]

    def opener(self, health=b'ok\n', index=None):
        import io
        import json as J
        import urllib.error

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()

        def open_(req, timeout=None):
            url = req.full_url
            if url.endswith('/health'):
                if health is None:
                    raise urllib.error.URLError('refused')
                return R(health)
            if url.endswith('/index.json'):
                return R(J.dumps(index if index is not None else {}).encode())
            raise urllib.error.HTTPError(url, 404, 'no', {}, None)
        return open_

    def run_(self, env, **kw):
        return D.check_hao_mirror(None, {'head_timeout_s': 1}, False, env=env, needs=self.NEEDS, **kw)

    def test_not_set(self):
        r = self.run_({})
        self.assertEqual((r[0]['state'], r[0]['detail']), ('skip', 'mirror: not set (downloads go to the origin)'))

    def test_all_held(self):
        r = self.run_({'HAO_MIRROR': 'http://m:8080/'}, opener=self.opener(index={
            'a' * 64: {'size': 10, 'name': 'a.dmg'}, 'b' * 64: {'size': 20, 'name': 'b.pkg'}}))
        self.assertEqual((r[0]['state'], r[0]['detail']), ('ok', 'mirror http://m:8080: reachable, 2 of 2 release inputs held'))

    def test_missing_and_wrong_size_are_named(self):
        r = self.run_({'HAO_MIRROR': 'http://m'}, opener=self.opener(index={'a' * 64: {'size': 11}}))
        self.assertEqual(r[0]['state'], 'warn')
        self.assertIn('0 of 2 release inputs held; missing: a.dmg, b.pkg', r[0]['detail'])

    def test_no_release_to_compare_with(self):
        r = D.check_hao_mirror(None, {'head_timeout_s': 1}, False, env={'HAO_MIRROR': 'http://m'}, needs=[],
                               opener=self.opener(index={'a' * 64: {'size': 1}}))
        self.assertEqual(r[0]['state'], 'warn')
        self.assertIn('reachable, 1 files; no release inputs to compare with', r[0]['detail'])

    def test_unreachable_or_not_ok_fails(self):
        self.assertEqual(self.run_({'HAO_MIRROR': 'http://m'}, opener=self.opener(health=None))[0]['state'], 'fail')
        self.assertEqual(self.run_({'HAO_MIRROR': 'http://m'}, opener=self.opener(health=b'nope'))[0]['state'], 'fail')
        self.assertEqual(self.run_({'HAO_MIRROR': 'file:///etc'})[0]['state'], 'fail')

    def test_mirror_list_lines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pipeline').mkdir()
            (Path(d) / 'pipeline' / 'mirror-list').write_text('')

            class Done:
                returncode = 0
                stdout = '%s 10 https://apple/a.dmg a file.dmg\nnot a line\n' % ('a' * 64)
            seen = []
            got = D.mirror_needs(None, '/x/release.json', run=lambda argv, **k: seen.append(argv) or Done(), top=d)
            self.assertEqual(got, [('a' * 64, 10, 'https://apple/a.dmg', 'a file.dmg')])
            self.assertEqual(seen[0][-2:], ['--release', '/x/release.json'])


if __name__ == '__main__':
    unittest.main()
