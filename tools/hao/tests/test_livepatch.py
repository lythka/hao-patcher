"""hao patch: write only changed files, check before and after, revert replaced and added files, lock, phases.
python3.14 -I -B -m unittest discover -s tools/hao/tests"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import livepatch as LP   # noqa: E402

T0 = datetime.datetime(2026, 11, 12, 18, 0, 0)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.root = self.d / 'root'
        self.state = self.d / 'state'
        (self.root / 'System/Library/Frameworks/Metal.framework/Versions/A').mkdir(parents=True)
        self.metal = self.root / 'System/Library/Frameworks/Metal.framework/Versions/A/Metal'
        self.metal.write_bytes(b'oclp metal')
        self.metal.chmod(0o755)
        (self.root / 'usr/lib').mkdir(parents=True)
        (self.root / 'usr/lib/libsame.dylib').write_bytes(b'same')
        (self.root / 'usr/lib/libsame.dylib').chmod(0o755)          # as the manifest says: nothing to do

    def overlay(self, name, phase, files):
        o = self.d / ('overlay-' + name)
        manifest = {'format': 1, 'pack': name, 'phase': phase, 'files': {}}
        for path, data in files.items():
            (o / 'files' / path).parent.mkdir(parents=True, exist_ok=True)
            (o / 'files' / path).write_bytes(data)
            manifest['files'][path] = {'sha256': sha(data), 'mode': '0755', 'owner': 'root:wheel'}
        o.mkdir(exist_ok=True)
        (o / 'manifest.json').write_text(json.dumps(manifest))
        return o

    def metal_pack(self):
        return LP.load_overlay(self.overlay('metal-3802', 'live-capable', {
            'System/Library/Frameworks/Metal.framework/Versions/A/Metal': b'patched metal',
            'usr/lib/libhelloblur.dylib': b'new library',
            'usr/lib/libsame.dylib': b'same'}))


class Planning(Base):
    def test_only_changed_files_are_written(self):
        p = LP.plan([self.metal_pack()], self.root)
        self.assertEqual(sorted(r['path'] for r in p['write']),
                         ['System/Library/Frameworks/Metal.framework/Versions/A/Metal', 'usr/lib/libhelloblur.dylib'])
        self.assertEqual([r['path'] for r in p['same']], ['usr/lib/libsame.dylib'])

    def test_installer_packs_need_recovery_when_live(self):
        kext = LP.load_overlay(self.overlay('intel-hd4000', 'installer', {'System/Library/Extensions/A.kext/A': b'k'}))
        p = LP.plan([self.metal_pack(), kext], self.root, 'live')
        self.assertEqual(p['needs_recovery'], ['intel-hd4000'])
        self.assertNotIn('System/Library/Extensions/A.kext/A', [r['path'] for r in p['write']])
        p = LP.plan([self.metal_pack(), kext], self.root, 'installer')
        self.assertIn('System/Library/Extensions/A.kext/A', [r['path'] for r in p['write']])

    def test_bad_overlays_refused(self):
        o = self.overlay('x', 'live-capable', {'usr/lib/a': b'a'})
        (o / 'files/usr/lib/a').write_bytes(b'tampered')
        with self.assertRaisesRegex(LP.PatchError, 'does not match its manifest'):
            LP.load_overlay(o)
        a = LP.load_overlay(self.overlay('a', 'live-capable', {'usr/lib/x': b'1'}))
        b = LP.load_overlay(self.overlay('b', 'live-capable', {'usr/lib/x': b'2'}))
        with self.assertRaisesRegex(LP.PatchError, 'usr/lib/x is in packs a and b'):
            LP.plan([a, b], self.root)


class ApplyRevert(Base):
    def test_apply_then_revert(self):
        events = []
        rec = LP.apply([self.metal_pack()], self.root, self.state, on_event=events.append, now=T0)
        self.assertEqual(rec['id'], 'hao-20261112-180000')
        self.assertEqual(self.metal.read_bytes(), b'patched metal')
        self.assertEqual((self.root / 'usr/lib/libhelloblur.dylib').read_bytes(), b'new library')
        self.assertEqual(oct(os.stat(self.metal).st_mode & 0o777), '0o755')
        self.assertEqual([e['ev'] for e in events], ['plan', 'file.write', 'file.write', 'done'])
        self.assertEqual({f['path']: f['old_sha256'] is None for f in rec['files']},
                         {'System/Library/Frameworks/Metal.framework/Versions/A/Metal': False,
                          'usr/lib/libhelloblur.dylib': True})
        again = LP.apply([self.metal_pack()], self.root, self.state, now=T0)     # same second: hao-…-2
        self.assertEqual(again['id'], 'hao-20261112-180000-2')
        self.assertEqual(again['files'], [])                         # nothing differs: nothing written
        LP.revert(self.root, self.state, patch_id=rec['id'])
        self.assertEqual(self.metal.read_bytes(), b'oclp metal')
        self.assertFalse((self.root / 'usr/lib/libhelloblur.dylib').exists())   # files new to the image go too
        self.assertEqual({r['id']: r['state'] for r in LP.records(self.state)},
                         {'hao-20261112-180000': 'reverted', 'hao-20261112-180000-2': 'applied'})

    def test_revert_refuses_files_changed_since(self):
        rec = LP.apply([self.metal_pack()], self.root, self.state, now=T0)
        self.metal.write_bytes(b'someone else')
        with self.assertRaisesRegex(LP.PatchError, 'changed after %s' % rec['id']):
            LP.revert(self.root, self.state)
        self.assertEqual(self.metal.read_bytes(), b'someone else')

    def test_lock(self):
        self.state.mkdir()
        (self.state / 'lock').write_text('pid 1\n')
        with self.assertRaisesRegex(LP.PatchError, 'another hao patch holds'):
            LP.apply([self.metal_pack()], self.root, self.state)

    def test_live_backend_is_not_proven(self):
        with self.assertRaisesRegex(LP.PatchError, 'not proven yet'):
            LP.apply([self.metal_pack()], self.root, self.state, backend=LP.MacOSLiveBackend())
        self.assertEqual(self.metal.read_bytes(), b'oclp metal')


class Attributes(Base):
    """Owner, mode, flags and folders from the manifest. Owners and system flags need root: those tests run with a
    privileged Attributes whose chown and chflags record the calls (the mode and user flags are set for real)."""

    def manifest_overlay(self, files, dirs=None):
        o = self.d / 'overlay-attrs'
        m = {'format': 1, 'pack': 'base', 'phase': 'live-capable', 'files': {}, 'dirs': dirs or {}}
        for path, (data, meta) in files.items():
            (o / 'files' / path).parent.mkdir(parents=True, exist_ok=True)
            (o / 'files' / path).write_bytes(data)
            m['files'][path] = dict(meta, sha256=sha(data))
        (o / 'manifest.json').write_text(json.dumps(m))
        return LP.load_overlay(o)

    def recorder(self):
        calls = []
        real = lambda p, bits: os.chflags(p, bits & 0xffff, follow_symlinks=False)   # user flags only, for real
        attrs = LP.Attributes(self.root, privileged=True, chown=lambda p, u, g: calls.append(('chown', p, u, g)),
                              chflags=lambda p, bits: (calls.append(('chflags', p, bits)), real(p, bits)))
        return attrs, calls

    def test_owners_by_name_from_the_root_and_by_number(self):
        (self.root / 'private/etc').mkdir(parents=True)
        (self.root / 'private/etc/passwd').write_text('# target\nroot:*:0:0::/var/root:/bin/sh\n'
                                                      '_iconservices:*:240:240::/var/empty:/usr/bin/false\n')
        (self.root / 'private/etc/group').write_text('wheel:*:0:root\nadmin:*:80:root\n')
        a = LP.Attributes(self.root)
        self.assertEqual(a.ids('_iconservices:admin'), (240, 80))
        self.assertEqual(a.ids('248:248'), (248, 248))               # tar --numeric-owner, as the manifest keeps it
        self.assertEqual(a.ids('root:wheel'), (0, 0))
        with self.assertRaisesRegex(LP.PatchError, 'unknown user'):
            a.ids('nosuchuser:wheel')

    def test_restricted_owner_mode_and_created_folders(self):
        attrs, calls = self.recorder()
        o = self.manifest_overlay(
            {'usr/lib/dyld': (b'dyld', {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']}),
             'private/var/folders/zz/x/C/store.index': (b'index', {'mode': '0600', 'owner': '248:248', 'flags': []})},
            dirs={'private/var/folders/zz/x/C': {'mode': '0700', 'owner': '248:248'}})
        rec = LP.apply([o], self.root, self.state, now=T0, attributes=attrs)
        index = self.root / 'private/var/folders/zz/x/C/store.index'
        self.assertEqual(oct(os.stat(index).st_mode & 0o7777), '0o600')
        self.assertEqual(oct(os.stat(index.parent).st_mode & 0o7777), '0o700')      # the manifest's folder mode
        self.assertEqual(oct(os.stat(index.parent.parent).st_mode & 0o7777), '0o755')
        self.assertIn(('chown', index, 248, 248), calls)
        self.assertIn(('chown', index.parent, 248, 248), calls)
        self.assertIn(('chflags', self.root / 'usr/lib/dyld', 0x80000), calls)       # SF_RESTRICTED
        self.assertEqual(rec['folders'], ['private', 'private/var', 'private/var/folders', 'private/var/folders/zz',
                                          'private/var/folders/zz/x', 'private/var/folders/zz/x/C'])
        LP.revert(self.root, self.state, attributes=attrs)
        self.assertFalse((self.root / 'private').exists())                          # folders it made go too

    def test_attributes_alone_are_set_and_reverted(self):
        os.chmod(self.metal, 0o644)
        o = self.manifest_overlay({'System/Library/Frameworks/Metal.framework/Versions/A/Metal':
                                   (b'oclp metal', {'mode': '0755', 'owner': 'root:wheel', 'flags': ['hidden']})})
        p = LP.plan([o], self.root)
        self.assertEqual((len(p['write']), [r['attrs'] for r in p['attrs']]), (0, [['mode', 'flags']]))
        events = []
        rec = LP.apply([o], self.root, self.state, on_event=events.append, now=T0)
        self.assertEqual([e['ev'] for e in events], ['plan', 'file.attrs', 'done'])
        st = os.stat(self.metal)
        self.assertEqual((oct(st.st_mode & 0o7777), st.st_flags & 0x8000), ('0o755', 0x8000))
        self.assertTrue(rec['files'][0]['attrs_only'])
        self.assertEqual(rec['files'][0]['before']['mode'], '0644')
        self.assertEqual(LP.plan([o], self.root)['attrs'], [])                     # nothing left to do
        LP.revert(self.root, self.state)
        st = os.stat(self.metal)
        self.assertEqual((oct(st.st_mode & 0o7777), st.st_flags & 0x8000, self.metal.read_bytes()),
                         ('0o644', 0, b'oclp metal'))

    def test_without_root_owners_and_system_flags_are_recorded_not_set(self):
        o = self.manifest_overlay({'usr/libexec/hao': (b'hao', {'mode': '0755', 'owner': 'root:wheel',
                                                                'flags': ['restricted']})})
        events = []
        rec = LP.apply([o], self.root, self.state, on_event=events.append, now=T0,
                       attributes=LP.Attributes(self.root, privileged=False))
        self.assertEqual(sorted(rec['files'][0]['not_set']), ['flags', 'gid', 'uid'] if os.geteuid() else ['flags'])
        self.assertEqual(events[-1]['not_set'], 1)
        self.assertEqual(os.stat(self.root / 'usr/libexec/hao').st_flags & 0x80000, 0)

    def test_unknown_flags_refused_and_names_match_the_declaration_check(self):
        with self.assertRaisesRegex(LP.PatchError, 'unknown flags'):
            self.manifest_overlay({'usr/lib/x': (b'x', {'mode': '0644', 'flags': ['sticky']})})
        patches = runpy.run_path(str(HERE.parents[2] / 'pipeline' / 'patches.py'))
        self.assertEqual(sorted(patches['INSTALL_FLAGS']), sorted(LP.FLAG_BITS))


class CommandLine(Base):
    TOOL = HERE.parent / 'hao'

    def run_hao(self, *args):
        return subprocess.run([sys.executable, '-I', str(self.TOOL), *map(str, args)], capture_output=True, text=True)

    def test_dry_run_apply_list_revert(self):
        o = self.overlay('metal-3802', 'live-capable', {'usr/lib/libhelloblur.dylib': b'new'})
        k = self.overlay('intel-hd4000', 'installer', {'System/Library/Extensions/A.kext/A': b'k'})
        r = self.run_hao('patch', o, k, '--root', self.root, '--dry-run')
        self.assertIn('needs Recovery (installer-only packs, not applied live): intel-hd4000', r.stdout)
        self.assertFalse((self.root / 'usr/lib/libhelloblur.dylib').exists())
        r = self.run_hao('patch', o, '--root', self.root, '--log', 'json')
        self.assertEqual([json.loads(l)['ev'] for l in r.stdout.splitlines()], ['plan', 'file.write', 'done'])
        r = self.run_hao('patch', '--list', '--root', self.root)
        self.assertIn('applied', r.stdout)
        r = self.run_hao('patch', '--revert', '--root', self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.root / 'usr/lib/libhelloblur.dylib').exists())
        self.assertEqual(self.run_hao('patch', '--revert', '--root', self.root).returncode, 1)   # nothing left


if __name__ == '__main__':
    unittest.main()
