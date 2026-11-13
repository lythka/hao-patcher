"""Behaviour of pipeline/patches.py (sets, applies, stages). python3 -m unittest discover -s pipeline/tests"""
import contextlib, hashlib, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / 'pipeline'))
import patches


class Plan(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.target = self.d / 'm.json'; self.target.write_text(json.dumps({'model': 'MacBookPro9,2'}))

    def patch(self, name, **p):
        (self.d / name).mkdir(); (self.d / name / 'patch.json').write_text(json.dumps({'revision': 1, **p}))

    def plan(self, text, build='25G332'):
        s = self.d / 'set.txt'; s.write_text(text)
        return patches.plan(s, self.target, build, root=self.d)

    def test_set_order_comments_and_stages(self):
        self.patch('hook', applies='any', stage='oclp-hook')
        self.patch('data', applies='any', stage='post', inputs=['row_a', 'row_b'])
        self.patch('rec', applies='any', stage='recovery', inputs=['row_c'])
        self.assertEqual(self.plan('# a set\n\ndata   # trailing note\nhook\nrec\n'),
                         [('post', 'data', ['row_a', 'row_b']), ('oclp-hook', 'hook', []), ('recovery', 'rec', ['row_c'])])
        self.assertEqual(self.plan(''), [])

    def test_applies_by_model_and_build(self):
        self.patch('only332', applies={'builds': ['25G332']}, stage='post')
        self.patch('other', applies={'models': ['MacBookPro8,1']}, stage='post')
        self.assertEqual([n for _, n, _ in self.plan('only332\nother\n')], ['only332'])
        self.assertEqual(self.plan('only332\n', build='25G241'), [])

    def test_bad_patch_stops(self):
        self.patch('nostage', applies='any')
        self.patch('badkey', applies={'gpu': ['x']}, stage='post')
        self.patch('noapplies', stage='post')
        self.patch('badstage', applies='any', stage='s9')
        for n in ('nostage', 'badkey', 'noapplies', 'badstage'):
            with self.assertRaises(ValueError, msg=n):
                self.plan(n + '\n')
        with self.assertRaises(OSError):
            self.plan('missing\n')


class Optional(unittest.TestCase):
    """Optional patches: skipped when a row or its path is missing or the group is skipped, never on a hash mismatch;
    needs_any skips a consumer when no provider of its group runs."""
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.lock = self.d / 'lock'; self.lock.mkdir()
        self.target = self.d / 'm.json'; self.target.write_text(json.dumps({'model': 'MacBookPro9,2'}))
        self.patch('base', stage='post', inputs=['base_row'])
        self.patch('seed', stage='post', optional='caches', needs_any='caches')
        self.patch('icons', stage='post', optional='caches', inputs=['icons_row'])
        self.patch('fonts', stage='post', optional='caches', inputs=['fonts_row'])
        self.artifact('icons_row'); self.artifact('fonts_row')

    def patch(self, name, **p):
        (self.d / name).mkdir(); (self.d / name / 'patch.json').write_text(json.dumps({'revision': 1, 'applies': 'any', **p}))

    def artifact(self, row, body=b'payload', sha=None):
        f = self.d / ('store-' + row); f.write_bytes(body)
        (self.lock / row).write_text('# fixture\n%s %s\n' % (sha or hashlib.sha256(body).hexdigest(), f))

    def plan(self, skip=()):
        s = self.d / 'set.txt'; s.write_text('base\nseed\nicons\nfonts\n'); record = {}
        with contextlib.redirect_stderr(io.StringIO()) as err:
            out = patches.plan(s, self.target, '25G342', root=self.d, locks=[str(self.lock)], skip=skip, record=record)
        return [n for _, n, _ in out], record, err.getvalue()

    def test_present_rows_run(self):
        names, record, _ = self.plan()
        self.assertEqual(names, ['base', 'seed', 'icons', 'fonts'])
        self.assertEqual(record, {'ran': {'seed': 'caches', 'icons': 'caches', 'fonts': 'caches'}, 'skipped': {}})

    def test_absent_row_or_path_skips_only_that_patch(self):
        (self.lock / 'icons_row').unlink(); (self.d / 'store-fonts_row').unlink()
        names, record, err = self.plan()
        self.assertEqual(names, ['base'])   # seed: no provider left
        self.assertIn('optional icons skipped: no row icons_row in', err)
        self.assertIn('optional fonts skipped: row fonts_row: %s is not there' % (self.d / 'store-fonts_row'), err)
        self.assertEqual(record['skipped']['seed']['reason'], 'no provider of optional group caches runs')
        self.assertEqual(record['ran'], {})

    def test_one_provider_keeps_the_consumer(self):
        (self.lock / 'icons_row').unlink()
        self.assertEqual(self.plan()[0], ['base', 'seed', 'fonts'])

    def test_hash_mismatch_stops(self):
        self.artifact('icons_row', sha='0' * 64)
        with self.assertRaisesRegex(ValueError, 'icons_row: .* is not 0{64} \\(a corrupted input stops'):
            self.plan()

    def test_forced_skip_even_when_present(self):
        names, record, err = self.plan(skip=['caches'])
        self.assertEqual(names, ['base'])
        self.assertEqual({n: v['reason'] for n, v in record['skipped'].items()},
                         {n: 'group caches skipped (--skip-optional)' for n in ('seed', 'icons', 'fonts')})

    def test_required_patch_is_never_skipped_here(self):
        # base_row has no row: plan keeps base; staging (inputs.sh) stops on it as before
        self.assertIn('base', self.plan()[0])

    def test_cli_without_harvest_rows(self):
        # the repository's dev-bs-test set with no rows at all: the cache group is skipped, the rest planned as before
        rec = self.d / 'optional.json'; empty = self.d / 'empty'; empty.mkdir()
        cmd = [sys.executable, '-I', str(TOP / 'pipeline/patches.py'), 'plan', str(TOP / 'pipeline/sets/dev-bs-test.txt'),
               str(TOP / 'pipeline/targets/mbp92.json'), '25G342']
        full = subprocess.run(cmd + ['--locks', str(empty), '--skip-optional', 'none'], capture_output=True, text=True)
        r = subprocess.run(cmd + ['--locks', str(empty), '--record', str(rec)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(rec.read_text())
        self.assertEqual(got['ran'], {})
        self.assertIn('golden-gate-user-seed', got['skipped'])
        self.assertTrue(got['skipped']['golden-gate-icon-caches']['reason'].startswith('no row icon_caches_golden_gate in '))
        self.assertIn('optional golden-gate-shader-caches skipped: no row shader_caches_golden_gate', r.stderr)
        names = [l.split()[1] for l in r.stdout.splitlines()]
        self.assertEqual(names, [l.split()[1] for l in full.stdout.splitlines() if l.split()[1] not in got['skipped']])
        self.assertIn('dev-access', names)
        self.assertIn('no patch of optional group none', full.stderr)


class Repo(unittest.TestCase):
    def test_shipped_sets_plan_for_every_target_and_build(self):
        for s in (TOP / 'pipeline/sets').glob('*.txt'):
            for t in (TOP / 'pipeline/targets').glob('*.json'):
                for lock in [d for d in (TOP / 'pipeline/inputs').iterdir() if d.is_dir() and d.name.startswith('25')]:
                    r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/patches.py'), 'plan', str(s), str(t),
                                        lock.name], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, '%s %s %s: %s' % (s.name, t.name, lock.name, r.stderr))
        self.assertEqual(patches.names(TOP / 'pipeline/sets/stock.txt'), [])

    def test_every_patch_folder_has_its_recipe(self):
        # oclp-hook: src/hook.sh or src/kc-hook.sh; post and recovery: apply.sh; inputs are lock rows
        rows = set()
        for lock in (TOP / 'pipeline/inputs').iterdir():
            rows |= {f.name for f in lock.iterdir() if f.is_file()}
        for lock in (TOP / 'pipeline/inputs/golden').iterdir():   # golden-run reads inputs/golden/<build> first
            rows |= {f.name for f in lock.iterdir() if f.is_file()}
        for pj in (TOP / 'patches').glob('*/patch.json'):
            p, d = json.loads(pj.read_text()), pj.parent
            if p.get('status') == 'unfinished':   # no recipe yet, in no set (patches.check keeps it out of shipping)
                continue
            if p['stage'] == 'oclp-hook':
                self.assertTrue((d / 'src/hook.sh').is_file() or (d / 'src/kc-hook.sh').is_file(), d.name)
            else:
                self.assertTrue((d / 'apply.sh').is_file(), d.name)
            for r in p.get('inputs', []):
                self.assertIn(r, rows, d.name)
            # the README's newest "Revisions" line is the patch.json revision
            rev = (d / 'README.md').read_text().split('\n## Revisions\n', 1)[-1]
            self.assertIn('\n- r%d ' % p['revision'], '\n' + rev, d.name)


    def test_catalog_is_current(self):
        # docs/PATCHES.md is generated; regenerate with `python3.14 -I pipeline/patches.py catalog`
        import patches
        self.assertEqual((TOP / 'docs/PATCHES.md').read_text(), patches.catalog(), 'docs/PATCHES.md is stale')

    def test_tracker_is_current(self):
        # patches/TRACKER.md is generated; regenerate with `python3 -I pipeline/patches.py tracker`
        import patches
        self.assertEqual((TOP / 'patches/TRACKER.md').read_text(), patches.tracker(), 'patches/TRACKER.md is stale')


class Packs(unittest.TestCase):
    """packs/*.json: detection from the target, plan filtering, and the 9,2 plan unchanged under --packs auto."""
    DEFS = {'base': {'format': 1, 'detect': {'any_intel': True}, 'phase': 'installer', 'recipes': ['a']},
            'gpu': {'format': 1, 'detect': {'oclp_patchsets': ['Intel Ivy Bridge']}, 'phase': 'installer',
                    'recipes': ['b']},
            'other': {'format': 1, 'detect': {'board_ids': ['Mac-0000']}, 'phase': 'live-capable', 'recipes': ['c']}}

    def test_detection_for_the_real_target(self):
        got = patches.detect_packs(TOP / 'pipeline/targets/mbp92.json', self.DEFS)
        self.assertEqual(sorted(got), ['base', 'gpu'])

    def test_plan_filters_unselected_packs(self):
        d = Path(tempfile.mkdtemp())
        for n in 'abcd':
            (d / n).mkdir(); (d / n / 'patch.json').write_text(json.dumps({'revision': 1, 'stage': 'post', 'applies': 'any'}))
        s = d / 'set.txt'; s.write_text('a\nb\nc\nd\n')
        t = d / 't.json'; t.write_text(json.dumps({'model': 'MacBookPro9,2'}))
        run = lambda packs: [n for _, n, _ in patches.plan(s, t, '25G342', root=d, packs=packs, pack_defs=self.DEFS)]
        self.assertEqual(run(None), ['a', 'b', 'c', 'd'])
        self.assertEqual(run(['base', 'gpu']), ['a', 'b', 'd'])      # d is in no pack: applies as before

    def test_pack_rules(self):
        bad = patches.pack_problems({'x': {'format': 1, 'detect': {}, 'phase': 'sometimes', 'recipes': ['nope']},
                                     'y': {'format': 1, 'detect': {'any_intel': True}, 'phase': 'installer',
                                           'recipes': ['hao-runtime']},
                                     'z': {'format': 1, 'detect': {'any_intel': True}, 'phase': 'installer',
                                           'recipes': ['hao-runtime']}})
        got = '\n'.join(bad)
        for want in ('x.json: needs format 1 and phase', 'x.json: detect is one of', 'x.json: no recipe nope',
                     'z.json: hao-runtime is already in pack y'):
            self.assertIn(want, got)

    def test_opt_in_packs_are_never_detected(self):
        defs = dict(self.DEFS, trial={'format': 1, 'detect': {'board_ids': ['Mac-6F01561E16C75D06']},
                                      'phase': 'live-capable', 'opt_in': True, 'recipes': ['c']})
        defs.pop('other')
        self.assertEqual(sorted(patches.detect_packs(TOP / 'pipeline/targets/mbp92.json', defs)), ['base', 'gpu'])
        self.assertEqual(patches.pack_problems({'t': dict(defs['trial'], opt_in=False, recipes=[])}),
                         ['packs/t.json: opt_in is true or absent'])
        real = patches.load_packs()
        self.assertTrue(real['metal27-trial']['opt_in'])
        self.assertNotIn('metal27-trial', patches.detect_packs(TOP / 'pipeline/targets/mbp92.json'))

    def test_mbp92_plan_is_unchanged_with_detected_packs(self):
        target = TOP / 'pipeline/targets/mbp92.json'
        for s in ('golden-gate-26B131', 'golden-gate'):
            setfile = TOP / 'pipeline/sets' / (s + '.txt')
            for build in ('25G332', '25G342'):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(patches.plan(setfile, target, build),
                                     patches.plan(setfile, target, build, packs=patches.detect_packs(target)), s)


class Format(unittest.TestCase):
    """patches.check: patch.json keys and values, required README sections, Validation agrees with "validated",
    shipping sets select product patches only."""
    README = '# x\n\n## Verify\nrun it\n\n## Validation\n%s\n\n## Revisions\n- r1 2026-11-02 new (abc)\n'

    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.sets = self.d / 'sets'; self.sets.mkdir()
        (self.d / 'patches').mkdir()

    def patch(self, name, validation='In use.', **p):
        d = self.d / 'patches' / name; d.mkdir()
        (d / 'patch.json').write_text(json.dumps({'revision': 1, 'stage': 'post', 'applies': 'any',
                                                  'status': 'product', 'validated': True, **p}))
        (d / 'README.md').write_text(self.README % validation)

    def check(self, shipping=''):
        (self.sets / 'recovery.txt').write_text(shipping)
        return patches.check(self.d / 'patches', self.sets)

    def test_a_patch_in_the_format_passes(self):
        self.patch('ok'); self.patch('wip', validation='NEEDS VALIDATION: OPEN.', validated=False)
        self.assertEqual(self.check('ok\nwip\n'), [])

    def test_each_rule(self):
        self.patch('nostatus', status='later'); self.patch('mismatch', validation='NEEDS VALIDATION.')
        self.patch('devship', status='dev'); self.patch('oldrev', revision=2)
        (self.d / 'patches' / 'nojson').mkdir()
        got = '\n'.join(self.check('devship\n'))
        for want in ('nostatus: status', 'mismatch: patch.json validated=True', 'devship: status dev, but set recovery',
                     'oldrev: newest Revisions line is r1', 'nojson: no patch.json'):
            self.assertIn(want, got)

    def test_optional_keys(self):
        self.patch('opt', optional='caches'); self.patch('cons', optional='caches', needs_any='caches')
        self.patch('badgroup', optional=True); self.patch('loose', needs_any='caches')
        got = '\n'.join(self.check())
        self.assertNotIn('opt:', got); self.assertNotIn('cons:', got)
        self.assertIn('badgroup: optional must be a group name', got)
        self.assertIn('loose: needs_any without optional', got)

    def edits(self, name, spec_name, spec):
        d = self.d / 'patches' / name / 'edits'; d.mkdir(exist_ok=True)
        (d / spec_name).write_text(json.dumps(spec))

    SPEC = {'format': 1, 'path': 'usr/lib/libx.dylib', 'origin': {'kind': 'apple'},
            'edits': [{'id': 'e', 'kind': 'literal', 'at': 16, 'find': '01', 'replace': '00', 'reason': 'why'}]}

    def test_declared_files(self):
        self.patch('good', inputs=['row_a'], files=[{'path': 'usr/lib/libx.dylib', 'edits': 'edits/x.json'},
                                                    {'path': 'usr/lib/new', 'origin': {'kind': 'hao', 'row': 'row_a'}}])
        self.edits('good', 'x.json', self.SPEC)
        self.assertEqual(self.check(), [])
        self.assertIn('1. e  literal at 0x10', patches.explain('libx.dylib', root=self.d / 'patches'))
        self.assertIn('origin   hao (row row_a)', patches.explain('good', root=self.d / 'patches'))

    def test_tree_and_install(self):
        self.patch('t', files=[{'path': 'usr/lib/hao', 'origin': {'kind': 'hao'}, 'tree': True,
                                'install': {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']}}])
        self.patch('badtree', files=[{'path': 'a', 'origin': {'kind': 'hao'}, 'tree': 'yes'}])
        self.patch('badinst', files=[{'path': 'a', 'origin': {'kind': 'hao'}, 'install': {'mode': 'rwx'}}])
        got = '\n'.join(self.check())
        self.assertFalse([l for l in got.splitlines() if l.startswith('t: ')])
        self.assertIn('badtree: files: a: "tree": true goes with an origin', got)
        self.assertIn('badinst: files: a: install is', got)
        self.assertIn('every file under it from hao', patches.explain('t', root=self.d / 'patches'))
        self.assertIn('install  mode 0755, owner root:wheel, flags restricted', patches.explain('t', root=self.d / 'patches'))
        self.assertIn('Installed mode 0755, owner root:wheel, flags restricted.', patches.catalog(root=self.d / 'patches'))

    def test_explain_and_catalog_show_install_and_after(self):
        stamp = {'format': 1, 'path': 'EFI/x', 'origin': {'kind': 'hao', 'row': 'row_a'},
                 'edits': [{'id': 's', 'kind': 'op', 'op': 'hao.stamp', 'reason': 'why',
                            'args': {'kernel_collection': 'System/Library/KernelCollections/BootKernelExtensions.kc'}}]}
        self.patch('late', inputs=['row_a'], files=[
            {'path': 'usr/lib/list', 'origin': {'kind': 'hao'}, 'after': ['overlays', 'usr/lib/other'],
             'install': {'mode': '0644', 'owner': 'root:wheel'}},
            {'path': 'EFI/x', 'edits': 'edits/x.json', 'after': ['usr/lib/list']},
            {'path': 'usr/lib/plain', 'origin': {'kind': 'hao', 'row': 'row_a'}}])
        self.edits('late', 'x.json', stamp)
        self.assertEqual(self.check(), [])
        text = patches.explain('late', root=self.d / 'patches')
        self.assertIn('install  mode 0644, owner root:wheel\n  after    every overlay file installed first; usr/lib/other',
                      text)
        self.assertIn('after    System/Library/KernelCollections/BootKernelExtensions.kc (hao.stamp reads it); '
                      'usr/lib/list', text)
        self.assertIn('usr/lib/plain\n  origin   hao (row row_a)\n  install  default: root:wheel', text)
        md = patches.catalog(root=self.d / 'patches')
        self.assertIn('Installed mode 0644, owner root:wheel. Made after every overlay file installed first, '
                      '`usr/lib/other`.', md)
        self.assertIn('Made after `System/Library/KernelCollections/BootKernelExtensions.kc` (hao.stamp reads it), '
                      '`usr/lib/list`.', md)
        self.assertIn('- `usr/lib/plain`: whole file from hao (row row_a).\n', md)

    def test_declared_file_rules(self):
        self.patch('both', files=[{'path': 'a', 'edits': 'edits/a.json', 'origin': {'kind': 'hao'}}])
        self.patch('abs', files=[{'path': '/abs', 'origin': {'kind': 'hao'}}])
        self.patch('norow', files=[{'path': 'a', 'origin': {'kind': 'hao', 'row': 'missing'}}])
        self.patch('kind', files=[{'path': 'a', 'origin': {'kind': 'mystery'}}])
        self.patch('otherpath', files=[{'path': 'usr/lib/other', 'edits': 'edits/x.json'}])
        self.edits('otherpath', 'x.json', self.SPEC)
        self.patch('badspec', files=[{'path': 'usr/lib/libx.dylib', 'edits': 'edits/x.json'}])
        self.edits('badspec', 'x.json', dict(self.SPEC, edits=[dict(self.SPEC['edits'][0], reason='')]))
        self.patch('stray')
        self.edits('stray', 'loose.json', self.SPEC)
        got = '\n'.join(self.check())
        for want in ('both: files: each entry is', 'abs: files: path must be relative', 'norow: files: a: origin row missing',
                     'kind: files: a: origin is', 'otherpath: edits/x.json: names usr/lib/libx.dylib',
                     'badspec: edits/x.json: e: reason is required', 'stray: edits/loose.json is not declared'):
            self.assertIn(want, got)

    def test_validated_revision_keeps_proof_only_while_installed_bytes_are_unchanged(self):
        self.patch('kept', revision=3)
        d = self.d / 'patches' / 'kept'
        (d / 'README.md').write_text(self.README.replace('- r1 ', '- r3 ') % 'In use.')
        (d / 'src').mkdir(); (d / 'src' / 'kc-hook.sh').write_text('#!/bin/sh\ntrue\n')
        digest = patches.recipe_digest('kept', root=self.d / 'patches')
        pj = json.loads((d / 'patch.json').read_text())
        (d / 'patch.json').write_text(json.dumps(dict(pj, validated_revision=2, validated_digest=digest)))
        self.assertEqual(self.check(), [])
        (d / 'edits').mkdir(); (d / 'tests').mkdir(); (d / 'tests' / 't.py').write_text('# declaration only\n')
        self.assertEqual(patches.recipe_digest('kept', root=self.d / 'patches'), digest)
        (d / 'src' / 'kc-hook.sh').write_text('#!/bin/sh\nfalse\n')
        self.assertIn('kept: validated at r2, but what it installs changed since', '\n'.join(self.check()))

    def test_validated_revision_rules(self):
        self.patch('alone', validated_revision=1)
        self.patch('same', revision=1, validated_revision=1, validated_digest='0' * 64)
        self.patch('unproven', revision=2, validated=False, validated_revision=1, validated_digest='0' * 64,
                   validation='NEEDS VALIDATION.')
        got = '\n'.join(self.check())
        for want in ('alone: validated_revision and validated_digest go together',
                     'same: validated_revision must be an earlier revision', 'unproven: validated_revision without'):
            self.assertIn(want, got)

    def test_repo_patches_follow_the_format(self):
        self.assertEqual(patches.check(), [])


if __name__ == '__main__':
    unittest.main()
