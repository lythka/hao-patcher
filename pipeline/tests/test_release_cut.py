"""pipeline/release-cut: every row of an assembly classified by content and by build record; release.json and its
staged assets.
python3.14 -I -B -m unittest discover -s pipeline/tests -k release"""
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

TOP = Path(__file__).resolve().parents[2]
R = runpy.run_path(str(TOP / 'pipeline' / 'release-cut'))
ASSEMBLY = TOP / 'pipeline/assemblies/mbp92-26B131.json'
ALPHA_ASSEMBLY = TOP / 'pipeline/assemblies/mbp92-26B131-alpha-v0.1.0.json'
DEV_ASSEMBLY = TOP / 'pipeline/assemblies/dev-26B131-alpha-v0.1.0.json'
CONTENT = {'ours', 'made-on-mac', 'download', 'harvested', 'in-checkout', 'dev-local', 'pending'}
STORE = (TOP / 'store/artifacts').is_dir()


_LEDGER = tempfile.TemporaryDirectory()


def setUpModule():
    # release-cut --write refuses while the laptops' swap ledger has a proven, unresolved swap (pipeline/swaps.py
    # gate); these tests are about the release, so they run against an empty ledger.
    os.environ['HAO_SWAP_LEDGER'] = str(Path(_LEDGER.name) / 'ledger.tsv')


def tearDownModule():
    os.environ.pop('HAO_SWAP_LEDGER', None)
    _LEDGER.cleanup()

class ReleaseCut(unittest.TestCase):
    def test_missing_rows_are_reported(self):
        self.assertEqual(R['classify']('nothing', None)[0], 'missing')
        self.assertEqual(R['classify']('gone', (TOP, ('0' * 64, str(TOP / 'store/artifacts/none/x'))))[0], 'missing')

    def test_assembly_rows_cover_the_set_and_donors(self):
        rows = [r for r, _ in R['rows_for'](ASSEMBLY)]
        for want in ('darwin27_kernel', 'hao_runtime', 'golden_gate_userspace', 'oclp_hardware', 'tahoe_native_donors'):
            self.assertIn(want, rows)
        self.assertEqual(len(rows), len(set(rows)))

    def test_release_rows_add_the_builders_rows_and_apple_inputs(self):
        rows = [r for r, _ in R['release_rows'](ASSEMBLY)]
        for want in ('kdk', 'python_arm64', 'python_x86_64', 'python_site', 'fl', 'opencore_release',
                     'ipsw_27_0', 'installer'):
            self.assertIn(want, rows)
        self.assertEqual(len(rows), len(set(rows)))

    def test_install_profile_uses_inputs_not_a_prebuilt_apple_cache(self):
        assembly=TOP/'pipeline/assemblies/mbp92-26B131-beta-v0.0.1.json'
        rows=dict(R['release_rows'](assembly))
        for row in ('shared_cache_builder','swift_metadata_catalog','hao_runtime_fold_catalog'):
            self.assertIn(row,rows);self.assertEqual(R['classes']()[row]['class'],'ours')
        self.assertNotIn('rebuilt_cache_catalog',rows)
        # Userspace cryptex stubs and OpenCore still consume the stock payload's shared files.
        self.assertIn('hao_runtime',rows)
        recipes=(TOP/'pipeline/sets/beta-v0.0.1.txt').read_text().splitlines()
        self.assertIn('hao-runtime-fold-catalog',recipes)
        self.assertNotIn('hao-runtime',recipes)

    def test_cache_rebuild_is_an_explicit_post_install_input(self):
        config = R['cache_rebuild_config'](ALPHA_ASSEMBLY)
        rows = dict(R['release_rows'](ALPHA_ASSEMBLY))
        self.assertEqual(config['build'], '26B131')
        self.assertEqual(len(config['files']), 36)
        for row in config['rows'].values():
            self.assertIn(row, rows)
            self.assertEqual(R['classes']()[row]['class'], 'ours')
        self.assertNotIn('rebuilt_cache_catalog', config['rows'].values())
        self.assertIsNone(R['cache_rebuild_config'](ASSEMBLY))

    @unittest.skipUnless(STORE, 'needs the store')
    def test_dry_run_on_the_real_store(self):
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--dry-run', str(ASSEMBLY),
                            '--json'], capture_output=True, text=True)
        rows = json.loads(r.stdout)
        self.assertLessEqual({x['class'] for x in rows}, {'download', 'reproducible', 'reproducible-except', 'dirty',
                                                          'harvested', 'unrecorded', 'missing'})
        self.assertLessEqual({x['content'] for x in rows}, CONTENT | {'unclassified'})
        ok = all(x['content'] != 'unclassified' and (x['content'] != 'ours' or x['class'] in (
            'reproducible', 'reproducible-except')) for x in rows)
        self.assertEqual(r.returncode, 0 if ok else 1)


