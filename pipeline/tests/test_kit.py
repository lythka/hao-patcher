"""The relocatable kit (pipeline/kit.py): kit.json renders to golden-run's own guest configuration in the builder VM,
and to the same configuration under any other kit and local roots. Fixtures only; no disk tools."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import tempfile
import unittest
from unittest import mock

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
            self.assertEqual('internal', config['disk_policy'])
            config = KIT['render'](kit, '/k', '/l', disk='disk9', confirm_disk='disk9', disk_policy='external')
            self.assertEqual('external', config['disk_policy'])
            with self.assertRaises(ValueError):
                KIT['render'](kit, '/k', '/l', disk='disk9', confirm_disk='disk9', disk_policy='any')

    def test_local_inside_the_kit_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, kit = self.configs(folder)
            for local in ('/k', '/k/work'):
                with self.assertRaises(ValueError):
                    KIT['render'](kit, '/k', local)

    def test_template_refuses_paths_outside_both_roots(self):
        with self.assertRaises(ValueError):
            KIT['template'](dict(python='/opt/elsewhere/py/bin/python3'), 'x', '/k', '/l')
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


class CaptureKit(unittest.TestCase):
    """golden-run --capture-kit: an image assembled outside the VM is committed as a VM build's is. A fake ssdimg
    records its calls; no disk tools run."""
    SIZE = 4096

    def fixture(self, root, status='assembled', attached=None, size=SIZE, link=False):
        record = root / 'record'; record.mkdir()
        (record / 'build.json').write_text(json.dumps(dict(status='kit staged', kit=str(root / 'kit'), commit='abc',
                                                           recipe=dict(size_bytes=self.SIZE, golden_build='26B131'))))
        local = root / 'local'; (local / 'work').mkdir(parents=True)
        (local / 'work/build.json').write_text(json.dumps(dict(status=status, attached_device=attached)))
        (local / 'work/command-000.log').write_text('evidence')
        if link:
            (root / 'elsewhere.img').write_bytes(b'\0' * size)
            (local / 'target.img').symlink_to(root / 'elsewhere.img')
        else:
            (local / 'target.img').write_bytes(b'\0' * size)
        calls = root / 'calls.txt'
        tool = root / 'ssdimg'
        tool.write_text('#!/bin/sh\necho "$@" >> %s\n[ "$1" != init ] || echo {} > "$2/image.json"\n' % calls)
        tool.chmod(0o755)
        return record, local, calls, tool

    def capture(self, record, local, repo, tool):
        with mock.patch.dict(os.environ, SSDIMG=str(tool)), contextlib.redirect_stdout(io.StringIO()):
            return RUN['capture_kit'](record, local, repo)

    def test_commits_the_image_and_keeps_the_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); record, local, calls, tool = self.fixture(root)
            repo = root / 'repo'; repo.mkdir()
            state = self.capture(record, local, repo, tool)
            lines = calls.read_text().splitlines()
            self.assertEqual(['init', 'capture', 'fsck', 'commit'],
                             [l.split()[0] if l.split()[0] != '-C' else l.split()[2] for l in lines])
            self.assertIn(str(local / 'target.img'), lines[1])
            self.assertEqual('committed', state['status'])
            self.assertEqual('committed', json.loads((record / 'build.json').read_text())['status'])
            self.assertEqual('evidence', (record / 'kit-output/guest/command-000.log').read_text())

    def test_refuses_an_unfinished_assembly_or_a_wrong_image(self):
        for options in (dict(status='failed'), dict(attached='/dev/disk9'), dict(size=10), dict(link=True)):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder); record, local, calls, tool = self.fixture(root, **options)
                with self.assertRaises((RuntimeError, ValueError)):
                    self.capture(record, local, root / 'repo', tool)
                self.assertFalse(calls.exists(), options)

    def test_refuses_a_record_that_is_no_kit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); record, local, calls, tool = self.fixture(root)
            (record / 'build.json').write_text(json.dumps(dict(status='committed')))
            with self.assertRaises(RuntimeError):
                self.capture(record, local, root / 'repo', tool)


if __name__ == '__main__':
    unittest.main()
