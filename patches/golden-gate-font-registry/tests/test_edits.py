"""patch.json "files" declares what apply.sh installs on the Data volume: the Setup account's registry in setup.tar,
and the account-neutral user registry under PerUserSeed/fonts/. All are whole files from one row, so each is an origin entry; there is
no edit spec. The list is derived from the artifact's manifest, never written by hand.
python3.14 -I -B -m unittest discover -s patches/golden-gate-font-registry/tests

Always: the declared entries are well formed, and apply.sh run on a fixture Data volume (made by cache/seeds/make from
a synthetic harvest; install -o/-g and chown shimmed) installs exactly the files the manifest rule lists, with the
bytes of the tar members and user/ files.
  FONT_REGISTRY_ARTIFACT   the font_registry_golden_gate artifact folder: its manifest (setup.tar member and user/ file)
                         must equal the declared paths, and SHA256SUMS must match its files.
Not declarable: the registry golden-gate-user-seed's seed.zsh later writes into each new account (its path holds that
account's uid and cache-folder hash, and the user's ~/Library/Fonts is written into it)."""
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
NAME = PATCH.name
ROW, KIND, TARNAME, USERDIR = 'font_registry_golden_gate', 'font-registry', 'setup.tar', 'fonts'
SEED = 'Library/Application Support/PerUserSeed'
sys.path.insert(0, str(TOP / 'cache/seeds/tests'))
import test_seeds  # noqa: E402  (the harvest fixture)

PATCH_JSON = json.loads((PATCH / 'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}
SHIMS = {'install': '#!/bin/sh\nn=\nfor a do shift; case $n$a in -o|-g) n=1;; 1*) n=;; *) set -- "$@" "$a";; esac; done\n'
                    'exec /usr/bin/install "$@"\n',
         'chown': '#!/bin/sh\nexit 0\n'}


def member_reader(tar_path, name):
    def read():
        with tarfile.open(tar_path) as t:
            return t.extractfile(name).read()
    return read


def manifest(artifact):
    """{installed path: bytes reader} the hook's tar and install lines put on Data, from the artifact alone."""
    out = {}
    with tarfile.open(artifact / TARNAME) as t:
        for m in t:
            if m.isfile():
                out[m.name] = member_reader(artifact / TARNAME, m.name)
    for f in sorted((artifact / 'user').iterdir()):
        out['%s/%s/%s' % (SEED, USERDIR, f.name)] = f.read_bytes
    return out


class Declaration(unittest.TestCase):
    def test_every_file_is_a_whole_file_from_the_row(self):
        self.assertEqual(len(FILES), len(PATCH_JSON['files']))
        for path, f in FILES.items():
            self.assertNotIn('edits', f)
            self.assertEqual(f['origin']['kind'], 'hao')
            self.assertEqual(f['origin']['row'], ROW)
            self.assertIn(ROW, PATCH_JSON['inputs'])
            self.assertFalse(path.startswith('/') or '..' in path.split('/'))

    def test_fixed_paths(self):
        self.assertEqual(sorted(FILES), [SEED + '/fonts/fontregistry.user',
                                         'private/var/folders/zz/zyxvpxvq6csfxvn_n00000z000007r/C/'
                                         'com.apple.FontRegistry/fontregistry.user'])


class Hook(unittest.TestCase):
    def test_hook_installs_exactly_the_manifest_on_a_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'shims').mkdir()
            for name, text in SHIMS.items():
                (d / 'shims' / name).write_text(text)
                (d / 'shims' / name).chmod(0o755)
            env = dict(os.environ, PATH='%s:%s' % (d / 'shims', os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')))
            fixture = test_seeds.Fixture(d)
            (d / 'artifacts').mkdir()
            r = test_seeds.run(fixture.path, '--out', d / 'artifacts')
            self.assertEqual(r.returncode, 0, r.stderr)
            artifact = next((d / 'artifacts').glob(KIND + '-*'))
            sysroot = d / 'sys'
            (sysroot / 'System/Library/CoreServices').mkdir(parents=True)
            (sysroot / 'System/Library/CoreServices/SystemVersion.plist').write_bytes(
                plistlib.dumps({'ProductBuildVersion': '26B131'}))
            data = d / 'data'
            (data / SEED).mkdir(parents=True)
            folder = d / 'hook'
            shutil.copytree(PATCH, folder, ignore=shutil.ignore_patterns('tests'))
            (folder / 'inputs').mkdir()
            (folder / 'inputs' / ROW).symlink_to(artifact)
            r = subprocess.run(['sh', '-eu', str(folder / 'apply.sh')], capture_output=True, text=True,
                               env=dict(env, DATA=str(data), SYS=str(sysroot), P=str(folder)))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            installed = {str(p.relative_to(data)): p for p in data.rglob('*') if p.is_file()}
            want = manifest(artifact)
            self.assertEqual(sorted(installed), sorted(want))
            for path, read in want.items():
                self.assertEqual(installed[path].read_bytes(), read(), path)
            fixed = [p for p in FILES if p in want]
            self.assertTrue(fixed, 'the fixture installs none of the declared fixed paths')


@unittest.skipUnless(os.environ.get('FONT_REGISTRY_ARTIFACT'), 'set FONT_REGISTRY_ARTIFACT')
class Real(unittest.TestCase):
    def test_declared_paths_are_the_artifacts_manifest(self):
        artifact = Path(os.environ['FONT_REGISTRY_ARTIFACT'])
        self.assertEqual(sorted(manifest(artifact)), sorted(FILES))
        for line in (artifact / 'SHA256SUMS').read_text().splitlines():
            digest, rel = line.split(None, 1)
            self.assertEqual(hashlib.sha256((artifact / rel).read_bytes()).hexdigest(), digest, rel)


if __name__ == '__main__':
    unittest.main()
