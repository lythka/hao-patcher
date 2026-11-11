"""The seed patches as a chain on a fixture Data volume: golden-gate-user-seed, -font-registry, -shader-caches,
-icon-caches (their set order), fed by cache/seeds/make from a synthetic harvest. Runs unprivileged: install's
-o/-g and chown are shimmed, so owners are checked in the artifacts (cache/seeds tests), not here."""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(TOP / 'cache/seeds/tests'))
import test_seeds  # noqa: E402  (the harvest fixture)

CHAIN = ('golden-gate-user-seed', 'golden-gate-font-registry', 'golden-gate-shader-caches', 'golden-gate-icon-caches')
ROWS = {'golden-gate-font-registry': ('font_registry_golden_gate', 'font-registry'),
        'golden-gate-shader-caches': ('shader_caches_golden_gate', 'shader-caches'),
        'golden-gate-icon-caches': ('icon_caches_golden_gate', 'icon-caches')}
SEED = 'Library/Application Support/PerUserSeed'
SHIMS = {'install': '#!/bin/sh\nn=\nfor a do shift; case $n$a in -o|-g) n=1;; 1*) n=;; *) set -- "$@" "$a";; esac; done\n'
                    'exec /usr/bin/install "$@"\n',
         'chown': '#!/bin/sh\nexit 0\n'}


class SeedChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        shims = self.d / 'shims'
        shims.mkdir()
        for name, text in SHIMS.items():
            (shims / name).write_text(text)
            (shims / name).chmod(0o755)
        self.env = dict(os.environ, PATH='%s:%s' % (shims, os.environ['PATH']))

    def tearDown(self):
        self.tmp.cleanup()

    def system(self, build):
        sys_root = self.d / ('sys-' + build)
        p = sys_root / 'System/Library/CoreServices'
        p.mkdir(parents=True)
        (p / 'SystemVersion.plist').write_bytes(plistlib.dumps({'ProductBuildVersion': build}))
        return sys_root

    def artifacts(self):
        h = test_seeds.Fixture(self.d)
        out = self.d / 'artifacts'
        out.mkdir()
        r = test_seeds.run(h.path, '--out', out)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out

    def apply(self, name, data, sys_root, artifacts):
        folder = self.d / 'hooks' / name
        shutil.copytree(TOP / 'patches' / name, folder, ignore=shutil.ignore_patterns('tests'))
        if name in ROWS:
            row, kind = ROWS[name]
            (folder / 'inputs').mkdir()
            (folder / 'inputs' / row).symlink_to(next(artifacts.glob(kind + '-*')))
        return subprocess.run(['sh', '-eu', str(folder / 'apply.sh')], capture_output=True, text=True,
                              env=dict(self.env, DATA=str(data), SYS=str(sys_root), P=str(folder)))

    def test_chain(self):
        artifacts, data, sys_root = self.artifacts(), self.d / 'data', self.system('26B131')
        data.mkdir()
        for name in CHAIN:
            r = self.apply(name, data, sys_root, artifacts)
            self.assertEqual(r.returncode, 0, name + ': ' + r.stdout + r.stderr)
        seed = data / SEED
        self.assertEqual((seed / 'build').read_text(), '26B131')
        self.assertTrue((seed / 'is/store.index').is_file() and (seed / 'is/__cache_info__').is_file())
        self.assertTrue((seed / 'fonts/fontregistry.user').is_file())
        self.assertTrue((seed / 'metal/com.apple.iconservicesagent/com.apple.iconservicesagent/com.apple.metal/'
                                '0_0/functions.data').is_file())
        self.assertTrue((data / test_seeds.SETUP_C / 'com.apple.SetupAssistant/com.apple.metal/0_0/functions.data')
                        .is_file())
        self.assertTrue((data / test_seeds.STORE / 'store-info.plist').is_file())
        self.assertTrue((data / test_seeds.SETUP_C / 'com.apple.FontRegistry/fontregistry.user').is_file())
        # the daemon runs seed.zsh exactly as committed
        plist = plistlib.loads((data / 'Library/LaunchDaemons/local.peruser-seed.plist').read_bytes())
        self.assertEqual(plist['ProgramArguments'][:2], ['/bin/zsh', '-c'])
        self.assertEqual(plist['ProgramArguments'][2],
                         (TOP / 'patches/golden-gate-user-seed/src/seed.zsh').read_text())
        self.assertEqual(plist['WatchPaths'], ['/private/var/db/dslocal/nodes/Default/users'])

    def test_wrong_build_refused(self):
        artifacts, data, sys_root = self.artifacts(), self.d / 'data', self.system('26B200')
        data.mkdir()
        self.assertEqual(self.apply('golden-gate-user-seed', data, sys_root, artifacts).returncode, 0)
        for name in CHAIN[1:]:
            r = self.apply(name, data, sys_root, artifacts)
            self.assertNotEqual(r.returncode, 0, name)
            self.assertIn('is 26B200', r.stderr)

    def test_order_enforced(self):
        artifacts, data, sys_root = self.artifacts(), self.d / 'data', self.system('26B131')
        data.mkdir()
        r = self.apply('golden-gate-shader-caches', data, sys_root, artifacts)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('after golden-gate-user-seed', r.stderr)

    def test_seed_zsh_parses(self):
        subprocess.run(['zsh', '-n', str(TOP / 'patches/golden-gate-user-seed/src/seed.zsh')], check=True)


if __name__ == '__main__':
    unittest.main()
