"""Signatures: "entitlements_from" in the sign format, signing.read/outside/compare, and the sign lint.
python3.14 -I -B -m unittest discover -s tools/edit-engine/tests -k signing

The signing tests run macOS codesign on copies of this Mac's /usr/bin/true (never executed) in a temporary folder."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import engine   # noqa: E402
import signing   # noqa: E402
import signlint   # noqa: E402
import spec as S   # noqa: E402

CODESIGN = sys.platform == 'darwin' and shutil.which('codesign') and Path('/usr/bin/true').is_file()
ENTS = {'com.apple.private.example': True, 'com.apple.security.cs.allow-jit': True}


def spec(sign):
    return {'format': 1, 'path': 'usr/bin/x', 'origin': {'kind': 'tahoe'}, 'sign': sign,
            'edits': [{'id': 'e', 'kind': 'literal', 'at': 0, 'find': '01', 'replace': '00', 'reason': 'x'}]}


class Format(unittest.TestCase):
    def test_entitlements_from_forms(self):
        ws = 'System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer'
        good = [{'mode': 'adhoc', 'identifier': 'com.apple.WindowServer',
                 'entitlements_from': {'kind': 'apple', 'path': ws}},
                {'mode': 'adhoc', 'entitlements_from': {'kind': 'hao', 'row': 'r', 'source': 'a/b', 'note': 'n'}}]
        for g in good:
            self.assertEqual(S.validate(spec(g)), [], g)
        bad = [{'kind': 'apple', 'row': 'r'}, {'kind': 'hao', 'path': 'x'}, {'kind': 'apple', 'path': '/abs'},
               {'kind': 'nope', 'path': 'x'}, 'apple:' + ws]
        for b in bad:
            self.assertIn('entitlements_from', ' '.join(S.validate(spec({'mode': 'adhoc', 'entitlements_from': b}))), b)
        both = {'mode': 'adhoc', 'preserve': ['entitlements'], 'entitlements_from': {'kind': 'apple', 'path': ws}}
        self.assertIn('exclude each other', ' '.join(S.validate(spec(both))))
        self.assertEqual(S.codesign_args(good[0], None, '/tmp/e.plist'),
                         ['codesign', '-f', '-s', '-', '-i', 'com.apple.WindowServer', '--entitlements', '/tmp/e.plist'])
        with self.assertRaisesRegex(ValueError, 'entitlements_from'):
            S.codesign_args(good[0])
        self.assertIn("with the entitlements of Apple's System/Library", S.sign_text(good[0]))

    def test_hao_stamp_is_external_and_names_its_input(self):
        s = spec(None)
        s['edits'] = [{'id': 'stamp', 'kind': 'op', 'op': 'hao.stamp', 'reason': 'x',
                       'args': {'kernel_collection': 'System/Library/KernelCollections/BootKernelExtensions.kc'}}]
        self.assertEqual(S.validate(s), [])
        self.assertIn('hao.stamp', S.EXTERNAL_OPS)
        self.assertEqual(S.EXTERNAL_INPUTS['hao.stamp'], ('kernel_collection',))

    def test_kc_bind_is_external_names_its_input_and_sits_with_plist_set(self):
        """hao-opencore's config.plist: kc.bind (tools/kc-binder writes the kernel repairs) and plist.set keys in
        one spec. An external op is no byte edit of the engine's, so the mix is allowed; apply refuses the op."""
        s = spec(None)
        s['path'] = 'EFI/OC/config.plist'
        s['edits'] = [{'id': 'bind', 'kind': 'op', 'op': 'kc.bind', 'reason': 'x',
                       'args': {'kernel_collection': 'System/Library/KernelCollections/BootKernelExtensions.kc'}},
                      {'id': 'key', 'kind': 'op', 'op': 'plist.set', 'args': {'key': ['a'], 'value': 1}, 'reason': 'x'}]
        self.assertEqual(S.validate(s), [])
        self.assertEqual(S.EXTERNAL_INPUTS['kc.bind'], ('kernel_collection',))
        self.assertIn('kc.bind (applied by tools/kc-binder', S.edit_summary(s['edits'][0]))
        with self.assertRaisesRegex(engine.EditError, 'applied by tools/kc-binder'):
            engine.apply(s, b'<?xml version="1.0"?><plist version="1.0"><dict/></plist>')


