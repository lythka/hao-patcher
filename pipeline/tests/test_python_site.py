"""pipeline/python-site: row python_site made from the python_site_* wheel rows, the same bytes and modes every time.
python3.14 -I -B -m unittest discover -s pipeline/tests -k python_site"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

TOP = Path(__file__).resolve().parents[2]
TOOL = TOP / 'pipeline' / 'python-site'
SITE = runpy.run_path(str(TOOL))
LOCK = runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))


def wheel(path, entries):
    """A wheel at PATH holding ENTRIES {name: (bytes, unix mode)}."""
    with zipfile.ZipFile(path, 'w') as z:
        for name, (data, mode) in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | mode) << 16
            z.writestr(info, data)
    return path


def row(lock, name, path):
    (lock / name).write_text('# test wheel\n%s %s\n' % (hashlib.sha256(path.read_bytes()).hexdigest(), path))


class PythonSite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lock = self.root / 'lock'
        self.lock.mkdir()
        a = wheel(self.root / 'alpha-1.0-py3-none-any.whl', {
            'alpha/__init__.py': (b'A = 1\n', 0o664),
            'alpha/_speed.so': (b'\xcf\xfa\xed\xfe', 0o775),
            'alpha-1.0.dist-info/RECORD': (b'alpha/__init__.py,,\n', 0o664),
            'alpha-1.0.data/scripts/alpha': (b'#!python\n', 0o755),
            'alpha-1.0.data/data/testing/tox.ini': (b'[tox]\n', 0o644),
            'alpha-1.0.data/purelib/alpha_extra.py': (b'X = 2\n', 0o644)})
        b = wheel(self.root / 'beta-2.0-py3-none-any.whl', {'beta.py': (b'B = 2\n', 0o644)})
        row(self.lock, 'python_site_alpha', a)
        row(self.lock, 'python_site_beta', b)
        (self.lock / 'other_row').write_text('- %s\n' % self.root)     # not a python_site_* row: ignored

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, site):
        return {str(p.relative_to(site)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in sorted(site.rglob('*')) if p.is_file()}

    def test_make_unpacks_the_wheels_as_pip_would_without_scripts_data_or_bytecode(self):
        record = SITE['make'](self.root / 'out', [self.lock])
        self.assertEqual({'alpha/__init__.py': (b'A = 1\n', 0o644), 'alpha/_speed.so': (b'\xcf\xfa\xed\xfe', 0o755),
                          'alpha-1.0.dist-info/RECORD': (b'alpha/__init__.py,,\n', 0o644),
                          'alpha_extra.py': (b'X = 2\n', 0o644), 'beta.py': (b'B = 2\n', 0o644)},
                         self.files(self.root / 'out/site'))
        self.assertEqual(['python_site_alpha', 'python_site_beta'], sorted(record['wheels']))
        self.assertEqual(record['content_sha256'], LOCK['content_sha256'](str(self.root / 'out/site')))
        self.assertEqual(json.loads((self.root / 'out/build.json').read_text())['files'], 5)
        self.assertEqual(stat.S_IMODE((self.root / 'out/site/alpha').stat().st_mode), 0o755)

    def test_same_content_every_time_and_check(self):
        old = os.umask(0o077)       # the caller's umask does not reach the result
        try:
            one = SITE['make'](self.root / 'one', [self.lock])['content_sha256']
        finally:
            os.umask(old)
        two = SITE['make'](self.root / 'two', [self.lock])['content_sha256']
        self.assertEqual(one, two)
        self.assertEqual([], SITE['check'](self.root / 'one/site', [self.lock]))
        (self.root / 'one/site/beta.py').write_bytes(b'B = 3\n')
        self.assertEqual(1, len(SITE['check'](self.root / 'one/site', [self.lock])))
        cli = subprocess.run([sys.executable, '-I', str(TOOL), 'check', str(self.root / 'two/site'), str(self.lock)],
                             capture_output=True, text=True)
        self.assertEqual(0, cli.returncode, cli.stderr)

    def test_refusals(self):
        (self.lock / 'python_site_beta').write_text('%s %s\n' % ('0' * 64, self.root / 'beta-2.0-py3-none-any.whl'))
        with self.assertRaisesRegex(ValueError, 'is not 0000'):
            SITE['make'](self.root / 'bad', [self.lock])
        clash = wheel(self.root / 'gamma-1-py3-none-any.whl', {'alpha/__init__.py': (b'other\n', 0o644)})
        row(self.lock, 'python_site_beta', clash)
        with self.assertRaisesRegex(ValueError, 'both write alpha/__init__.py'):
            SITE['make'](self.root / 'clash', [self.lock])
        unsafe = wheel(self.root / 'delta-1-py3-none-any.whl', {'../escape.py': (b'x\n', 0o644)})
        row(self.lock, 'python_site_beta', unsafe)
        with self.assertRaisesRegex(ValueError, 'unsafe entry'):
            SITE['make'](self.root / 'unsafe', [self.lock])


if __name__ == '__main__':
    unittest.main()
