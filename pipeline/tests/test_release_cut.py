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
CONTENT = {'ours', 'made-on-mac', 'download', 'harvested', 'in-checkout', 'pending'}
STORE = (TOP / 'store/artifacts').is_dir()


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

    def test_a_file_asset_must_be_the_pinned_file(self):
        d = self.folder()
        hit = (TOP, ('0' * 64, str(d / 'b.dylib')))
        bad = R['make_asset']('x', hit, {'publish': 'file'})[3]
        self.assertTrue(any('pinned' in b for b in bad), bad)

    def test_check_stage(self):
        stage = Path(tempfile.mkdtemp())
        (stage / 'a.dylib').write_bytes(b'abc')
        rel = {'assets': [{'file': 'a.dylib', 'size': 3, 'sha256': R['sha256_file'](stage / 'a.dylib')}]}
        self.assertEqual(R['check_stage'](rel, stage), [])
        (stage / 'release.json').write_text('{}')
        (stage / '.DS_Store').write_text('')
        self.assertEqual(R['check_stage'](rel, stage), [])
        (stage / 'extra.bin').write_text('x')
        self.assertEqual(R['check_stage'](rel, stage), ['extra.bin is staged but not an asset'])
        (stage / 'a.dylib').write_bytes(b'abd')
        self.assertIn('staged a.dylib is not the file release.json pins', R['check_stage'](rel, stage))


class Reproductions(unittest.TestCase):
    def test_entries_name_their_artifact_and_a_known_commit(self):
        data = json.loads((TOP / 'pipeline/reproductions.json').read_text())
        for row, e in data['rows'].items():
            self.assertIn(e['result'], ('identical', 'except'), row)
            self.assertTrue(e['result'] != 'except' or e.get('except'), row)
            self.assertTrue(R['commit_known'](e['commit']), '%s: commit %s not found' % (row, e['commit']))

    def test_a_record_for_another_artifact_is_ignored(self):
        self.assertIsNone(R['reproduction']('io80211_compat', 'io80211-compat-some-other-build'))
        self.assertIsNotNone(R['reproduction']('io80211_compat', 'io80211-compat-26B131-20261111-c0558983229b'))


class Write(unittest.TestCase):
    @unittest.skipUnless(STORE, 'needs the store')
    def test_release_written_and_staged(self):
        work = Path(tempfile.mkdtemp())
        out, stage = work / 'release.json', work / 'stage'
        cmd = [sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--write', str(out), str(ASSEMBLY),
               '--release', '27.2.1-26B131.0', '--stage', str(stage)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:                                # incomplete (pending rows): refused, nothing left staged
            self.assertIn('refused', r.stderr)
            self.assertFalse(out.exists())
            self.assertEqual(list(stage.iterdir()), [])
            r = subprocess.run(cmd + ['--allow-pending'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rel = json.loads(out.read_text())
        self.assertEqual((rel['contract_version'], rel['release'], rel['prerelease']), (1, '27.2.1-26B131.0', True))
        self.assertIn('MacBookPro9,2', rel['proven_models'])
        self.assertIn('golden-gate-hello', rel['recipes'])
        placed = [x['row'] for k in ('downloads', 'assets', 'made_on_mac') for x in rel[k]] + rel['harvested']
        self.assertEqual(len(placed), len(set(placed)))
        for x in rel['downloads']:
            self.assertTrue(x['url'].startswith('https://') and isinstance(x['size'], int), x['row'])
        prefix = 'https://' + 'github.com/lythka/hao-assets/releases/download/27.2.1-26B131.0/'
        for x in rel['assets']:
            self.assertEqual(x['url'], prefix + x['file'])
            self.assertEqual(len(x['commit']), 40, x['row'])
        self.assertEqual(R['check_stage'](rel, stage), [])
        for x in rel['made_on_mac']:
            self.assertNotIn('url', x)
        data = R['classes']()
        self.assertTrue(all(data[x['row']]['class'] == 'ours' for x in rel['assets']))

    def test_a_release_needs_a_name(self):
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--write', '/nonexistent/r.json',
                            str(ASSEMBLY)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        with self.assertRaises(ValueError):
            R['build_release'](ASSEMBLY, '27.2.1')


if __name__ == '__main__':
    unittest.main()
