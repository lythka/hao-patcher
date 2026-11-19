"""hao verify-release (src/releasecheck.py): a good, a tampered and an unsigned release, as a folder, as vX.Y.Z (a fake
GitHub) and as a USB installer; PASS, FAIL and SKIP per check; exit 4 on a mismatch."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
import urllib.error

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
sys.path.insert(0, str(TOOL / 'src'))
sys.path.insert(0, str(HERE))
import releasecheck as RC  # noqa: E402
import signing as SIGN     # noqa: E402
from test_signing import sshsig_files  # noqa: E402

SECRET = b'\x21' * 32
COMMIT = 'a' * 40


def sign_into(folder, name):
    data = (Path(folder) / name).read_bytes()
    (Path(folder) / (name + '.sig')).write_text(sshsig_files(SECRET, data)[1])


KEYS = SIGN.parse_keys(sshsig_files(SECRET, b'')[0])

# these tests judge signatures: no owner waiver applies unless a test names one (Unsigned below)
_NO_WAIVERS = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
_NO_WAIVERS.write('{"releases": {}}')
_NO_WAIVERS.close()
SIGN.UNSIGNED_LIST = Path(_NO_WAIVERS.name)


def release_folder(d, asset=b'asset bytes', sign=True):
    d = Path(d)
    (d / 'dss.dylib').write_bytes(asset)
    rel = {'release': '27.2.1-26B131.0', 'version': 'v0.0.1', 'hao_patcher': COMMIT,
           'assets': [{'file': 'dss.dylib', 'sha256': hashlib.sha256(b'asset bytes').hexdigest(), 'size': 11}],
           'downloads': [{'name': 'big.dmg', 'sha256': 'b' * 64, 'size': 1}]}
    (d / 'release.json').write_text(json.dumps(rel))
    sums = '%s  release.json\n%s  dss.dylib\n' % (hashlib.sha256((d / 'release.json').read_bytes()).hexdigest(),
                                                   hashlib.sha256(b'asset bytes').hexdigest())
    (d / 'SHA256SUMS').write_text(sums)
    if sign:
        sign_into(d, 'release.json')
        sign_into(d, 'SHA256SUMS')
    return d


def github(tag_commit=COMMIT, files=None):
    """An opener answering GitHub's tag API and release downloads from FILES {url suffix: bytes}."""
    def opener(url, limit=None):
        if '/git/ref/tags/' in url:
            if tag_commit is None:
                raise urllib.error.HTTPError(url, 404, 'Not Found', {}, io.BytesIO())
            return json.dumps({'object': {'type': 'tag', 'sha': 't' * 40}}).encode()
        if '/git/tags/' in url:
            return json.dumps({'object': {'type': 'commit', 'sha': tag_commit}}).encode()
        for suffix, data in (files or {}).items():
            if url.endswith(suffix):
                return data
        raise urllib.error.HTTPError(url, 404, 'Not Found', {}, io.BytesIO())
    return opener


def states(checks):
    return [(k['name'], k['state']) for k in checks]


