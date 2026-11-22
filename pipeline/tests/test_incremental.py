"""pipeline/incremental.py: fingerprints of a staged kit, and the plan of a build from an earlier one (fixtures only)."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

TOP = Path(__file__).resolve().parents[2]
I = runpy.run_path(str(TOP/'pipeline/incremental.py'))
RUN = runpy.run_path(str(TOP/'pipeline/golden-run'))

SET = [('oclp-hook', 'darwin27-kernel', ['kernel']),
       ('oclp-hook', 'golden-gate-userspace', ['payload', 'runtime']),
       ('oclp-hook', 'hao-runtime', ['runtime']),
       ('oclp-hook', 'golden-gate-dyld-internal', []),
       ('post', 'hao-opencore', ['runtime', 'efi']),
       ('post', 'dev-access', [])]
ASSEMBLY = dict(schema=1, target='mbp92', golden_build='26B131', donor_build='25G342', oclp='fd83bda09',
                patch_set='dev-lite', name='macOS', size_bytes=80, inputs=dict(native='native_donors'),
                repo='store/images/x', workspace_size_bytes=160, checkpoints=['hao-runtime', 'snapshot'])
LOCAL = '/Volumes/builder-work-20261118-000000-abcdef/job'


class Fixture:
    """A staged kit (SHARE) as golden-run leaves it before the guest starts, its input records and patches folder."""

    def __init__(self, root):
        self.root = Path(root)
        self.share = self.root/'share'
        self.patches = self.root/'patches'
        self.store = self.root/'store'
        self.records = {}
        for row in ('kernel', 'payload', 'efi', 'native_donors', 'kdk'):
            self.records[row] = dict(sha256=row[0]*64, path=str(self.store/'artifacts'/row))
        runtime = self.store/'artifacts/runtime'
        for rel, body in (('embed/manifest.json', '{}'), ('embed/stubs/a.stub', 'stub'), ('hao-install.py', 'i'),
                          ('root/usr/lib/hao/libhao.dylib', 'libhao 1')):
            (runtime/rel).parent.mkdir(parents=True, exist_ok=True)
            (runtime/rel).write_text(body)
        self.records['runtime'] = dict(sha256='1'*64, path=str(runtime))
        lines = []
        for number, (stage, name, rows) in enumerate(SET, 1):
            lines.append(' '.join([stage, name] + rows))
            folder = self.share/('hooks' if stage == 'oclp-hook' else 'post')/('%02d-%s' % (number, name))
            (folder/'inputs').mkdir(parents=True)
            (folder/('kc-hook.sh' if stage == 'oclp-hook' else 'apply.sh')).write_text('# %s\n' % name)
            for row in rows:
                (folder/'inputs'/row).write_text('staged ' + row)
            (self.patches/name).mkdir(parents=True)
            (self.patches/name/'patch.json').write_text(json.dumps(dict(inputs=rows)))
        (self.share/'patches.txt').write_text('\n'.join(lines) + '\n')
        for name in I['BASE_FILES']:
            (self.share/name).write_text(name)
        (self.share/'assembly-source').mkdir()
        (self.share/'assembly-source/golden-image.py').write_text('steps')
        config = dict(image=LOCAL + '/target.img', name='macOS',
                      payload='/private/var/ssdshare/hooks/02-golden-gate-userspace/inputs/payload',
                      kernel=LOCAL + '/hooks/01-darwin27-kernel/inputs/kernel',
                      hooks=[dict(name=n) for s, n, _ in SET if s == 'oclp-hook'],
                      posts=[dict(name=n) for s, n, _ in SET if s == 'post'])
        (self.share/'guest-config.json').write_text(json.dumps(config))

    def fingerprints(self, assembly=ASSEMBLY, local=LOCAL):
        return I['fingerprints'](self.share, self.records, assembly, local=local, store=self.store, top=self.root,
                                 patches=self.patches)

    def committed(self, fps):
        return dict(status='committed', repository='/images/x', fingerprints=fps)


class Incremental(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.f = Fixture(self.folder.name)
        self.fps = self.f.fingerprints()

    def tearDown(self):
        self.folder.cleanup()

    def plan(self, new, saved=('hao-runtime',), old=None):
        keys = {I['point_key'](self.fps, n) for n in saved}
        return I['plan'](self.f.committed(old or self.fps), new, keys)

    def test_base_holds_the_rows_the_base_layers_read(self):
        base = self.fps['base']
        # not recipe inputs (the assembly's donors, the KDK), and the recipe rows guest-config names (payload, kernel)
        self.assertEqual(sorted(base['rows']), ['kdk', 'kernel', 'native_donors', 'payload'])
        self.assertNotIn('repo', base['assembly'])
        self.assertNotIn('checkpoints', base['assembly'])
        self.assertEqual([r['name'] for r in self.fps['recipes']], [n for _, n, _ in SET])

    def test_the_same_kit_gives_the_same_fingerprints_and_an_unchanged_plan(self):
        self.assertEqual(self.f.fingerprints(), self.fps)
        result = self.plan(self.f.fingerprints())
        self.assertEqual(result['mode'], 'unchanged')

    def test_scratch_keys_caches_and_the_jobs_local_folder_decide_nothing(self):
        assembly = dict(ASSEMBLY, repo='store/images/y', workspace_size_bytes=1, disk_need_gb=9, checkpoints=[])
        (self.f.share/'hooks/03-hao-runtime/__pycache__').mkdir()
        (self.f.share/'hooks/03-hao-runtime/__pycache__/x.pyc').write_text('cache')
        other = '/Volumes/builder-work-20261118-111111-123456/job'
        config = json.loads((self.f.share/'guest-config.json').read_text().replace(LOCAL, other))
        (self.f.share/'guest-config.json').write_text(json.dumps(config))
        self.assertEqual(self.f.fingerprints(assembly, local=other), self.fps)

    def test_a_runtime_payload_change_starts_before_hao_runtime(self):
        """The owner's case: only the hao_runtime row changed. golden-gate-userspace reads only the payload's stubs
        (input_reads), so the image up to hao-runtime is reused; hao-runtime, every later recipe, the snapshot and
        every post run."""
        (self.f.patches/'golden-gate-userspace/patch.json').write_text(json.dumps(dict(
            inputs=['payload', 'runtime'], input_reads=dict(runtime=['embed', 'hao-install.py']))))
        before = self.f.fingerprints()
        lib = Path(self.f.records['runtime']['path'])/'root/usr/lib/hao/libhao.dylib'
        lib.write_text('libhao 2')
        self.f.records['runtime']['sha256'] = '2'*64
        after = self.f.fingerprints()
        result = self.plan(after, old=before, saved=())
        self.assertEqual(result['mode'], 'full')      # no saved point yet
        self.assertIn('no saved point at or before hao-runtime', ' '.join(result['reasons']))
        result = I['plan'](self.f.committed(before), after, {I['point_key'](before, 'hao-runtime')})
        self.assertEqual((result['mode'], result['point'], result['first_changed']),
                         ('incremental', 'hao-runtime', 'hao-runtime'))
        self.assertEqual([c['name'] for c in result['changed']], ['hao-runtime', 'hao-opencore'])
        self.assertEqual(result['reused'], ['darwin27-kernel', 'golden-gate-userspace'])
        self.assertEqual(result['runs'], ['hao-runtime', 'golden-gate-dyld-internal', 'snapshot', 'hao-opencore',
                                          'dev-access'])
        # a change to the stubs it does read starts before golden-gate-userspace instead
        (Path(self.f.records['runtime']['path'])/'embed/stubs/a.stub').write_text('stub 2')
        stubs = self.f.fingerprints()
        result = I['plan'](self.f.committed(before), stubs, {I['point_key'](before, 'hao-runtime')})
        self.assertEqual((result['mode'], result['restart']), ('full', 'golden-gate-userspace'))
        saved = {I['point_key'](before, n) for n in ('golden-gate-userspace', 'hao-runtime')}
        result = I['plan'](self.f.committed(before), stubs, saved)
        self.assertEqual((result['mode'], result['point']), ('incremental', 'golden-gate-userspace'))

    def test_without_input_reads_the_whole_row_counts(self):
        self.f.records['runtime']['sha256'] = '2'*64
        result = self.plan(self.f.fingerprints())
        self.assertEqual(result['first_changed'], 'golden-gate-userspace')
        self.assertEqual(result['mode'], 'full')

    def test_the_latest_saved_point_at_or_before_the_first_change_is_used(self):
        (self.f.share/'hooks/04-golden-gate-dyld-internal/kc-hook.sh').write_text('# edited\n')
        new = self.f.fingerprints()
        result = self.plan(new)
        self.assertEqual((result['mode'], result['restart'], result['point']),
                         ('incremental', 'golden-gate-dyld-internal', 'hao-runtime'))
        self.assertEqual(result['changed'], [dict(name='golden-gate-dyld-internal', stage='oclp-hook', why=['code'])])
        result = self.plan(new, saved=('darwin27-kernel', 'golden-gate-dyld-internal'))
        self.assertEqual(result['point'], 'golden-gate-dyld-internal')

    def test_a_post_change_starts_before_the_snapshot(self):
        (self.f.share/'post/06-dev-access/apply.sh').write_text('# edited\n')
        new = self.f.fingerprints()
        result = self.plan(new, saved=('snapshot',))
        self.assertEqual((result['mode'], result['restart'], result['point']), ('incremental', 'snapshot', 'snapshot'))
        self.assertEqual(result['reused'], [n for s, n, _ in SET if s == 'oclp-hook'])
        self.assertEqual(result['runs'], ['snapshot', 'hao-opencore', 'dev-access'])

    def test_a_staged_tool_or_an_added_or_removed_recipe_is_a_change(self):
        build = self.f.share/'hooks/02-golden-gate-userspace/build/metal-fixes'
        build.mkdir(parents=True)
        (build/'fix.py').write_text('copied in by make.sh')
        result = self.plan(self.f.fingerprints())
        self.assertEqual(result['changed'][0], dict(name='golden-gate-userspace', stage='oclp-hook', why=['code']))
        old = copy.deepcopy(self.fps)
        new = copy.deepcopy(self.fps)
        new['recipes'].append(dict(stage='post', name='extra', folder='post/07-extra', code='0', inputs={}))
        self.assertEqual(I['plan'](self.f.committed(old), new, set())['changed'][-1]['why'], ['added'])
        self.assertEqual(I['plan'](self.f.committed(new), old, set())['changed'][-1]['why'], ['removed'])
        # a removed post starts before the snapshot
        keys = {I['point_key'](old, 'snapshot')}
        self.assertEqual(I['plan'](self.f.committed(new), old, keys)['point'], 'snapshot')

    def test_a_base_change_is_a_full_build(self):
        for change in (lambda: (self.f.share/'assembly-source/golden-image.py').write_text('other steps'),
                       lambda: self.f.records['kdk'].update(sha256='9'*64),
                       lambda: self.f.records['payload'].update(sha256='9'*64),
                       lambda: (self.f.share/'MANIFEST.txt').write_text('oclp other')):
            with self.subTest(change=change):
                f = Fixture(tempfile.mkdtemp(dir=self.folder.name))
                old = f.fingerprints()
                self.f = f
                change()
                result = I['plan'](f.committed(old), f.fingerprints(),
                                   {I['point_key'](old, n) for n in I['point_names'](old)})
                self.assertEqual(result['mode'], 'full')
                self.assertTrue(result['reasons'][0].startswith('base: '), result['reasons'])
        f = Fixture(tempfile.mkdtemp(dir=self.folder.name))
        result = I['plan'](f.committed(f.fingerprints()), f.fingerprints(dict(ASSEMBLY, size_bytes=81)), set())
        self.assertEqual(result['reasons'], ['base: assembly: size_bytes'])

    def test_records_that_cannot_be_planned_from_are_a_full_build(self):
        for previous, reason in ((dict(status='failed', fingerprints=self.fps), 'did not commit'),
                                 (dict(status='committed'), 'no fingerprints'),
                                 (dict(status='committed', fingerprints=dict(self.fps, schema=0)), 'schema'),
                                 (dict(status='committed', fingerprints=self.fps, overlays={'plan': {}}), 'overlays')):
            result = I['plan'](previous, self.fps, {I['point_key'](self.fps, 'hao-runtime')})
            self.assertEqual(result['mode'], 'full')
            self.assertIn(reason, ' '.join(result['reasons']))

    def test_an_unpinned_folder_row_never_matches(self):
        """A '-' folder row in the store is named, not hashed: an entry reading it always runs again and no point
        after it is reused. A '-' folder of the checkout counts by its content."""
        unpacked = self.f.store/'unpacked'
        unpacked.mkdir(parents=True)
        tool = self.f.root/'tools/edit-engine'
        tool.mkdir(parents=True)
        (tool/'engine.py').write_text('v1')
        self.f.records['kernel'] = dict(sha256='-', path=str(unpacked))
        self.f.records['efi'] = dict(sha256='-', path=str(tool))
        fps = self.f.fingerprints()
        self.assertTrue(fps['base']['rows']['kernel'].startswith(I['UNPINNED']))
        self.assertTrue(fps['recipes'][4]['inputs']['efi'].startswith('tree '))
        self.assertIsNone(I['point_key'](fps, 'hao-runtime'))
        result = I['plan'](self.f.committed(fps), fps, set())
        self.assertEqual(result['mode'], 'full')
        self.assertIn('rows: kernel unpinned', ' '.join(result['reasons']))
        # in a recipe input: that entry always counts as changed
        self.f.records['kernel'] = dict(sha256='k'*64, path=str(self.f.store/'artifacts/kernel'))
        self.f.records['runtime'] = dict(sha256='-', path=str(unpacked))
        fps = self.f.fingerprints()
        self.assertEqual(I['plan'](self.f.committed(fps), fps, set())['first_changed'], 'golden-gate-userspace')
        (tool/'engine.py').write_text('v2')
        self.assertNotEqual(I['row_identity'](self.f.records['efi'], self.f.root, self.f.store),
                            fps['recipes'][4]['inputs']['efi'])

    def test_points_the_assembly_asks_for(self):
        self.assertEqual(sorted(I['points'](self.fps, ASSEMBLY)), ['hao-runtime', 'snapshot'])
        self.assertEqual(I['points'](self.fps, dict(ASSEMBLY, checkpoints=[])), {})
        with self.assertRaisesRegex(I['Refused'], 'no point hao-opencore'):
            I['points'](self.fps, dict(ASSEMBLY, checkpoints=['hao-opencore']))
        with self.assertRaisesRegex(I['Refused'], 'distinct'):
            I['points'](self.fps, dict(ASSEMBLY, checkpoints=['snapshot', 'snapshot']))
        # a point's key covers only what runs before it
        later = copy.deepcopy(self.fps)
        later['recipes'][3]['code'] = 'other'
        self.assertEqual(I['point_key'](later, 'hao-runtime'), I['point_key'](self.fps, 'hao-runtime'))
        self.assertNotEqual(I['point_key'](later, 'snapshot'), I['point_key'](self.fps, 'snapshot'))

    def test_saved_points_need_their_key_and_image(self):
        folder = self.f.root/'points'
        for key, image in (('k1', True), ('k2', False)):
            (folder/key).mkdir(parents=True)
            (folder/key/'point.json').write_text(json.dumps(dict(key=key, name='hao-runtime')))
            if image:
                (folder/key/'target.img').write_bytes(b'')
        (folder/'k3').mkdir()
        (folder/'k3/point.json').write_text(json.dumps(dict(key='other')))
        (folder/'k3/target.img').write_bytes(b'')
        self.assertEqual(sorted(I['saved_points'](folder)), ['k1'])
        self.assertEqual(I['saved_points'](self.f.root/'none'), {})

    def test_input_reads_declarations(self):
        problems = I['reads_problems']
        self.assertEqual(problems(dict(inputs=['r'])), [])
        self.assertEqual(problems(dict(inputs=['r'], input_reads=dict(r=['embed', 'a/b.py']))), [])
        for reads, why in ((dict(x=['a']), 'not one of its inputs'), (dict(r=[]), 'a list'),
                           (dict(r=['../a']), 'a list'), (dict(r=['/a']), 'a list'), (dict(r=['a', 'a']), 'twice'),
                           ([], 'must be an object')):
            self.assertIn(why, ' '.join(problems(dict(inputs=['r'], input_reads=reads))))
        (self.f.patches/'hao-runtime/patch.json').write_text(json.dumps(dict(inputs=['runtime'],
                                                                             input_reads=dict(runtime=['../x']))))
        with self.assertRaisesRegex(I['Refused'], 'hao-runtime: input_reads'):
            self.f.fingerprints()
        # the shipped recipes' declarations pass patches.py check (which runs reads_problems on each)
        P = runpy.run_path(str(TOP/'pipeline/patches.py'))
        self.assertFalse([p for p in P['check']() if 'input_reads' in p])

    def test_previous_record_from_a_repository_or_a_record(self):
        runs = self.f.root/'runs'
        repo = self.f.root/'images/dev'
        repo.mkdir(parents=True)
        (repo/'image.json').write_text('{}')
        with self.assertRaisesRegex(I['Refused'], 'no committed build record'):
            I['previous_record'](repo, runs)
        for name, status, mtime in (('golden-a', 'committed', 100), ('golden-b', 'committed', 200),
                                    ('golden-c', 'failed', 300)):
            (runs/name).mkdir(parents=True)
            (runs/name/'build.json').write_text(json.dumps(dict(status=status, repository=str(repo))))
            os.utime(runs/name/'build.json', (mtime, mtime))
        (runs/'golden-d').mkdir()
        (runs/'golden-d/build.json').write_text(json.dumps(dict(status='committed', repository='/elsewhere')))
        folder, data = I['previous_record'](repo, runs)
        self.assertEqual(folder.name, 'golden-b')
        self.assertEqual(I['previous_record'](runs/'golden-a', runs)[0].name, 'golden-a')
        self.assertEqual(I['previous_record'](runs/'golden-a/build.json', runs)[0].name, 'golden-a')
        with self.assertRaisesRegex(I['Refused'], 'no build record'):
            I['previous_record'](self.f.root/'nothing', runs)

    def test_cli_plan_and_show(self):
        runs = self.f.root/'runs'
        for name, fps in (('old', self.fps), ('new', copy.deepcopy(self.fps))):
            (runs/name).mkdir(parents=True)
            (runs/name/'build.json').write_text(json.dumps(dict(status='committed', fingerprints=fps)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(I['main'](['plan', str(runs/'old'), str(runs/'new'), '--points',
                                        str(self.f.root/'points')]), 0)
        self.assertIn('incremental plan: unchanged', out.getvalue())
        (runs/'new/build.json').write_text(json.dumps(dict(status='kit staged')))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(I['main'](['plan', str(runs/'old'), str(runs/'new')]), 2)
        self.assertIn('recorded no fingerprints', err.getvalue())


class GoldenRunFrom(unittest.TestCase):
    def test_incremental_record_keeps_fingerprints_points_and_the_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            f = Fixture(folder)
            fps = f.fingerprints()
            store = f.root/'store'
            key = I['point_key'](fps, 'hao-runtime')
            (store/'cache/golden-points'/key).mkdir(parents=True)
            (store/'cache/golden-points'/key/'point.json').write_text(json.dumps(dict(key=key)))
            (store/'cache/golden-points'/key/'target.img').write_bytes(b'')
            changed = copy.deepcopy(fps)
            changed['recipes'][2]['inputs']['runtime'] = 'f'*64

            def fingerprints(*args, **kwargs):
                return changed
            state = {}
            with mock.patch.dict(RUN['INCREMENTAL'], fingerprints=fingerprints), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                result = RUN['incremental_record'](state, f.share, f.records, ASSEMBLY, LOCAL, store,
                                                   (Path('/runs/old'), dict(status='committed', fingerprints=fps,
                                                                            repository='/images/x',
                                                                            image_revision='abc123')))
            self.assertEqual(state['fingerprints'], changed)
            self.assertEqual(sorted(state['points']), ['hao-runtime', 'snapshot'])
            self.assertEqual((result['mode'], result['point'], result['used']), ('incremental', 'hao-runtime', False))
            self.assertEqual((result['previous'], result['previous_image_revision']), ('/runs/old', 'abc123'))
            self.assertIn('runs in full', result['why'])
            self.assertIn('start from the point before hao-runtime', out.getvalue())
            # without --from: fingerprints and points only
            state = {}
            self.assertIsNone(RUN['incremental_record'](state, f.share, f.records, ASSEMBLY, LOCAL, store))
            self.assertEqual(set(state), {'fingerprints', 'points'})

    def test_from_refuses_overlays_and_capture_modes_and_unknown_records(self):
        with tempfile.TemporaryDirectory() as folder:
            for argv, why in ((['a.json', '--repo', 'r', '--from', folder, '--from-overlays', '--overlay-system', '/x'],
                               'without --from-overlays'),
                              (['--capture-record', folder, '--from', folder], 'without --from-overlays'),
                              (['a.json', '--repo', 'r', '--from', folder], 'no build record')):
                with mock.patch.object(sys, 'argv', ['golden-run'] + argv), \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    with self.assertRaises(SystemExit):
                        RUN['main']()
                self.assertIn(why, err.getvalue())


if __name__ == '__main__':
    unittest.main()