class Classes(unittest.TestCase):
    def test_every_entry_is_complete(self):
        data = R['classes']()
        for row, e in data.items():
            self.assertIn(e['class'], CONTENT, row)
            self.assertTrue(e.get('reason'), row)
            if e['class'] == 'ours':
                self.assertIn(e['publish'], ('file', 'tar'), row)
                self.assertTrue(e['publish'] == 'tar' or 'include' not in e, row)
            if e['class'] == 'made-on-mac':
                self.assertTrue(e['producer'], row)
                for i in e['inputs']:
                    self.assertIn(i, data, '%s: input %s is not classified' % (row, i))
            if e['class'] == 'download':
                self.assertTrue(e['url'].startswith('https://') and e['name'], row)
            for f in e.get('made_on_mac_files', []):
                self.assertIn(f['from_row'], data, row)

    def test_every_row_of_the_assembly_is_classified(self):
        data = R['classes']()
        self.assertEqual([r for r, _ in R['release_rows'](ASSEMBLY) if r not in data], [])


class Archive(unittest.TestCase):
    def folder(self):
        d = Path(tempfile.mkdtemp())
        (d / 'sub').mkdir()
        (d / 'b.dylib').write_bytes(b'\xcf\xfa\xed\xfe' + bytes(60))
        os.chmod(d / 'b.dylib', 0o751)
        (d / 'sub' / 'a.json').write_text('{}')
        (d / 'build.json').write_text('{"where": "/' + 'Users/someone/x"}')
        os.symlink('b.dylib', d / 'link')
        return d

    def tar(self, d, include=None):
        b = io.BytesIO()
        R['write_archive'](b, d, include)
        return b.getvalue()

    def test_deterministic_and_normalised(self):
        d = self.folder()
        one = self.tar(d)
        os.utime(d / 'b.dylib', (time.time() - 99999, time.time() - 99999))
        self.assertEqual(one, self.tar(d))
        members = tarfile.open(fileobj=io.BytesIO(one)).getmembers()
        self.assertEqual([m.name for m in members], ['b.dylib', 'build.json', 'link', 'sub', 'sub/a.json'])
        for m in members:
            self.assertEqual((m.mtime, m.uid, m.gid, m.uname, m.gname), (0, 0, 0, '', ''))
        modes = {m.name: m.mode for m in members}
        self.assertEqual((modes['b.dylib'], modes['sub/a.json'], modes['sub']), (0o755, 0o644, 0o755))
        self.assertTrue(next(m for m in members if m.name == 'link').issym())

    def test_include_takes_the_listed_files_and_their_folders(self):
        d = self.folder()
        names = [m.name for m in tarfile.open(fileobj=io.BytesIO(self.tar(d, ['sub/a.json']))).getmembers()]
        self.assertEqual(names, ['sub', 'sub/a.json'])
        with self.assertRaises(ValueError):
            self.tar(d, ['missing.dylib'])

    def test_an_asset_with_a_private_path_is_refused(self):
        d = self.folder()
        hit = (TOP, ('-', str(d)))
        _, size, _, bad = R['make_asset']('x', hit, {'publish': 'tar'})
        self.assertIsNone(size)
        self.assertTrue(any('build.json' in b and 'home folder' in b for b in bad), bad)
        stage = Path(tempfile.mkdtemp())
        name, size, digest, bad = R['make_asset']('x', hit, {'publish': 'tar', 'include': ['b.dylib', 'sub']}, stage)
        self.assertEqual(bad, [])
        self.assertEqual((name, size, digest), ('x.tar', (stage / 'x.tar').stat().st_size,
                                                R['sha256_file'](stage / 'x.tar')))
        self.assertEqual(R['make_asset']('x', hit, {'publish': 'tar', 'include': ['b.dylib', 'sub']})[1:3],
                         (size, digest))                  # hashed without staging: the same archive

    def test_a_tar_asset_must_carry_everything_its_row_sha256_covers(self):
        d = self.folder()
        (d / 'build.json').write_text('{}')
        lk = runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))
        entry = {'publish': 'tar', 'include': ['b.dylib', 'sub']}
        whole = lk['row_sha256'](str(d))                       # a row without "# hash: release-files"
        bad = R['make_asset']('x', (TOP, (whole, str(d))), entry)[3]
        self.assertTrue(any('release-files' in b for b in bad), bad)
        scoped = lk['row_sha256'](str(d), ['b.dylib', 'sub'])  # the same row with it
        self.assertEqual(R['make_asset']('x', (TOP, (scoped, str(d))), entry)[3], [])

    def test_a_file_asset_must_be_the_pinned_file(self):
        d = self.folder()
        hit = (TOP, ('0' * 64, str(d / 'b.dylib')))
        bad = R['make_asset']('x', hit, {'publish': 'file'})[3]
        self.assertTrue(any('pinned' in b for b in bad), bad)

    def test_public_archive_can_have_a_distinct_variant_filename(self):
        d = self.folder(); source=d/'b.dylib'
        hit=(TOP,(R['sha256_file'](source),str(source)))
        entry={'publish':'file','file':'runtime-fold.tar.gz'}
        name,size,digest,bad=R['make_asset']('fold',hit,entry)
        self.assertEqual(name,'runtime-fold.tar.gz');self.assertEqual(bad,[])
        self.assertEqual(digest,R['sha256_file'](source))
        for name in ('../escape','/absolute','a/b',''):
            self.assertTrue(R['make_asset']('fold',hit,dict(entry,file=name))[3])

    def test_check_stage(self):
        stage = Path(tempfile.mkdtemp())
        (stage / 'a.dylib').write_bytes(b'abc')
        rel = {'assets': [{'file': 'a.dylib', 'size': 3, 'sha256': R['sha256_file'](stage / 'a.dylib')}]}
        self.assertEqual(R['check_stage'](rel, stage), [])
        sums = (Path(stage) / 'SHA256SUMS').read_text().splitlines() if (Path(stage) / 'SHA256SUMS').exists() else []
        self.assertEqual(len(sums), len(set(l.split()[1] for l in sums)), 'each file listed once')
        (stage / 'release.json').write_text('{}')
        (stage / '.DS_Store').write_text('')
        self.assertEqual(R['check_stage'](rel, stage), [])
        for sig in ('release.json.sig', 'SHA256SUMS.sig'):           # D5: the owner's signatures
            (stage / sig).write_text('untrusted comment: x\n')
        self.assertEqual(R['check_stage'](rel, stage), [])
        R['write_sums'](stage, stage / 'release.json')
        listed = [l.split()[1] for l in (stage / 'SHA256SUMS').read_text().splitlines()]
        self.assertEqual(listed, ['a.dylib', 'release.json'])
        (stage / 'extra.bin').write_text('x')
        self.assertEqual(R['check_stage'](rel, stage), ['extra.bin is staged but not an asset'])
        (stage / 'a.dylib').write_bytes(b'abd')
        self.assertIn('staged a.dylib is not the file release.json pins', R['check_stage'](rel, stage))