class Folder(unittest.TestCase):
    def test_a_good_release_passes(self):
        with tempfile.TemporaryDirectory() as d:
            checks = RC.check_folder(release_folder(d), KEYS, opener=github())
            self.assertNotIn('FAIL', [s for _, s in states(checks)], checks)
            self.assertIn(('tag', 'PASS'), states(checks))
            self.assertIn(('sums', 'PASS'), states(checks))
            self.assertIn(('files', 'PASS'), states(checks))
            self.assertIn(('files', 'SKIP'), states(checks))      # big.dmg is not here

    def test_a_tampered_file_fails(self):
        with tempfile.TemporaryDirectory() as d:
            checks = RC.check_folder(release_folder(d, asset=b'asset bytez'), KEYS, opener=github())
            self.assertIn(('files', 'FAIL'), states(checks))

    def test_a_changed_release_json_fails_its_signature(self):
        with tempfile.TemporaryDirectory() as d:
            f = release_folder(d)
            (f / 'release.json').write_text((f / 'release.json').read_text().replace('v0.0.1', 'v0.0.2'))
            checks = RC.check_folder(f, KEYS, opener=github())
            self.assertIn(('signature', 'FAIL'), states(checks))
            self.assertNotIn(('sums', 'PASS'), states(checks))      # an unverified release.json is never compared
            self.assertNotIn(('tag', 'PASS'), states(checks))

    def test_an_unsigned_release_fails(self):
        with tempfile.TemporaryDirectory() as d:
            checks = RC.check_folder(release_folder(d, sign=False), KEYS, opener=github())
            self.assertEqual([s for n, s in states(checks) if n == 'signature'], ['FAIL', 'FAIL'])
            self.assertNotIn(('files', 'PASS'), states(checks))      # unsigned pins are never used

    def test_the_tag(self):
        with tempfile.TemporaryDirectory() as d:
            f = release_folder(d)
            self.assertIn(('tag', 'FAIL'), states(RC.check_folder(f, KEYS, opener=github(tag_commit='c' * 40))))
            self.assertIn(('tag', 'FAIL'), states(RC.check_folder(f, KEYS, opener=github(tag_commit=None))))
            self.assertIn(('tag', 'SKIP'), states(RC.check_folder(f, KEYS, offline=True)))
            self.assertIn(('tag', 'FAIL'), states(RC.check_folder(f, KEYS, offline=True, here='d' * 40)))
            self.assertIn(('tag', 'PASS'), states(RC.check_folder(f, KEYS, offline=True, here=COMMIT)))
            self.assertIn(('tag', 'FAIL'), states(RC.check_folder(f, KEYS, offline=True, tag='v0.0.2')))

    def test_a_pinned_name_taken_by_a_link_or_folder_fails(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as cache:
            f = release_folder(d)
            good = Path(cache) / 'sha256' / hashlib.sha256(b'asset bytes').hexdigest()
            good.parent.mkdir()
            good.write_bytes(b'asset bytes')                      # a good copy in the cache does not help
            (f / 'dss.dylib').unlink()
            (f / 'dss.dylib').symlink_to(good)
            checks = RC.check_folder(f, KEYS, cache=cache, offline=True)
            self.assertTrue(any(k['state'] == 'FAIL' and k.get('file') == 'dss.dylib' for k in checks), checks)

    def test_missing_release_json_fails_when_listed_or_when_a_tag_is_asked(self):
        with tempfile.TemporaryDirectory() as d:
            f = release_folder(d)
            (f / 'release.json').unlink()
            (f / 'release.json.sig').unlink()
            self.assertIn(('sums', 'FAIL'), states(RC.check_folder(f, KEYS, offline=True)))
            self.assertIn(('tag', 'FAIL'), states(RC.check_folder(f, KEYS, offline=True, tag='v0.0.1')))

    def test_a_signed_sums_file_with_a_bad_line_fails(self):
        with tempfile.TemporaryDirectory() as d:
            f = release_folder(d)
            (f / 'SHA256SUMS').write_text('nonsense\n')
            sign_into(f, 'SHA256SUMS')
            self.assertIn(('sums', 'FAIL'), states(RC.check_folder(f, KEYS, offline=True)))

    def test_downloads_in_the_cache_are_checked(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'rel').mkdir()
            f = release_folder(Path(d) / 'rel')
            (Path(d) / 'cache' / 'sha256').mkdir(parents=True)
            (Path(d) / 'cache' / 'sha256' / ('b' * 64)).write_bytes(b'not it')
            checks = RC.check_folder(f, KEYS, cache=Path(d) / 'cache', offline=True)
            self.assertTrue(any(k['state'] == 'FAIL' and k.get('file') == 'big.dmg' for k in checks))


class Tag(unittest.TestCase):
    def gh(self, f, tar_listed=True, assets_rel=None):
        """hao-patcher v0.0.1: SHA256SUMS(.sig) listing its tar; hao-assets 27.2.1-26B131.0: SHA256SUMS(.sig) listing
        release.json (ASSETS_REL: other release.json bytes)."""
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / 'SHA256SUMS').write_text('%s  hao-patcher-v0.0.1.tar\n' % ('c' * 64) if tar_listed else '')
            sign_into(t, 'SHA256SUMS')
            files = {'/hao-patcher/releases/download/v0.0.1/' + n: (t / n).read_bytes()
                     for n in ('SHA256SUMS', 'SHA256SUMS.sig')}
            (t / 'SHA256SUMS').write_text('%s  release.json\n' % hashlib.sha256(
                assets_rel or (f / 'release.json').read_bytes()).hexdigest())
            sign_into(t, 'SHA256SUMS')
            files.update({'/hao-assets/releases/download/27.2.1-26B131.0/' + n: (t / n).read_bytes()
                          for n in ('SHA256SUMS', 'SHA256SUMS.sig')})
        return github(files=files)

    def checks(self, f, opener, dest):
        got = RC.download('v0.0.1', opener, beside=f, dest=dest, keys=KEYS)
        return RC.check_folder(got, KEYS, tag='v0.0.1', opener=opener) + RC.tag_extra(got, KEYS, 'v0.0.1')

    def test_a_good_tag_passes(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dest:
            f = release_folder(src)
            checks = self.checks(f, self.gh(f), dest)
            self.assertNotIn('FAIL', [s for _, s in states(checks)], checks)
            self.assertEqual(sum(1 for k in checks if k['name'] == 'sums' and k['state'] == 'PASS'), 2)
            self.assertEqual(sum(1 for k in checks if k['name'] == 'signature' and k['state'] == 'PASS'), 3)

    def test_another_release_json_or_a_missing_archive_fails(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dest:
            f = release_folder(src)
            self.assertIn(('sums', 'FAIL'), states(self.checks(f, self.gh(f, assets_rel=b'other'), dest)))
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dest:
            f = release_folder(src)
            self.assertIn(('sums', 'FAIL'), states(self.checks(f, self.gh(f, tar_listed=False), dest)))

    def test_without_release_json_beside_hao_it_fails(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dest:
            f = release_folder(src)
            got = RC.download('v0.0.1', self.gh(f), beside=None, dest=dest, keys=KEYS)
            self.assertIn(('sums', 'FAIL'), states(RC.tag_extra(got, KEYS, 'v0.0.1')))


class Drive(unittest.TestCase):
    class Stick:
        def __init__(self, problems):
            self.problems = problems

        def check_manifest(self, top):
            return self.problems

    def drive(self, d, sign=True):
        f = release_folder(d, sign=sign)
        (f / 'stick.json').write_text('{}')
        (f / 'manifest.json').write_text(json.dumps({'format': 1, 'files': {}, 'links': {}, 'release_sha256':
                                                    hashlib.sha256((f / 'release.json').read_bytes()).hexdigest()}))
        return f

    def test_a_good_drive_passes(self):
        with tempfile.TemporaryDirectory() as d:
            checks = RC.check_drive(self.drive(d), KEYS, self.Stick([]))
            self.assertEqual([s for _, s in states(checks)], ['PASS', 'PASS', 'PASS'])

    def test_a_changed_drive_fails(self):
        with tempfile.TemporaryDirectory() as d:
            checks = RC.check_drive(self.drive(d), KEYS, self.Stick([('kit/x', 'a different size')]))
            self.assertIn(('drive', 'FAIL'), states(checks))

    def test_a_drive_whose_manifest_names_another_release_fails(self):
        with tempfile.TemporaryDirectory() as d:
            f = self.drive(d)
            (f / 'manifest.json').write_text(json.dumps({'release_sha256': '0' * 64}))
            self.assertIn(('drive', 'FAIL'), states(RC.check_drive(f, KEYS, self.Stick([]))))

    def test_an_unsigned_drive_and_one_without_a_release_fail(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn(('signature', 'FAIL'), states(RC.check_drive(self.drive(d, sign=False), KEYS,
                                                                        self.Stick([]))))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(states(RC.check_drive(Path(d), KEYS, self.Stick([]))), [('drive', 'FAIL')])


class RealDrive(unittest.TestCase):
    """With stick.py's own manifest (write_manifest / check_manifest): a USB installer as create-installer leaves it."""
    def test_a_written_drive_passes_and_a_changed_one_fails(self):
        import stick as STICK
        if not hasattr(STICK, 'write_manifest'):
            self.skipTest('the drive manifest (installer) is not in this tree yet')
        with tempfile.TemporaryDirectory() as d:
            top = release_folder(d)
            (top / 'stick.json').write_text('{}')
            (top / 'kit').mkdir()
            (top / 'kit' / 'kit.json').write_text('{"kit": 1}')
            STICK.write_manifest(top)
            checks = RC.check_drive(top, KEYS, STICK)
            self.assertNotIn('FAIL', [s for _, s in states(checks)], checks)
            (top / 'kit' / 'kit.json').write_text('{"kit": 2}')
            self.assertIn(('drive', 'FAIL'), states(RC.check_drive(top, KEYS, STICK)))


class Candidate(unittest.TestCase):
    """Candidate cuts are signed with the candidate key: trusted only by a hao that carries candidate-keys.pub
    (release-cut --candidate writes it into the cut); a final build has release-keys.pub only and refuses them."""
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        d = Path(self.d.name)
        self.release_pub, _ = sshsig_files(b'\41' * 32, b'')
        self.candidate_pub, _ = sshsig_files(b'\42' * 32, b'')
        (d / 'release-keys.pub').write_text(self.release_pub)
        (d / 'candidate-keys.pub').write_text(self.candidate_pub)
        self.data = json.dumps({'release': '27.2.1-26B131.0', 'version': 'v0.0.1', 'hao_patcher': COMMIT}).encode()
        self.sig = sshsig_files(b'\42' * 32, self.data)[1]

    def keys(self, candidate):
        d = Path(self.d.name)
        real = SIGN.KEYS, SIGN.CANDIDATE_KEYS
        SIGN.KEYS, SIGN.CANDIDATE_KEYS = d / 'release-keys.pub', d / ('candidate-keys.pub' if candidate else 'none')
        try:
            return SIGN.load_keys()
        finally:
            SIGN.KEYS, SIGN.CANDIDATE_KEYS = real

    def test_a_final_build_refuses_a_candidate_signed_release(self):
        with self.assertRaises(SIGN.SignatureError):
            SIGN.verify(self.data, self.sig, self.keys(candidate=False))

    def test_a_candidate_build_accepts_it_and_says_so(self):
        fp, _ = SIGN.verify(self.data, self.sig, self.keys(candidate=True))
        self.assertTrue(SIGN.is_candidate(fp))
        release_sig = sshsig_files(b'\41' * 32, self.data)[1]
        self.assertFalse(SIGN.is_candidate(SIGN.verify(self.data, release_sig, self.keys(candidate=True))[0]))

    def test_verify_release_prints_candidate(self):
        hao = runpy.run_path(str(TOOL / 'hao'), run_name='hao_module')
        import console as CON
        d = Path(self.d.name)
        with tempfile.TemporaryDirectory() as rel:
            f = Path(rel)
            (f / 'release.json').write_bytes(self.data)
            (f / 'release.json.sig').write_text(self.sig)
            real = SIGN.KEYS, SIGN.CANDIDATE_KEYS
            SIGN.KEYS, SIGN.CANDIDATE_KEYS = d / 'release-keys.pub', d / 'candidate-keys.pub'
            out = io.StringIO()
            try:
                c = CON.Console('steps', out=out, err=out, env={})
                hao['cmd_verify_release'](argparse.Namespace(target=str(f), cache=None, offline=True, keys=None), c)
            finally:
                SIGN.KEYS, SIGN.CANDIDATE_KEYS = real
        self.assertIn('CANDIDATE, not a release', out.getvalue())

    def test_a_checkouts_drive_gets_the_candidate_key(self):
        import subprocess
        import stick as STICK
        with tempfile.TemporaryDirectory() as d:
            top = Path(d)
            (top / 'tools/hao/data').mkdir(parents=True)
            (top / 'tools/hao/hao').write_text('x')
            run = lambda *a: subprocess.run(['/usr/bin/git', '-C', d] + list(a), capture_output=True, check=True)  # noqa
            run('init', '-q')
            run('add', 'tools/hao/hao')
            self.assertNotIn('tools/hao/data/candidate-keys.pub', STICK.hao_files(top))
            (top / 'tools/hao/data/candidate-keys.pub').write_text(self.candidate_pub)
            self.assertIn('tools/hao/data/candidate-keys.pub', STICK.hao_files(top))

    @unittest.skipUnless((TOOL.parents[1] / '.git').exists(), 'not a checkout')
    def test_the_repository_never_carries_candidate_keys(self):
        r = __import__('subprocess').run(['/usr/bin/git', '-C', str(TOOL.parents[1]), 'ls-files',
                                          'tools/hao/data/candidate-keys.pub'], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), '')


class Unsigned(unittest.TestCase):
    """Owner 2026-11-18: "Ship v0.0.1 as unsigned". A release on hao's waiver list with no signature file is used, said
    to be unsigned everywhere, and still checked against SHA256SUMS; anything else unsigned is refused."""
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        waivers = Path(self.d.name) / 'unsigned.json'
        waivers.write_text(json.dumps({'releases': {'v0.0.1': 'owner 2026-11-18: "Ship v0.0.1 as unsigned"'}}))
        self.real = SIGN.UNSIGNED_LIST
        SIGN.UNSIGNED_LIST = waivers
        self.addCleanup(setattr, SIGN, 'UNSIGNED_LIST', self.real)

    def folder(self, version='v0.0.1', asset=b'asset bytes'):
        f = Path(self.d.name) / version
        f.mkdir()
        release_folder(f, asset=asset, sign=False)
        if version != 'v0.0.1':
            rel = json.loads((f / 'release.json').read_text())
            rel['version'] = version
            (f / 'release.json').write_text(json.dumps(rel))
            (f / 'SHA256SUMS').write_text('%s  release.json\n' % hashlib.sha256((f / 'release.json').read_bytes())
                                         .hexdigest())
        return f

    def test_the_waived_release_is_used_and_said_unsigned(self):
        copy, kid, how = SIGN.verified_release(self.folder() / 'release.json', KEYS)
        self.assertTrue(SIGN.is_unsigned(kid))
        self.assertIn('Ship v0.0.1 as unsigned', kid)
        self.assertEqual(how, 'sha256 checked against SHA256SUMS')
        self.assertTrue((copy.parent / 'SHA256SUMS').is_file())            # it goes with the copy, onto the drive

    def test_without_sha256sums_it_is_refused(self):
        f = self.folder()
        (f / 'SHA256SUMS').unlink()
        with self.assertRaises(SIGN.SignatureError):
            SIGN.verified_release(f / 'release.json', KEYS)
        self.assertIn(('signature', 'FAIL'), states(RC.check_folder(f, KEYS, offline=True)))

    def test_a_checksum_mismatch_is_still_refused(self):
        f = self.folder()
        (f / 'release.json').write_text((f / 'release.json').read_text().replace('27.2.1', '27.2.9'))
        with self.assertRaises(SIGN.SignatureError):
            SIGN.verified_release(f / 'release.json', KEYS)

    def test_a_bad_signature_or_another_version_is_still_refused(self):
        f = self.folder()
        (f / 'release.json.sig').write_text(sshsig_files(b'\x55' * 32, b'other')[1])
        with self.assertRaises(SIGN.SignatureError):
            SIGN.verified_release(f / 'release.json', KEYS)
        with self.assertRaises(SIGN.SignatureError):
            SIGN.verified_release(self.folder('v0.0.2') / 'release.json', KEYS)

    def test_verify_release_passes_with_unsigned_said_and_fails_a_changed_file(self):
        checks = RC.check_folder(self.folder(), KEYS, offline=True)
        self.assertEqual([k['state'] for k in checks if k['name'] == 'signature'], ['SKIP', 'SKIP'])
        self.assertTrue(all('UNSIGNED release v0.0.1' in k['detail'] for k in checks if k['name'] == 'signature'))
        self.assertNotIn('FAIL', [k['state'] for k in checks])
        bad = RC.check_folder(self._tampered(), KEYS, offline=True)
        self.assertIn(('files', 'FAIL'), states(bad))

    def test_the_real_command_refuses_an_unsigned_v001(self):
        # the tree carries the release key and no v0.0.1 waiver: an unsigned v0.0.1 stops, signed or tampered
        import subprocess
        for folder in (self.folder(), self._tampered()):
            r = subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'hao'), 'verify-release', str(folder),
                                '--offline'], capture_output=True, text=True, env=dict(os.environ, HAO_LOGS=self.d.name))
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertNotIn('UNSIGNED release', r.stdout + r.stderr)

    def _tampered(self):
        f = Path(self.d.name) / 'tampered'
        f.mkdir()
        release_folder(f, asset=b'tampered!!!', sign=False)
        return f


