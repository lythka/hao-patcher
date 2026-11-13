"""The relocatable kit (pipeline/kit.py): kit.json renders to golden-run's own guest configuration in the builder VM,
and to the same configuration under any other kit and local roots. Fixtures only; no disk tools."""
import json
from pathlib import Path
import re
import runpy
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
KIT = runpy.run_path(str(TOP / 'pipeline/kit.py'))
RUN = runpy.run_path(str(TOP / 'pipeline/golden-run'))
ROWS = [('oclp-hook', 'darwin27-kernel'), ('oclp-hook', 'golden-gate-userspace'), ('oclp-hook', 'selectors'),
        ('post', 'hao-opencore')]
RECIPE = dict(size_bytes=80, name='GG', golden_build='26B131', donor_build='25G342')
LOCAL = '/Volumes/builder-work-x/job'


def share(folder):
    root = Path(folder)
    (root / 'kdk/KDK_25.kdk').mkdir(parents=True)
    (root / 'patches.txt').write_text(''.join(s + ' ' + n + ' input\n' for s, n in ROWS))
    for i, (stage, name) in enumerate(ROWS, 1):
        path = root / ('hooks' if stage == 'oclp-hook' else 'post') / ('%02d-%s' % (i, name))
        path.mkdir(parents=True)
        (path / ('apply.sh' if stage == 'post' else 'kc-hook.sh')).touch()
    return root


class Kit(unittest.TestCase):
    def configs(self, folder, **options):
        s = share(folder)
        guest = RUN['guest_config'](RECIPE, s, LOCAL, **options)
        kit = KIT['template'](RUN['guest_config'](RECIPE, s, LOCAL, **options), 'mbp92-26B131', RUN['GUEST'], LOCAL)
        return s, guest, json.loads(json.dumps(kit))

    def test_guest_render_is_golden_runs_configuration(self):
        for options in ({}, dict(overlays=True, cryptex_cache=True)):
            with tempfile.TemporaryDirectory() as folder:
                _, guest, kit = self.configs(folder, **options)
                guest['image'] = LOCAL + '/target.img'
                self.assertEqual(guest, KIT['render'](kit, RUN['GUEST'], LOCAL))

    def test_kit_json_has_no_absolute_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, kit = self.configs(folder, overlays=True)
            text = json.dumps(kit['config'])
            self.assertNotIn(RUN['GUEST'], text)
            self.assertNotIn(LOCAL, text)
            self.assertNotIn('image', kit['config'])
            self.assertEqual('${LOCAL}/py/bin/python3.12', kit['config']['python'])
            self.assertEqual('${KIT}', kit['config']['environment']['S'])

    def test_render_elsewhere_moves_every_path(self):
        with tempfile.TemporaryDirectory() as folder:
            _, guest, kit = self.configs(folder)
            config = KIT['render'](kit, '/Volumes/HAOKIT/kit', '/Volumes/Work/hao')
            paths = [v for v in KIT['strings'](config) if v.startswith('/')]
            self.assertTrue(paths)
            for p in paths:
                self.assertTrue(p.startswith(('/Volumes/HAOKIT/kit', '/Volumes/Work/hao')), p)
            self.assertEqual('/Volumes/Work/hao/target.img', config['image'])
            self.assertEqual([h['name'] for h in guest['hooks']], [h['name'] for h in config['hooks']])

    def test_disk_target_needs_confirmation_and_drops_the_image(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, kit = self.configs(folder)
            with self.assertRaises(ValueError):
                KIT['render'](kit, '/k', '/l', disk='disk9')
            with self.assertRaises(ValueError):
                KIT['render'](kit, '/k', '/l', disk='disk9', confirm_disk='')
            config = KIT['render'](kit, '/k', '/l', disk='disk9', confirm_disk='disk9')
            self.assertEqual(('disk9', 'disk9'), (config['disk'], config['confirm_disk']))
            self.assertNotIn('image', config)

    def test_local_inside_the_kit_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, kit = self.configs(folder)
            for local in ('/k', '/k/work'):
                with self.assertRaises(ValueError):
                    KIT['render'](kit, '/k', local)

    def test_template_refuses_paths_outside_both_roots(self):
        with self.assertRaises(ValueError):
            KIT['template'](dict(python='/Users/someone/py/bin/python3'), 'x', '/k', '/l')
        self.assertEqual('/usr/bin/true', KIT['template'](dict(tool='/usr/bin/true'), 'x', '/k', '/l')['config']['tool'])

    def test_schema_is_checked(self):
        with self.assertRaises(ValueError):
            KIT['render'](dict(schema=99, config={}), '/k', '/l')

    def test_assemble_script_picks_the_same_python_folders(self):
        text = (TOP / 'pipeline/stages/golden-assemble.sh').read_text()
        pairs = dict(re.findall(r'^\s+(\w+)\) PYDIR=([\w-]+) ;;$', text, re.M))
        self.assertEqual(KIT['PYTHON_DIRS'], pairs)
        self.assertEqual('py-x86_64', KIT['python_dir']('x86_64'))
        with self.assertRaises(ValueError):
            KIT['python_dir']('ppc')


if __name__ == '__main__':
    unittest.main()