@unittest.skipUnless(CODESIGN, 'needs macOS codesign and /usr/bin/true')
class Signatures(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ents = self.d / 'ents.plist'
        self.ents.write_bytes(plistlib.dumps(ENTS))

    def signed(self, name, *args):
        p = self.d / name
        shutil.copyfile('/usr/bin/true', p)
        subprocess.run(['codesign', '-f', '-s', '-', *args, str(p)], check=True, capture_output=True)
        return p.read_bytes()

    def test_read_finds_identifier_and_entitlements(self):
        sig = signing.read(self.signed('a', '-i', 'local.a', '--entitlements', str(self.ents)))
        x = sig['x86_64']
        self.assertEqual(x['identifier'], 'local.a')
        self.assertEqual(plistlib.loads(x['entitlements'][8:]), ENTS)
        self.assertIsNotNone(x['der_entitlements'])
        self.assertEqual(len(x['cdhash']), 40)

    def test_compare_entitlements_identifier_and_padding(self):
        ref = self.signed('ref', '-i', 'local.a', '--entitlements', str(self.ents))
        none = self.signed('none', '-i', 'local.a')
        other_id = self.signed('id', '-i', 'local.b', '--entitlements', str(self.ents))
        same = self.signed('same', '-i', 'local.a', '--entitlements', str(self.ents))
        self.assertEqual(signing.compare(ref, same)['problems'], [])
        self.assertTrue(any('entitlements differ' in p for p in signing.compare(ref, none)['problems']))
        self.assertTrue(any("identifier 'local.b'" in p for p in signing.compare(ref, other_id)['problems']))
        padded = pad(ref, 18000)                       # what the builder VM's codesign leaves: zeros after the blobs
        result = signing.compare(padded, ref)
        self.assertEqual(result['problems'], [])
        self.assertEqual(signing.read(padded)['x86_64']['padding'] - signing.read(ref)['x86_64']['padding'], 18000)

    def test_code_outside_the_signature_must_match(self):
        ref = bytearray(self.signed('ref', '-i', 'local.a'))
        start = next(s for c, s, n, _ in signing.slices(bytes(ref)) if c == 0x1000007)
        ref2 = bytearray(ref)
        ref2[start + 0x1000] ^= 0xff
        self.assertIn('code differs', signing.compare(bytes(ref), bytes(ref2))['problems'][0])

    def test_sign_takes_another_files_entitlements(self):
        source = self.signed('source', '-i', 'local.src', '--entitlements', str(self.ents))
        target = self.signed('target', '-i', 'local.t')
        out = signing.sign(target, {'mode': 'adhoc', 'identifier': 'local.t',
                                    'entitlements_from': {'kind': 'tahoe', 'path': 'x'}}, target, source,
                           S.codesign_args)
        got = signing.read(out)['x86_64']
        self.assertEqual((got['identifier'], plistlib.loads(got['entitlements'][8:])), ('local.t', ENTS))
        with self.assertRaisesRegex(signing.SignatureError, 'has no entitlements'):
            signing.sign(target, {'mode': 'adhoc', 'entitlements_from': {'kind': 'tahoe', 'path': 'x'}}, target,
                         target, S.codesign_args)

    def test_verify_declaration_compares_references(self):
        """verify-declaration --reference: what the declarations make against what the hook installed. The
        WindowServer gap (same code, signature without the entitlements) is refused; padding alone is a note."""
        import runpy
        vd = runpy.run_path(str(HERE.parent / 'verify-declaration'))
        made = self.d / 'made'
        ws = 'System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer'
        (made / 'base/files' / ws).parent.mkdir(parents=True)
        (made / 'base/files' / ws).write_bytes(self.signed('declared', '-i', 'com.apple.WindowServer'))
        (made / 'report.json').write_text(json.dumps([
            {'path': ws, 'outcome': 'made', 'pack': 'base'},
            {'path': 'EFI/x', 'outcome': 'deferred', 'reason': 'made on the target', 'pack': 'base'}]))
        hook = self.d / 'hook'
        hook.write_bytes(self.signed('hook', '-i', 'com.apple.WindowServer', '--entitlements', str(self.ents)))
        problems, notes = vd['reference_problems'](made, {ws: str(hook), 'EFI/x': str(hook)})
        self.assertTrue(any('entitlements differ' in p for p in problems), problems)
        self.assertTrue(any('EFI/x: not made from the declarations (deferred' in p for p in problems), problems)
        plain = self.d / 'plain'
        plain.write_bytes(pad((made / 'base/files' / ws).read_bytes(), 18000))
        problems, notes = vd['reference_problems'](made, {ws: str(plain)})
        self.assertEqual(problems, [])
        self.assertTrue(notes and 'signature only' in notes[0], notes)

    def test_command_line_sign_needs_the_entitlements_file(self):
        target = self.d / 'in'
        target.write_bytes(self.signed('t', '-i', 'local.t'))
        source = self.d / 'src'
        source.write_bytes(self.signed('s', '-i', 'local.s', '--entitlements', str(self.ents)))
        offset = signing.slices(target.read_bytes())[0][1]           # a byte edit in the first slice's header
        byte = target.read_bytes()[offset + 8]
        sp = self.d / 'spec.json'
        sp.write_text(json.dumps({'format': 1, 'path': 'usr/bin/true', 'origin': {'kind': 'apple'},
                                  'edits': [{'id': 'b', 'kind': 'literal', 'at': offset + 8, 'find': '%02X' % byte,
                                             'replace': '%02X' % byte, 'mask': '00', 'reason': 'x'}],
                                  'sign': {'mode': 'adhoc', 'identifier': 'local.t',
                                           'entitlements_from': {'kind': 'apple', 'path': 'usr/bin/true'}}}))
        tool = HERE.parent / 'edit-engine'
        run = lambda *a: subprocess.run([sys.executable, '-I', str(tool), 'apply', '--sign', *map(str, a)],
                                        capture_output=True, text=True)
        r = run(sp, target, self.d / 'out')
        self.assertEqual(r.returncode, 1)
        self.assertIn('--entitlements-from', r.stderr)
        self.assertFalse((self.d / 'out').exists())
        r = run(sp, target, self.d / 'out', '--entitlements-from', source)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = signing.read((self.d / 'out').read_bytes())['x86_64']
        self.assertEqual((got['identifier'], plistlib.loads(got['entitlements'][8:])), ('local.t', ENTS))


def pad(data, extra):
    """DATA with EXTRA zero bytes after each slice's signature blobs (thin x86_64 only in the test's slice), the
    LC_CODE_SIGNATURE datasize and __LINKEDIT sizes grown to match: the layout the builder VM's codesign writes."""
    out = bytearray(data)
    for cpu, start, length, _ in reversed(signing.slices(data)):
        if cpu != 0x1000007:
            continue
        for cmd, at, size in signing._commands(bytes(out), start):
            if cmd == signing.LC_CODE_SIGNATURE:
                dataoff, datasize = struct.unpack_from('<II', out, at + 8)
                struct.pack_into('<I', out, at + 12, datasize + extra)
            if cmd == signing.LC_SEGMENT_64 and out[at + 8:at + 24].rstrip(b'\0') == b'__LINKEDIT':
                vmsize, = struct.unpack_from('<Q', out, at + 32)
                filesize, = struct.unpack_from('<Q', out, at + 48)
                struct.pack_into('<Q', out, at + 32, vmsize + extra)
                struct.pack_into('<Q', out, at + 48, filesize + extra)
        end = start + length
        out[end:end] = bytes(extra)
        if data[:4] in (b'\xca\xfe\xba\xbe',):
            count = struct.unpack_from('>I', out, 4)[0]
            for i in range(count):
                row = 8 + 20 * i
                c, _, off, ln = struct.unpack_from('>IIII', out, row)
                if off == start:
                    struct.pack_into('>I', out, row + 12, ln + extra)
                elif off > start:
                    struct.pack_into('>I', out, row + 8, off + extra)
    return bytes(out)


class Lint(unittest.TestCase):
    """signlint: a hook that signs with another file's entitlements (or keeps the input's metadata) must have a spec
    that says so."""

    def recipe(self, hook, specs):
        d = Path(tempfile.mkdtemp())
        (d / 'src').mkdir()
        (d / 'src' / 'kc-hook.sh').write_text(hook)
        (d / 'edits').mkdir()
        files = []
        for name, sign in specs.items():
            (d / 'edits' / name).write_text(json.dumps(spec(sign)))
            files.append({'path': 'usr/bin/x', 'edits': 'edits/' + name})
        (d / 'patch.json').write_text(json.dumps({'files': files}))
        return d

    def test_entitlements_from_a_file_needs_a_spec_that_takes_them(self):
        hook = 'codesign -f -s - -i "$ID" --entitlements "$W/e.plist" "$WS"\n'
        bad = self.recipe(hook, {'a.json': {'mode': 'adhoc', 'identifier': 'com.apple.WindowServer'}})
        self.assertIn('--entitlements', ' '.join(signlint.problems(bad)))
        good = self.recipe(hook, {'a.json': {'mode': 'adhoc', 'identifier': 'com.apple.WindowServer',
                                             'entitlements_from': {'kind': 'apple', 'path': 'x'}}})
        self.assertEqual(signlint.problems(good), [])

    def test_preserved_metadata_must_be_declared(self):
        hook = "run('codesign', '-f', '-s', '-', '-i', ident, '--preserve-metadata=entitlements,flags,runtime', f)\n"
        bad = self.recipe(hook, {'a.json': {'mode': 'adhoc', 'preserve': ['identifier']}})
        self.assertIn('entitlements, flags, runtime', ' '.join(signlint.problems(bad)))
        good = self.recipe(hook, {'a.json': {'mode': 'adhoc', 'preserve': ['identifier', 'entitlements', 'flags',
                                                                           'runtime']}})
        self.assertEqual(signlint.problems(good), [])

    def test_every_recipe_in_the_repository(self):
        root = HERE.parents[2] / 'patches'
        bad = [p for d in sorted(root.iterdir()) if (d / 'patch.json').is_file() for p in signlint.problems(d)]
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
