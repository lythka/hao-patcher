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
        self.env = dict(os.environ, PATH='%s:%s' % (shims, os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')))

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

    def test_place_chunk_leaves_out_metalfe(self):
        """place_chunk run as written in seed.zsh: an old seed's com.apple.metalfe (path-bound clang module caches)
        is not placed, com.apple.metal is, and an app's existing tree is kept."""
        text = (TOP / 'patches/golden-gate-user-seed/src/seed.zsh').read_text()
        start = text.index('place_chunk() {')
        body = text[start:text.index('\n}\n', start) + 3]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            seed, cache = d / 'seed', d / 'C'
            for bundle in ('com.apple.Preview', 'com.apple.Maps'):
                (seed / 'metal' / bundle / bundle / 'com.apple.metal/0_0').mkdir(parents=True)
                (seed / 'metal' / bundle / bundle / 'com.apple.metal/0_0/functions.data').write_bytes(b'F')
            fe = seed / 'metal/com.apple.Preview/com.apple.Preview/com.apple.metalfe/3BFB57ANS780'
            fe.mkdir(parents=True)
            (fe / 'monolithic_metal-4JF842TLW74.pcm').write_bytes(b'CPCH')
            (seed / 'metal/com.apple.Preview/com.apple.metalfe').mkdir()
            (cache / 'com.apple.Maps').mkdir(parents=True)          # the app made its own tree first
            script = '\n'.join([
                'setopt null_glob', 'zmodload zsh/datetime',
                'zmodload -F zsh/files b:zf_mv b:zf_rm b:zf_mkdir b:zf_chown b:zf_chmod', 'zmodload -F zsh/stat b:zstat',
                'log() { print -r -- "$*" }', 'ms() { REPLY=0 }', 'own() { return 0 }',
                'SEED=%s; typeset -a KEPT; NB=0; NS=0' % seed, body,
                'place_chunk benchtest 501 20 %s/ 0 com.apple.Preview com.apple.Maps' % cache,
                'print "NB=$NB NS=$NS"'])
            r = subprocess.run(['/bin/zsh', '-f', '-c', script], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('left out 2 com.apple.metalfe tree(s)', r.stdout)
            self.assertIn('NB=1 NS=1', r.stdout)
            placed = cache / 'com.apple.Preview'
            self.assertTrue((placed / 'com.apple.Preview/com.apple.metal/0_0/functions.data').is_file())
            self.assertEqual(list(placed.rglob('com.apple.metalfe')), [])
            self.assertEqual(list((cache / 'com.apple.Maps').iterdir()), [])
            self.assertFalse((cache / '.peruser-seed.stage').exists())
            self.assertTrue(fe.is_dir())                                  # the seed itself is untouched

    def test_seed_zsh_parses(self):
        subprocess.run(['zsh', '-n', str(TOP / 'patches/golden-gate-user-seed/src/seed.zsh')], check=True)


if __name__ == '__main__':
    unittest.main()
