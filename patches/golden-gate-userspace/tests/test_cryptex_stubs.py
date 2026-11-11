"""src/cryptex_stubs.py: which payload entries belong to a cryptex, how they are rebased, and that every one must embed.

The fixture payload carries a minimal hao-install.py with the functions cryptex_stubs uses (hao-runtime's own is not
part of this repository); the merge is a concatenation. With HAO_RUNTIME_PAYLOAD (a real hao-runtime payload built
with the cryptexes in its root) and GOLDEN_APP_CRYPTEX (a mount of the build's app.dmg), the real installer embeds
every App cryptex stub into a copy of those programs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]/'src/cryptex_stubs.py'
spec = importlib.util.spec_from_file_location('cryptex_stubs', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

FAKE_INSTALLER = '''
import os, types
class Unsafe(Exception):
    pass
def relative(name):
    if not name or name.startswith('/') or '..' in name.split('/'):
        raise Unsafe('unsafe path %r' % name)
    return name.split('/')
def inside(root, name, final_link=False):
    parts = relative(name)
    p = root
    for k, part in enumerate(parts):
        p = os.path.join(p, part)
        if os.path.islink(p) and not (final_link and k == len(parts) - 1):
            raise Unsafe('%s is a symbolic link' % name)
    return p
def manifest_names(embed, replace):
    for e in embed['files']:
        relative(e['path']); relative(e['stub'])
def check_bound(system, e, stub):
    for b in e.get('bound', []):
        if 'file' in b and open(inside(system, b['file']), 'rb').read() != b['data'].encode():
            return 'bound %s differs' % b['file']
    return None
def write_in_place(path, data):
    with open(path, 'r+b') as f:
        f.write(data); f.truncate()
hao_fat = types.SimpleNamespace(combine=lambda data, stub, drop_x86_64=False: data + stub)
'''
OS = 'System/Volumes/Preboot/Cryptexes/OS/'
APP = 'System/Volumes/Preboot/Cryptexes/App/'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, root):
        self.hao, self.mount = root/'hao', root/'mount'
        (self.hao/'embed/stubs').mkdir(parents=True)
        self.mount.mkdir()
        (self.hao/'hao-install.py').write_text(FAKE_INSTALLER)
        self.files = []

    def program(self, path, data=b'arm', stub=b'+x86', present=True, bound=None):
        name = f'stubs/{len(self.files)}'
        (self.hao/'embed'/name).write_bytes(stub)
        entry = dict(path=path, stub=name, source_sha256=sha(data), result_sha256=sha(data + stub))
        if bound is not None:
            entry['bound'] = bound
        self.files.append(entry)
        if present:
            rel = path.split('/', 5)[5]
            (self.mount/rel).parent.mkdir(parents=True, exist_ok=True)
            (self.mount/rel).write_bytes(data)
        return self.mount/path.split('/', 5)[5] if present else None

    def write(self):
        (self.hao/'embed/manifest.json').write_text(json.dumps(dict(files=self.files, required=[])))


class CryptexStubsTest(unittest.TestCase):
    def test_selects_one_cryptex_and_embeds_in_place(self):
        with tempfile.TemporaryDirectory() as temporary:
            f = Fixture(Path(temporary))
            helper = f.program(OS + 'System/Library/Frameworks/X.framework/XPCServices/H.xpc/Contents/MacOS/H')
            f.program('usr/libexec/system-program', present=False)       # the System volume's: hao-install.py's
            f.program(APP + 'usr/libexec/app-agent', present=False)       # another cryptex's
            f.files.append(dict(f.files[0]))                              # hard links are listed twice
            f.write()
            inode = helper.stat().st_ino
            self.assertEqual([e['path'] for e in module.entries(f.hao, 'OS')], [f.files[0]['path']])
            self.assertEqual(module.embed(f.hao, 'OS', f.mount), dict(embedded=1, already=0))
            self.assertEqual(helper.read_bytes(), b'arm+x86')
            self.assertEqual(helper.stat().st_ino, inode)
            # A rebuilt image whose file is already embedded counts it as such.
            self.assertEqual(module.embed(f.hao, 'OS', f.mount), dict(embedded=1, already=1))

    def test_no_entries_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as temporary:
            f = Fixture(Path(temporary))
            f.program('usr/libexec/system-program', present=False)
            f.write()
            self.assertEqual(module.entries(f.hao, 'App'), [])
            self.assertEqual(module.embed(f.hao, 'App', f.mount), dict(embedded=0, already=0))

    def test_bound_files_are_rebased_into_the_cryptex(self):
        with tempfile.TemporaryDirectory() as temporary:
            f = Fixture(Path(temporary))
            bundle = APP + 'System/Applications/S.app/Contents/'
            info = f.mount/'System/Applications/S.app/Contents/Info.plist'
            info.parent.mkdir(parents=True)
            info.write_text('plist')
            f.program(bundle + 'MacOS/S', bound=[dict(slot=-1, file=bundle + 'Info.plist', data='plist')])
            f.write()
            self.assertEqual(module.embed(f.hao, 'App', f.mount)['embedded'], 1)
            info.write_text('other')
            with self.assertRaisesRegex(RuntimeError, 'bound .*Info.plist differs'):
                module.embed(f.hao, 'App', f.mount)

    def test_bound_file_outside_the_cryptex_is_refused(self):
        entry = dict(path=APP + 'System/Applications/S.app/Contents/MacOS/S',
                     bound=[dict(slot=-1, file='System/Applications/S.app/Contents/Info.plist')])
        with self.assertRaisesRegex(ValueError, 'not in the App cryptex'):
            module.rebase(entry, 'App')

    def test_every_entry_must_embed(self):
        cases = {'missing': dict(present=False), 'source hash differs': dict(data=b'other')}
        for why, options in cases.items():
            with self.subTest(why), tempfile.TemporaryDirectory() as temporary:
                f = Fixture(Path(temporary))
                good = f.program(OS + 'usr/libexec/good')
                path = OS + 'usr/libexec/bad'
                f.program(path, present=options.get('present', True))
                if 'data' in options:
                    (f.mount/'usr/libexec/bad').write_bytes(options['data'])
                f.write()
                with self.assertRaisesRegex(RuntimeError, f'1 of 2 hao stubs not embedded: {path} \\({why}\\)'):
                    module.embed(f.hao, 'OS', f.mount)
                self.assertEqual(good.read_bytes(), b'arm+x86')

    def test_merge_result_must_match_the_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            f = Fixture(Path(temporary))
            program = f.program(OS + 'usr/libexec/p')
            f.files[0]['result_sha256'] = sha(b'something else')
            f.write()
            with self.assertRaisesRegex(RuntimeError, 'merge result differs'):
                module.embed(f.hao, 'OS', f.mount)
            self.assertEqual(program.read_bytes(), b'arm')

    @unittest.skipUnless(os.environ.get('HAO_RUNTIME_PAYLOAD') and os.environ.get('GOLDEN_APP_CRYPTEX'),
                         'HAO_RUNTIME_PAYLOAD and GOLDEN_APP_CRYPTEX not set')
    def test_real_payload_embeds_the_app_cryptex(self):
        hao, app = Path(os.environ['HAO_RUNTIME_PAYLOAD']), Path(os.environ['GOLDEN_APP_CRYPTEX'])
        selected = module.entries(hao, 'App')
        self.assertTrue(selected)
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary)
            # the programs and every file their stubs bind, copied out of the read-only cryptex
            for entry in selected:
                rebased = module.rebase(entry, 'App')
                for rel in [rebased['path']] + [b['file'] for b in rebased.get('bound', []) if 'file' in b]:
                    (mount/rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(app/rel, mount/rel)
            self.assertEqual(module.embed(hao, 'App', mount), dict(embedded=len(selected), already=0))
            for entry in selected:
                self.assertEqual(sha((mount/module.rebase(entry, 'App')['path']).read_bytes()), entry['result_sha256'])


if __name__ == '__main__':
    unittest.main()