class Command(unittest.TestCase):
    def run_cmd(self, target, opener=None, offline=True):
        hao = runpy.run_path(str(TOOL / 'hao'), run_name='hao_module')
        import console as CON
        with tempfile.TemporaryDirectory() as k:
            (Path(k) / 'keys.pub').write_text(sshsig_files(SECRET, b'')[0])
            keys, SIGN.KEYS = SIGN.KEYS, Path(k) / 'keys.pub'
            try:
                c = CON.Console('quiet', out=io.StringIO(), err=io.StringIO(), env={})
                a = argparse.Namespace(target=str(target), cache=None, offline=offline)
                return hao['cmd_verify_release'](a, c, opener=opener)
            except CON.Stopped as x:
                return x.code
            finally:
                SIGN.KEYS = keys

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cmd(release_folder(d)), 0)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cmd(release_folder(d, asset=b'tampered!!!')), 4)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cmd(release_folder(d, sign=False)), 4)

    @unittest.skipUnless((TOOL.parents[1] / '.git').exists(), '--keys is for a checkout; this is not one')
    def test_keys_from_a_file_in_a_checkout(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as k:
            (Path(k) / 'test.pub').write_text(sshsig_files(SECRET, b'')[0])
            r = __import__('subprocess').run([sys.executable, '-I', '-B', str(TOOL / 'hao'), 'verify-release',
                                              str(release_folder(d)), '--offline', '--keys', str(Path(k) / 'test.pub')],
                                             capture_output=True, text=True, env=dict(os.environ, HAO_LOGS=k))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('PASS', r.stdout + r.stderr)

    def test_without_keys_it_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            r = __import__('subprocess').run([sys.executable, '-I', '-B', str(TOOL / 'hao'), 'verify-release',
                                              str(release_folder(d)), '--offline'], capture_output=True, text=True,
                                             env=dict(os.environ, HAO_LOGS=d))
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