class Reproductions(unittest.TestCase):
    def test_entries_name_their_artifact_and_a_known_commit(self):
        data = json.loads((TOP / 'pipeline/reproductions.json').read_text())
        classes = json.loads((TOP / 'pipeline/release-classes.json').read_text())
        for row, e in data['rows'].items():
            # a harvested row is never rebuilt: its entry only names the commit of the tool or recipe that produced it
            allowed = ('identical', 'except', 'harvested') if classes['rows'].get(row, {}).get('class') == 'harvested' else ('identical', 'except')
            self.assertIn(e['result'], allowed, row)
            self.assertTrue(e['result'] != 'except' or e.get('except'), row)
            self.assertTrue(R['commit_known'](e['commit']), '%s: commit %s not found' % (row, e['commit']))

    def test_a_record_for_another_artifact_is_ignored(self):
        self.assertIsNone(R['reproduction']('io80211_compat', 'io80211-compat-some-other-build'))
        self.assertIsNotNone(R['reproduction']('io80211_compat', 'io80211-compat-26B131-20261111-c0558983229b'))


class HarvestedAssets(unittest.TestCase):
    """Shipping harvested rows is a per-target setting in release-classes.json, off unless set true."""

    def test_off_by_default_and_per_target(self):
        self.assertFalse(R['harvested_ship']({}, 'mbp92'))
        self.assertFalse(R['harvested_ship']({'harvested_assets': {'mbp92': False}}, 'mbp92'))
        self.assertTrue(R['harvested_ship']({'harvested_assets': {'mbp92': True}}, 'mbp92'))
        self.assertFalse(R['harvested_ship']({'harvested_assets': {'mbp92': True}}, 'mbp121'))
        self.assertFalse(R['harvested_ship']({'harvested_assets': {'mbp92': 'yes'}}, 'mbp92'))

    def test_only_a_public_repository_is_cited(self):
        notes = []
        self.assertEqual(R['public_provenance']('a', 'hao-patcher', 'c' * 40, notes), ('hao-patcher', 'c' * 40))
        self.assertEqual(R['public_provenance']('b', 'hao-runtime', 'd' * 40, notes), ('hao-runtime', 'd' * 40))
        self.assertEqual(notes, [])
        self.assertEqual(R['public_provenance']('c', 'elsewhere', 'e' * 40, notes), (None, ''))
        self.assertEqual(R['public_provenance']('d', None, 'f' * 40, notes), (None, ''))
        self.assertEqual(len(notes), 2)
        self.assertNotIn('elsewhere', ' '.join(notes))

    def test_a_recipe_of_a_release_set_that_needs_a_harvested_row_is_optional(self):
        # a cut without harvested assets must still build: every recipe that reads one is skipped like the caches
        data = R['classes']()
        harvested = {r for r, e in data.items() if isinstance(e, dict) and e.get('class') == 'harvested'}
        for assembly in (TOP / 'pipeline/assemblies').glob('mbp92-26B131*.json'):
            name = json.loads(assembly.read_text())['patch_set']
            for line in (TOP / 'pipeline/sets' / (name + '.txt')).read_text().splitlines():
                recipe = line.split('#')[0].strip()
                if not recipe:
                    continue
                patch = json.loads((TOP / 'patches' / recipe / 'patch.json').read_text())
                if harvested & set(patch.get('inputs', [])):
                    self.assertTrue(patch.get('optional'), '%s in %s' % (recipe, name))

    def test_the_checked_in_setting_ships_them_for_the_9_2_only(self):
        # owner 2026-11-15: all four harvested rows, as public assets, for MacBookPro9,2 only
        targets = {k: v for k, v in R['settings']()['harvested_assets'].items() if not k.startswith('_')}
        self.assertEqual(targets, {'mbp92': True})


