"""patch.json "files" declares what the kc-hook's hao-install.py (from the payload of row hao_runtime) writes.
python3.14 -I -B -m unittest discover -s patches/hao-runtime/tests

The files under usr/lib/hao that the payload ships one by one (the libraries, async-contract.json, the two lists) are
whole-file entries; the interface units (usr/lib/hao/if) and the AOT caches (usr/lib/hao/aot) are whole folders
("tree": true), because the payload ships ~2,800 files there. usr/lib/hao itself is not a tree: golden-gate-native-
identities writes its own files beside them. The embedding itself (an x86_64 launcher slice added to each ARM64-only
program, 963 entries in the 26B131 payload, of which the native Intel donors that already have an x86_64 slice are kept
native) is not written down file by file: that is hundreds of specs. The payload's
embed/manifest.json is the list, and this test is the declaration: the manifest's paths are checked against what
golden-gate-userspace declares (the 26 in the cryptexes, which it embeds into the images it builds) and against every
other recipe's declared files (no other recipe edits an embedded program).

Always: the declared entries, the hook, and no other recipe declaring a file inside the two trees.
With real inputs:
  HAO_RUNTIME_PAYLOAD  the row hao_runtime folder. Every file of its root/ is declared (as a file or inside a tree),
                       with the mode hao-install.py gives it, and nothing else is; the manifest's paths are as above
  HAO_RUNTIME_SYSTEM   a pristine 26B131 System volume (read only). hao-install.py is run on a scratch copy of the
                       files it needs: it must embed every non-cryptex entry, replace usr/lib/dyld, report the 26
                       cryptex programs as missing and nothing else wrong, and the set of files it writes (plus the
                       programs it edits in place) must be exactly the declared set"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
PATCH_JSON = json.loads((PATCH/'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}
TREES = {p for p, f in FILES.items() if f.get('tree')}
CRYPTEX = 'System/Volumes/Preboot/Cryptexes/'
USERSPACE = TOP/'patches/golden-gate-userspace'
WINDOWSERVER = 'System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer'


def at_path(value):
    return Path(value) if value else None


def declared_for(rel):
    """The declared entry that covers payload file REL (a file entry, or the tree around it), or None."""
    if rel in FILES:
        return FILES[rel]
    for tree in TREES:
        if rel.startswith(tree + '/'):
            return FILES[tree]
    return None


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Declaration(unittest.TestCase):
    def test_entries_are_whole_files_and_trees_from_the_payload_row(self):
        self.assertEqual(PATCH_JSON['inputs'], ['hao_runtime'])
        for path, entry in FILES.items():
            self.assertNotIn('edits', entry, path)
            self.assertEqual(entry['origin']['kind'], 'hao', path)
            self.assertTrue(entry['origin'].get('note'), path)
            if path != 'usr/lib/hao/install.json':
                self.assertEqual(entry['origin']['row'], 'hao_runtime', path)
        self.assertEqual(TREES, {'usr/lib/hao/if', 'usr/lib/hao/aot'})
        for tree in TREES:
            self.assertEqual(FILES[tree]['install'], {'mode': '0644', 'owner': 'root:wheel'})

    def test_the_hook_runs_the_payloads_installer_from_the_row(self):
        hook = (PATCH/'src/kc-hook.sh').read_text()
        self.assertIn('inputs/hao_runtime/hao-install.py', hook)

    def test_hao_install_writes_its_record_at_the_declared_path(self):
        self.assertIn('usr/lib/hao/install.json', FILES)
        self.assertIn('usr/lib/dyld', FILES)

    def test_no_other_recipe_declares_a_file_inside_a_tree(self):
        for patch_json in sorted((TOP/'patches').glob('*/patch.json')):
            if patch_json.parent == PATCH:
                continue
            for entry in json.loads(patch_json.read_text()).get('files', []):
                for tree in TREES:
                    self.assertFalse(entry['path'].startswith(tree + '/'), (patch_json.parent.name, entry['path']))


@unittest.skipUnless(at_path(os.environ.get('HAO_RUNTIME_PAYLOAD')), 'set HAO_RUNTIME_PAYLOAD to the hao_runtime row')
class RealPayload(unittest.TestCase):
    def setUp(self):
        self.payload = at_path(os.environ.get('HAO_RUNTIME_PAYLOAD'))

    def test_every_file_of_the_payload_root_is_declared_with_its_mode(self):
        files = sorted(str(p.relative_to(self.payload/'root')) for p in (self.payload/'root').rglob('*') if p.is_file())
        self.assertTrue(files)
        used = set()
        for rel in files:
            entry = declared_for(rel)
            self.assertIsNotNone(entry, '%s is installed but not declared' % rel)
            used.add(entry['path'])
            mode = stat.S_IMODE((self.payload/'root'/rel).stat().st_mode)
            if rel.startswith('usr/lib/hao/aot/'):
                mode = 0o644   # hao-install.py ensure_aot_dirs: root:wheel 0644 for every cache
            self.assertEqual(entry['install']['mode'], '%04o' % mode, rel)
        # every declared file entry exists in the payload; install.json and usr/lib/dyld are made or replaced instead
        for path in FILES:
            if path not in ('usr/lib/hao/install.json', 'usr/lib/dyld'):
                self.assertIn(path, used)
        self.assertEqual(sha((self.payload/'replace/dyld').read_bytes()),
                         json.loads((self.payload/'replace/manifest.json').read_text())['files'][0]['result_sha256'])
        self.assertEqual([f['path'] for f in json.loads((self.payload/'replace/manifest.json').read_text())['files']],
                         ['usr/lib/dyld'])

    def test_manifest_matches_the_other_declarations(self):
        manifest = json.loads((self.payload/'embed/manifest.json').read_text())
        paths = [e['path'] for e in manifest['files']]
        self.assertEqual(len(paths), len(set(paths)))
        build = json.loads((self.payload/'build.json').read_text())
        self.assertEqual(len(paths), build['embedded'])
        cryptex = {p for p in paths if p.startswith(CRYPTEX)}
        stubs = {}
        for spec in (USERSPACE/'edits').glob('embed-*.json'):
            data = json.loads(spec.read_text())
            stubs[data['path']] = data['edits'][0]['args']
        self.assertEqual(cryptex, set(stubs))   # golden-gate-userspace embeds exactly these, into its images
        for entry in manifest['files']:
            if entry['path'] in stubs:
                self.assertEqual(stubs[entry['path']]['stub_sha256'], entry['stub_sha256'])
                self.assertEqual(stubs[entry['path']]['result_sha256'], entry['result_sha256'])
        embedded = set(paths) - cryptex
        self.assertEqual(len(embedded), len(paths) - len(cryptex))
        for patch_json in sorted((TOP/'patches').glob('*/patch.json')):
            other = json.loads(patch_json.read_text())
            if other['stage'] == 'recovery':
                continue   # another image (the Recovery), whose System is not this one
            for entry in other.get('files', []):
                if entry['path'] == WINDOWSERVER:
                    # the manifest lists the 27.x WindowServer, but golden-gate-userspace replaced it with Tahoe's
                    # (which has an x86_64 slice), so hao-install.py reports it as kept native and edits nothing
                    self.assertEqual(patch_json.parent.name, 'golden-gate-userspace')
                    continue
                self.assertNotIn(entry['path'], embedded, '%s also declares an embedded program' % patch_json.parent.name)
        for entry in manifest['files']:   # the paths hao-install.py accepts
            self.assertFalse(entry['path'].startswith('/') or '..' in entry['path'].split('/'), entry['path'])


@unittest.skipUnless(at_path(os.environ.get('HAO_RUNTIME_PAYLOAD')) and at_path(os.environ.get('HAO_RUNTIME_SYSTEM')),
                     'set HAO_RUNTIME_PAYLOAD and HAO_RUNTIME_SYSTEM (a pristine 26B131 System volume)')
class RealInstall(unittest.TestCase):
    def test_installer_writes_exactly_the_declared_files(self):
        payload, system = at_path(os.environ.get('HAO_RUNTIME_PAYLOAD')), at_path(os.environ.get('HAO_RUNTIME_SYSTEM'))
        manifest = json.loads((payload/'embed/manifest.json').read_text())
        needed = {'usr/lib/dyld'}
        for entry in manifest['files']:
            if not entry['path'].startswith(CRYPTEX):
                needed.add(entry['path'])
                needed.update(b['file'] for b in entry.get('bound', []) if 'file' in b)
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)/'system'
            for rel in sorted(needed):
                (scratch/rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(system/rel, scratch/rel)
            (scratch/'private/var/db').mkdir(parents=True)
            before = {str(p.relative_to(scratch)) for p in scratch.rglob('*') if p.is_file()}
            run = subprocess.run([sys.executable, '-I', str(payload/'hao-install.py'), str(scratch), str(payload)],
                                 capture_output=True, text=True)
            record = json.loads((scratch/'usr/lib/hao/install.json').read_text())
            self.assertEqual(run.returncode, 0, run.stdout[-2000:])
            self.assertEqual(record['errors'], [])
            self.assertEqual(sorted(record['missing']), sorted(e['path'] for e in manifest['files'] if e['path'].startswith(CRYPTEX)))
            self.assertEqual(record['embedded'], len(manifest['files']) - len(record['missing']))
            self.assertEqual(record['replaced'], ['usr/lib/dyld'])
            after = {str(p.relative_to(scratch)) for p in scratch.rglob('*') if p.is_file()}
            written = after - before
            for rel in written:
                self.assertIsNotNone(declared_for(rel), '%s is written but not declared' % rel)
            covered = {declared_for(rel)['path'] for rel in written}
            self.assertEqual(covered | {'usr/lib/dyld'}, set(FILES))   # every declared entry was written or replaced
            for rel in (r for r in written if r in FILES):
                mode = stat.S_IMODE((scratch/rel).stat().st_mode)
                self.assertEqual(FILES[rel]['install']['mode'], '%04o' % mode, rel)
            for rel in written:   # modes of the files inside the trees
                entry = declared_for(rel)
                if entry['path'] in TREES:
                    self.assertEqual(stat.S_IMODE((scratch/rel).stat().st_mode), 0o644, rel)
            for entry in manifest['files']:
                if not entry['path'].startswith(CRYPTEX):
                    self.assertEqual(sha((scratch/entry['path']).read_bytes()), entry['result_sha256'], entry['path'])


if __name__ == '__main__':
    unittest.main()
