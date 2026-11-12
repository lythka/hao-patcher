"""patch.json "files" declares what apply.sh installs: the launchd daemon plist and PerUserSeed/build. Both are
generated at install (the plist by src/plist.sh from src/seed.zsh, build from the System's ProductBuildVersion), so
each is an origin entry of kind hao with no row; there is no edit spec.
python3.14 -I -B -m unittest discover -s patches/golden-gate-user-seed/tests

Not declarable: what seed.zsh writes into each new account at run time (icon index, Metal trees, font registry in the
account's cache folder). Those paths hold the account's uid and the hash of its cache folder, which exist only once
the account does. The files it reads, PerUserSeed/is, metal and fonts, are declared by the three cache patches.
No real input is needed; the hook runs on a fixture Data volume (install -o/-g and chown shimmed)."""
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
SEED = 'Library/Application Support/PerUserSeed'
PATCH_JSON = json.loads((PATCH / 'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}
SHIMS = {'install': '#!/bin/sh\nn=\nfor a do shift; case $n$a in -o|-g) n=1;; 1*) n=;; *) set -- "$@" "$a";; esac; done\n'
                    'exec /usr/bin/install "$@"\n',
         'chown': '#!/bin/sh\nexit 0\n'}


class Declaration(unittest.TestCase):
    def test_entries_are_generated_hao_files_without_a_row(self):
        self.assertEqual(sorted(FILES), [SEED + '/build', 'Library/LaunchDaemons/local.peruser-seed.plist'])
        self.assertEqual(PATCH_JSON['inputs'], [])
        for f in FILES.values():
            self.assertEqual(f['origin']['kind'], 'hao')
            self.assertNotIn('row', f['origin'])
            self.assertNotIn('edits', f)

    def test_the_payload_folders_the_cache_patches_declare_are_the_ones_seed_zsh_reads(self):
        seed_zsh = (PATCH / 'src/seed.zsh').read_text()
        for name, folder in (('golden-gate-icon-caches', 'is'), ('golden-gate-shader-caches', 'metal'),
                             ('golden-gate-font-registry', 'fonts')):
            declared = [f['path'] for f in json.loads((TOP / 'patches' / name / 'patch.json').read_text())['files']
                        if f['path'].startswith(SEED + '/')]
            self.assertTrue(declared, name)
            self.assertTrue(all(p.startswith('%s/%s/' % (SEED, folder)) for p in declared), name)
            self.assertIn('$SEED/%s' % folder, seed_zsh)

    def test_hook_installs_exactly_the_declared_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'shims').mkdir()
            for name, text in SHIMS.items():
                (d / 'shims' / name).write_text(text)
                (d / 'shims' / name).chmod(0o755)
            sysroot = d / 'sys'
            (sysroot / 'System/Library/CoreServices').mkdir(parents=True)
            (sysroot / 'System/Library/CoreServices/SystemVersion.plist').write_bytes(
                plistlib.dumps({'ProductBuildVersion': '26B131'}))
            data = d / 'data'
            data.mkdir()
            folder = d / 'hook'
            shutil.copytree(PATCH, folder, ignore=shutil.ignore_patterns('tests'))
            r = subprocess.run(['sh', '-eu', str(folder / 'apply.sh')], capture_output=True, text=True,
                               env=dict(os.environ, PATH='%s:%s' % (d / 'shims', os.environ['PATH']),
                                        DATA=str(data), SYS=str(sysroot), P=str(folder)))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(sorted(str(p.relative_to(data)) for p in data.rglob('*') if p.is_file()), sorted(FILES))
            self.assertEqual((data / SEED / 'build').read_text(), '26B131')


if __name__ == '__main__':
    unittest.main()
