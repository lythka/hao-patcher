"""patch.json "files" declares what apply.sh installs on the Data volume: auth.db and root's two LaunchService stores,
whole files from row root_launchservices_golden_gate.
python3.14 -I -B -m unittest discover -s patches/golden-gate-root-launchservices/tests

Always: the declared entries are well formed; an artifact made by tools/first-boot-seeds/rootls collect from a fixture
Data volume, run through apply.sh on a fixture (install -o/-g shimmed), installs exactly the declared files with the
artifact's bytes and modes; apply.sh refuses another build, a changed file, and a Data volume with an auth.db.
  ROOT_LAUNCHSERVICES_ARTIFACT   a real artifact folder: rootls check passes for 26B131 and its files are the declared
                                 sources."""
import datetime
import importlib.machinery
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
ROW, BUILD = 'root_launchservices_golden_gate', '26B131'
TOOL = TOP / 'tools/first-boot-seeds'
loader = importlib.machinery.SourceFileLoader('rootls', str(TOOL / 'rootls'))
spec = importlib.util.spec_from_loader('rootls', loader)
rl = importlib.util.module_from_spec(spec)
loader.exec_module(rl)
loader = importlib.machinery.SourceFileLoader('test_rootls', str(TOOL / 'tests/test_rootls.py'))
spec = importlib.util.spec_from_loader('test_rootls', loader)
fx = importlib.util.module_from_spec(spec)
loader.exec_module(fx)

PATCH_JSON = json.loads((PATCH / 'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}
SCHEMA = int(rl.STORE_RX.match(Path(next(p for p in FILES if p.endswith('.csstore'))).name).group(2))
SHIMS = {'install': '#!/bin/sh\nn=\nfor a do shift; case $n$a in -o|-g) n=1;; 1*) n=;; *) set -- "$@" "$a";; esac; done\n'
                    'exec /usr/bin/install "$@"\n'}
MODES = {'auth.db': 0o600, '.csstore': 0o644}


class Declaration(unittest.TestCase):
    def test_whole_files_from_the_row(self):
        self.assertEqual(PATCH_JSON['inputs'], [ROW])
        for path, f in FILES.items():
            self.assertNotIn('edits', f)
            self.assertEqual((f['origin']['kind'], f['origin']['row']), ('hao', ROW))
            self.assertFalse(path.startswith('/') or '..' in path.split('/'))
            self.assertEqual(Path(path).name, Path(f['source']).name)

    def test_one_schema_and_root_dir(self):
        stores = [p for p in FILES if p.endswith('.csstore')]
        self.assertEqual(len(stores), 2)
        for p in stores:
            self.assertEqual(str(Path(p).parent), rl.ROOT_LS)
            self.assertEqual(int(rl.STORE_RX.match(Path(p).name).group(2)), SCHEMA)
        self.assertIn(rl.AUTH_DB, FILES)


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = Path(self.tmp.name)
        (d / 'shims').mkdir()
        for name, text in SHIMS.items():
            (d / 'shims' / name).write_text(text)
            (d / 'shims' / name).chmod(0o755)
        self.env = dict(os.environ, PATH='%s:%s' % (d / 'shims', os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')))
        f = fx.Fixture('setUp')
        f.setUp()
        self.addCleanup(f.tearDown)
        self.artifact = rl.collect(f.data, BUILD, SCHEMA, d / 'artifacts', 'fixture', today=datetime.date(2026, 11, 12))
        self.sys = d / 'sys'
        (self.sys / 'System/Library/CoreServices').mkdir(parents=True)
        self.version(BUILD)
        self.data = d / 'data'
        (self.data / 'private/var').mkdir(parents=True)
        self.folder = d / 'hook'
        shutil.copytree(PATCH, self.folder, ignore=shutil.ignore_patterns('tests'))
        (self.folder / 'inputs').mkdir()
        (self.folder / 'inputs' / ROW).symlink_to(self.artifact)

    def tearDown(self):
        self.tmp.cleanup()

    def version(self, build):
        (self.sys / 'System/Library/CoreServices/SystemVersion.plist').write_bytes(
            plistlib.dumps({'ProductBuildVersion': build}))

    def run_hook(self):
        return subprocess.run(['sh', '-eu', str(self.folder / 'apply.sh')], capture_output=True, text=True,
                              env=dict(self.env, DATA=str(self.data), SYS=str(self.sys), P=str(self.folder)))

    def test_installs_exactly_the_declared_files(self):
        r = self.run_hook()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('schema %d' % SCHEMA, r.stdout)
        installed = {str(p.relative_to(self.data)): p for p in self.data.rglob('*') if p.is_file()}
        self.assertEqual(sorted(installed), sorted(FILES))
        for path, f in FILES.items():
            self.assertEqual(installed[path].read_bytes(), (self.artifact / f['source']).read_bytes(), path)
            want = next(m for k, m in MODES.items() if path.endswith(k))
            self.assertEqual(installed[path].stat().st_mode & 0o777, want, path)
        self.assertEqual((self.data / rl.ROOT_LS).stat().st_mode & 0o777, 0o744)
        self.assertEqual((self.data / 'private/var/folders/zz').stat().st_mode & 0o777, 0o755)

    def test_refuses_another_build(self):
        self.version('26B140')
        r = self.run_hook()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('store is for 26B131, System is 26B140', r.stderr)
        self.assertFalse((self.data / 'private/var/db/auth.db').exists())

    def test_refuses_a_changed_file(self):
        p = self.artifact / 'auth.db'
        p.chmod(0o600)
        p.write_bytes(p.read_bytes() + b'x')
        r = self.run_hook()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SHA256SUMS', r.stderr)

    def test_refuses_existing_auth_db(self):
        (self.data / 'private/var/db').mkdir(parents=True)
        (self.data / 'private/var/db/auth.db').write_bytes(b'other')
        r = self.run_hook()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.data / 'private/var/db/auth.db').read_bytes(), b'other')


@unittest.skipUnless(os.environ.get('ROOT_LAUNCHSERVICES_ARTIFACT'), 'set ROOT_LAUNCHSERVICES_ARTIFACT')
class Real(unittest.TestCase):
    def test_artifact(self):
        a = Path(os.environ['ROOT_LAUNCHSERVICES_ARTIFACT'])
        self.assertEqual(rl.check(a, BUILD, SCHEMA)['schema'], SCHEMA)
        self.assertEqual(sorted(f['source'] for f in FILES.values()),
                         sorted(str(p.relative_to(a)) for p in a.rglob('*') if p.is_file()
                                and p.name not in ('build.json', 'SHA256SUMS')))


if __name__ == '__main__':
    unittest.main()