class Write(unittest.TestCase):
    @unittest.skipUnless(STORE, 'needs the store')
    def test_release_written_and_staged(self):
        work = Path(tempfile.mkdtemp())
        out, stage = work / 'release.json', work / 'stage'
        cmd = [sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--write', str(out), str(ASSEMBLY),
               '--release', '27.2.1-26B131.0', '--channel', 'dev', '--version', 'v0.1.0', '--stage', str(stage)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:                                # incomplete (pending rows): refused, nothing left staged
            self.assertIn('refused', r.stderr)
            self.assertFalse(out.exists())
            self.assertEqual(list(stage.iterdir()), [])
            r = subprocess.run(cmd + ['--allow-pending'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rel = json.loads(out.read_text())
        self.assertEqual((rel['contract_version'], rel['release'], rel['prerelease'], rel['channel'], rel['version']),
                         (2, '27.2.1-26B131.0', True, 'dev', 'v0.1.0'))
        self.assertIn('MacBookPro9,2', rel['proven_models'])
        self.assertIn('golden-gate-hello', rel['recipes'])
        placed = [x['row'] for k in ('downloads', 'assets', 'made_on_mac') for x in rel[k]] + rel['harvested']
        self.assertEqual(len(placed), len(set(placed)))
        for x in rel['downloads']:
            self.assertTrue(x['url'].startswith('https://') and isinstance(x['size'], int), x['row'])
        prefix = 'https://' + 'github.com/lythka/hao-assets/releases/download/27.2.1-26B131.0/'
        data = R['classes']()
        for x in rel['assets']:
            self.assertEqual(x['url'], data[x['row']].get('url') or prefix + x['file'])   # or its own host's
            self.assertTrue(len(x['commit']) == 40 and x.get('repo') in R['PUBLIC_REPOS'] or
                            (x['commit'] == '' and 'repo' not in x), x['row'])
        self.assertEqual(R['check_stage'](rel, stage), [])
        for x in rel['made_on_mac']:
            self.assertNotIn('url', x)
        data = R['classes']()
        self.assertTrue(all(data[x['row']]['class'] == ('harvested' if x.get('harvested') else 'ours')
                            for x in rel['assets']))
        self.assertEqual(rel['target'], 'mbp92')

    def test_channel_and_version_are_required_and_checked(self):
        base = [sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--write', '/nonexistent/r.json', str(ASSEMBLY),
                '--release', '27.2.1-26B131.0']
        self.assertNotEqual(subprocess.run(base, capture_output=True).returncode, 0)          # no channel, version
        r = subprocess.run(base + ['--channel', 'nightly', '--version', 'v0.1.0'], capture_output=True, text=True)
        self.assertIn("channel 'nightly'", r.stderr)
        r = subprocess.run(base + ['--channel', 'dev', '--version', '0.1'], capture_output=True, text=True)
        self.assertIn("version '0.1'", r.stderr)
        r = subprocess.run(base + ['--channel', 'beta', '--version', 'v0.1.0', '--final'], capture_output=True,
                           text=True)
        self.assertIn('no final channel', r.stderr)

    @unittest.skipUnless(STORE, 'needs the store')
    def test_a_beta_is_never_written_incomplete(self):
        work = Path(tempfile.mkdtemp())
        out = work / 'release.json'
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--write', str(out), str(ASSEMBLY),
                            '--release', '27.2.1-26B131.0', '--channel', 'beta', '--version', 'v0.1.0',
                            '--allow-pending'], capture_output=True, text=True)
        if r.returncode:                    # today the runtime's public archive is missing: refused despite the flag
            self.assertIn('a beta is never written incomplete', r.stderr)
            self.assertFalse(out.exists())
        else:
            self.assertEqual(json.loads(out.read_text())['state'], 'complete')

    def test_a_release_needs_a_name(self):
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--write', '/nonexistent/r.json',
                            str(ASSEMBLY)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        with self.assertRaises(ValueError):
            R['build_release'](ASSEMBLY, '27.2.1')

    def test_only_release_profiles_are_cut(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / 'a.json'
            a.write_text(json.dumps(dict(json.loads(ASSEMBLY.read_text()), patch_set='dev-bs-test')))
            rel, problems = R['build_release'](a, '27.2.1-26B131.0')
        self.assertIn('profile dev-bs-test: not marked "release": true', problems)

    def test_dev_cuts_only_a_candidate(self):
        with self.assertRaises(ValueError):
            R['build_release'](DEV_ASSEMBLY, '27.2.1-26B131.0', dev=True)
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--write', '/nonexistent/r.json',
                            str(DEV_ASSEMBLY), '--release', '27.2.1-26B131.0', '--channel', 'dev', '--version',
                            'v0.0.1', '--dev'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn('--candidate --dev', r.stderr)

    def test_dev_refuses_a_release_profile(self):
        rel, problems = R['build_release'](ALPHA_ASSEMBLY, '27.2.1-26B131.0', candidate=True, dev=True)
        self.assertIn('profile alpha-v0.1.0: a release profile is cut without --dev', problems)

    def test_a_dev_candidate_is_marked_and_allows_the_dev_pack(self):
        rel, problems = R['build_release'](DEV_ASSEMBLY, '27.2.1-26B131.0', candidate=True, dev=True)
        self.assertIs(rel['dev'], True)
        self.assertIs(rel['candidate'], True)
        self.assertEqual([p for p in problems if 'dev-layer' in p or 'status dev' in p or 'release": true' in p
                          or 'dev-local' in p], [])
        self.assertEqual({x['row'] for x in rel['dev_local']},
                         {'test_host_key', 'test_authorized_key', 'screensharing_users'})
        self.assertTrue(any('dev-access' in u for u in rel['unvalidated']))
        # without --dev the same profile is refused, and its private rows are problems, never assets
        plain, plain_problems = R['build_release'](DEV_ASSEMBLY, '27.2.1-26B131.0', candidate=True)
        self.assertNotIn('dev', plain)
        self.assertNotIn('dev_local', plain)
        self.assertIn('profile dev-alpha-v0.1.0: not marked "release": true', plain_problems)
        self.assertIn('test_host_key: dev-local, cut only into a dev candidate (--candidate --dev)', plain_problems)
        self.assertNotIn('test_host_key', [x['row'] for x in plain['assets'] + rel['assets']])

    def test_release_cut_refuses_an_experiment_recipe(self):
        # release-cut runs release_problems with the validated/experiment gate on; here against a small tree whose
        # release profile holds one experiment recipe.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'patches/exp-a').mkdir(parents=True)
            (root / 'patches/exp-a/patch.json').write_text(json.dumps(
                {'revision': 3, 'pack': 'base', 'stage': 'post', 'status': 'experiment', 'validated': True}))
            (root / 'packs').mkdir()
            (root / 'packs/base.json').write_text(json.dumps(
                {'format': 1, 'phase': 'installer', 'layer': 'os', 'detect': {'any_intel': True}}))
            (root / 'profiles').mkdir()
            (root / 'profiles/rel.json').write_text(json.dumps({'target': 'mbp92', 'release': True}))
            (root / 'order.txt').write_text('exp-a\n')
            real = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
            packs = real['load_packs'](str(root / 'packs'), str(root / 'patches'))
            real['generate'].__globals__['detect_packs'] = lambda target, packs=None: ['base']
            fixture = lambda name, **kw: real['release_problems'](
                'rel', str(root / 'patches'), packs, str(root / 'profiles'), str(root / 'order.txt'), waivers=[], gate=kw.get('gate', True))
            g = R['build_release'].__globals__
            saved = g['runpy']
            g['runpy'] = type('runpy', (), {'run_path': staticmethod(
                lambda path, *a, **k: dict(saved.run_path(path, *a, **k), release_problems=fixture)
                if str(path).endswith('patches.py') else saved.run_path(path, *a, **k))})
            try:
                rel, problems = R['build_release'](ASSEMBLY, '27.2.1-26B131.0')
                cand, cand_problems = R['build_release'](ASSEMBLY, '27.2.1-26B131.0', candidate=True)
            finally:
                g['runpy'] = saved
        why = 'profile rel: exp-a r3 is status experiment (no owner waiver in pipeline/release-waivers.json)'
        self.assertIn(why, problems)
        self.assertFalse(rel['candidate'])
        # a candidate cut (proof images) allows it, says so, and lists it
        self.assertNotIn(why, cand_problems)
        self.assertTrue(cand['candidate'])
        self.assertEqual(cand['unvalidated'], [why])


class Versions(unittest.TestCase):
    def test_a_release_version_may_carry_an_alpha_or_beta_number(self):
        C = runpy.run_path(str(TOP / 'tools/hao-contract/hao_contract.py'))
        for pattern in (R['VERSION'], C['RELEASE_VERSION']):
            for ok in ('v0.1.0', 'v0.1.0-alpha.1', 'v1.2.3-beta.12'):
                self.assertTrue(pattern.match(ok), ok)
            for bad in ('0.1.0', 'v0.1', 'v0.1.0-alpha', 'v0.1.0-rc.1', 'v0.1.0-alpha.1x'):
                self.assertFalse(pattern.match(bad), bad)


class PendingRows(unittest.TestCase):
    """--allow-pending-row ROW lets only that row's problems through; anything else still refuses."""
    def cut(self, channel, *extra):
        g = R['main'].__globals__
        saved = g['build_release']
        rel = {'channel': channel, 'state': 'incomplete', 'downloads': [], 'assets': [], 'made_on_mac': [],
               'harvested': [], 'pending': [{'row': 'a'}, {'row': 'b'}]}
        g['build_release'] = lambda *a, **k: (rel, ['a: pending (x)', 'b: pending (y)'])
        try:
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / 'r.json'
                rc = R['main'](['--write', str(out), str(ASSEMBLY), '--release', '27.2.1-26B131.0', '--channel',
                                channel, '--version', 'v0.1.0', *extra])
                return rc, out.exists()
        finally:
            g['build_release'] = saved

    def test_only_the_named_rows_may_be_pending(self):
        self.assertEqual(self.cut('alpha'), (1, False))
        self.assertEqual(self.cut('alpha', '--allow-pending-row', 'a'), (1, False))
        self.assertEqual(self.cut('alpha', '--allow-pending-row', 'a', '--allow-pending-row', 'b'), (0, True))
        self.assertEqual(self.cut('beta', '--allow-pending-row', 'a', '--allow-pending-row', 'b'), (1, False))


def one_cut(profile, target, assets, downloads=(), harvested=(), problems=()):
    """A one-assembly release as build_release returns it (the fields merge_releases reads)."""
    rows = sorted([a['row'] for a in assets] + [d['row'] for d in downloads])
    rel = {'contract_version': 2, 'release': '27.2.1-26B131.0', 'version': 'v0.0.1', 'channel': 'alpha',
           'prerelease': True, 'golden_build': '26B131', 'donor_build': '25G342', 'oclp': 'fd83bda09',
           'hao_patcher': 'c' * 40, 'hao_runtime': '', 'state': 'complete' if not problems else 'incomplete',
           'proven_models': ['MacBookPro9,2'], 'assembly': '%s-26B131' % target, 'target': target,
           'patch_set': profile, 'recipes': {'base-recipe': 1}, 'dev_only_recipes': [],
           'downloads': list(downloads), 'assets': list(assets), 'made_on_mac': [], 'harvested': list(harvested),
           'in_checkout': [], 'pending': [], 'notes': [], 'expected': {}}
    rel['profiles'] = {profile: {'assembly': rel['assembly'], 'target': target, 'patch_set': profile,
                                 'recipes': rel['recipes'], 'dev_only_recipes': [],
                                 'proven_models': ['MacBookPro9,2'] if target == 'mbp92' else [], 'rows': rows,
                                 'harvested': sorted(harvested)}}
    return rel, list(problems)


def asset(row, data, harvested=False):
    import hashlib
    x = {'row': row, 'file': row + '.tar', 'url': R['ASSET_URL'] % ('27.2.1-26B131.0', row + '.tar'),
         'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'commit': '', 'format': 'tar'}
    if harvested:
        x['harvested'] = True
    return x


DATA = {'runtime': b'shared runtime', 'esp_mbp92': b'9,2 only', 'caches': b'harvested 9,2 caches',
        'esp_generic': b'generic only'}
KDK = {'row': 'kdk', 'name': 'KDK.dmg', 'url': 'https://example.invalid/KDK.dmg', 'size': 1, 'sha256': '1' * 64}


def two_profiles():
    nine = one_cut('alpha-v0.1.0', 'mbp92', [asset('runtime', DATA['runtime']), asset('esp_mbp92', DATA['esp_mbp92']),
                                             asset('caches', DATA['caches'], harvested=True)], [KDK])
    generic = one_cut('generic', 'generic', [asset('runtime', DATA['runtime']), asset('esp_generic', DATA['esp_generic'])],
                      [KDK], harvested=['caches'])
    return [nine, generic]


class Profiles(unittest.TestCase):
    """One release, several profiles (the 9,2 image and the generic one), cut from one commit."""

    def test_cache_rebuild_survives_merge_and_conflicts_refuse(self):
        cuts = two_profiles()
        config = R['cache_rebuild_config'](ALPHA_ASSEMBLY)
        cuts[1][0]['cache_rebuild'] = config
        result, _ = R['merge_releases'](cuts)
        self.assertEqual(result['cache_rebuild'], config)
        cuts[0][0]['cache_rebuild'] = dict(config, cache_uuid='different')
        with self.assertRaisesRegex(ValueError, 'different cache rebuild'):
            R['merge_releases'](cuts)

    def test_shared_assets_once_each_profile_says_its_rows(self):
        rel, problems = R['merge_releases'](two_profiles())
        self.assertEqual(problems, [])
        self.assertEqual([x['row'] for x in rel['assets']], ['runtime', 'esp_mbp92', 'caches', 'esp_generic'])
        self.assertEqual([x['row'] for x in rel['downloads']], ['kdk'])
        self.assertEqual(rel['harvested'], [])                       # shipped for the 9,2: listed once, as an asset
        self.assertEqual(sorted(rel['profiles']), ['alpha-v0.1.0', 'generic'])
        self.assertEqual(rel['profiles']['generic']['rows'], ['esp_generic', 'kdk', 'runtime'])
        self.assertEqual(rel['profiles']['generic']['harvested'], ['caches'])
        self.assertEqual(rel['profiles']['alpha-v0.1.0']['rows'], ['caches', 'esp_mbp92', 'kdk', 'runtime'])
        # the top level is the first profile's image, as a reader without "profiles" knows it
        self.assertEqual((rel['assembly'], rel['target'], rel['patch_set']), ('mbp92-26B131', 'mbp92', 'alpha-v0.1.0'))
        C = runpy.run_path(str(TOP / 'tools/hao-contract/hao_contract.py'))
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'release.json').write_text(json.dumps(rel))
            self.assertEqual(C['check_release_file'](Path(d) / 'release.json'), [])

    def test_each_profile_entry_names_its_proven_models(self):
        patches = runpy.run_path(str(TOP / 'pipeline/patches.py'))
        packs = patches['load_packs']()
        for target, proven in (('mbp92', ['MacBookPro9,2']), ('generic', [])):
            rel = dict(assembly='x', target=target, patch_set='s', recipes={}, dev_only_recipes=[], made_on_mac=[],
                       downloads=[KDK], assets=[asset('runtime', b'r')], harvested=['caches'])
            entry = R['profile_entry'](rel, patches, packs)
            self.assertEqual((entry['proven_models'], entry['rows'], entry['harvested']),
                             (proven, ['kdk', 'runtime'], ['caches']), target)

    def test_a_row_two_profiles_make_differently_is_a_problem(self):
        cuts = two_profiles()
        cuts[1][0]['assets'][0] = asset('runtime', b'another build')
        rel, problems = R['merge_releases'](cuts)
        self.assertIn('runtime: the profiles place it differently in assets', problems)
        self.assertEqual(rel['state'], 'incomplete')

    def test_one_macos_build_per_release(self):
        cuts = two_profiles()
        cuts[1][0]['golden_build'] = '26C5'
        with self.assertRaises(ValueError):
            R['merge_releases'](cuts)
        cuts = two_profiles()
        cuts[1][0]['profiles'] = cuts[0][0]['profiles']
        with self.assertRaises(ValueError):
            R['merge_releases'](cuts)

    def test_written_staged_and_summed_once(self):
        g = R['main'].__globals__
        saved = g['build_release']
        infrastructure = g['add_builder_assets']
        g['add_builder_assets'] = lambda rel, stage: None  # this test isolates profile merging
        cuts = dict(zip(('mbp92', 'generic'), two_profiles()))

        def fake(assembly, name, stage=None, **kw):
            rel, problems = cuts[Path(assembly).stem]
            for x in rel['assets']:
                (Path(stage) / x['file']).write_bytes(DATA[x['row']])
            return json.loads(json.dumps(rel)), problems
        g['build_release'] = fake
        try:
            with tempfile.TemporaryDirectory() as d:
                out, stage = Path(d) / 'release.json', Path(d) / 'stage'
                rc = R['main'](['--write', str(out), 'mbp92.json', 'generic.json', '--release', '27.2.1-26B131.0',
                                '--channel', 'alpha', '--version', 'v0.0.1', '--stage', str(stage)])
                self.assertEqual(rc, 0)
                rel = json.loads(out.read_text())
                self.assertEqual(sorted(rel['profiles']), ['alpha-v0.1.0', 'generic'])
                sums = (stage / 'SHA256SUMS').read_text().splitlines()
                self.assertEqual(sorted(l.split()[1] for l in sums),
                                 sorted(['caches.tar', 'esp_generic.tar', 'esp_mbp92.tar', 'runtime.tar', 'release.json'])
                                 if (stage / 'release.json').exists() else
                                 ['caches.tar', 'esp_generic.tar', 'esp_mbp92.tar', 'runtime.tar'])
                self.assertEqual(R['check_stage'](rel, stage), [])
        finally:
            g['build_release'] = saved
            g['add_builder_assets'] = infrastructure


if __name__ == '__main__':
    unittest.main()


class UpstreamAssets(unittest.TestCase):
    def test_upstream_source_keeps_its_authors_home_folders_but_not_our_private_names(self):
        home = ('/' 'Users/' 'someone/Downloads/tool').encode()
        self.assertEqual(R['private_refs'](home), ['home folder'])
        self.assertEqual(R['private_refs'](home, upstream=True), [])

    def test_only_oclp_source_is_declared_upstream(self):
        classes = json.loads((TOP / 'pipeline/release-classes.json').read_text())
        rows = classes.get('rows', classes)
        self.assertEqual(sorted(k for k, v in rows.items() if isinstance(v, dict) and v.get('upstream')), ['oclp_source'])
