"""edits/*.json declare what src/add_load_dylib.py does to the two daemons: the engine's output is byte-identical
to the script's. python3.14 -I -B -m unittest discover -s patches/golden-gate-corespotlight-compat/tests

Always runs on a synthetic universal file. With CORESPOTLIGHT_CORESPOTLIGHTD / CORESPOTLIGHT_MANAGEDCORESPOTLIGHTD
(Golden Gate's own daemons, 26A428 or 26B131) it also runs on the real file, checks the spec's recorded output hash
when the input is the recorded one, and that the file's signature identifier is the one the spec re-signs with."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
sys.path.insert(0, str(TOP / 'tools/edit-engine/tests'))
import engine   # noqa: E402
import spec as S   # noqa: E402
import fixture   # noqa: E402

SCRIPT = runpy.run_path(str(PATCH / 'src/add_load_dylib.py'))
DYLIB = '/usr/lib/corespotlight_compat.dylib'
SUPPORT = ('System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/'
           'Versions/A/Support/')
NAMES = {'corespotlightd': 'CORESPOTLIGHT_CORESPOTLIGHTD', 'managedcorespotlightd': 'CORESPOTLIGHT_MANAGEDCORESPOTLIGHTD'}


def load(name):
    return S.load(PATCH / 'edits' / (name + '.json'))


class Declaration(unittest.TestCase):
    def test_specs_follow_the_format(self):
        files = json.loads((PATCH / 'patch.json').read_text())['files']
        for name in NAMES:
            spec = load(name)
            self.assertEqual(S.validate(spec), [])
            self.assertEqual(spec['path'], SUPPORT + name)
            self.assertIn({'path': spec['path'], 'edits': 'edits/%s.json' % name}, files)
            edit, = spec['edits']
            self.assertEqual((edit['op'], edit['args']), ('macho.add-dylib', {
                'path': DYLIB, 'weak': False, 'timestamp': 2, 'current_version': 0x10000, 'compat_version': 0x10000}))
            # as kc-hook.sh: -i <the daemon's own identifier> --preserve-metadata=entitlements,flags,runtime
            self.assertEqual(spec['sign'], {'mode': 'adhoc', 'preserve': ['identifier', 'entitlements', 'flags', 'runtime']})
        self.assertIn({'path': 'usr/lib/corespotlight_compat.dylib', 'origin': {
            'kind': 'hao', 'row': 'corespotlight_compat_shim', 'note': files[2]['origin']['note']}}, files)

    def test_same_bytes_as_add_load_dylib_on_a_synthetic_universal_file(self):
        thin = fixture.dylib(ident=b'/usr/libexec/corespotlightd')
        data, x86_at = fixture.universal(thin)
        for name in NAMES:
            out, record = engine.apply(load(name), data, repo=str(TOP))
            self.assertEqual(out, SCRIPT['add'](data, DYLIB))
            changed = [i for i in range(len(data)) if data[i] != out[i]]
            self.assertTrue(changed and all(x86_at <= i < x86_at + 0x1000 for i in changed))
            end = x86_at + 32 + int.from_bytes(data[x86_at + 20:x86_at + 24], 'little')
            self.assertEqual(out[end:end + 24], (0xc).to_bytes(4, 'little') + (64).to_bytes(4, 'little') +
                             (24).to_bytes(4, 'little') + (2).to_bytes(4, 'little') + (0x10000).to_bytes(4, 'little') * 2)


class Real(unittest.TestCase):
    def check(self, name):
        var = NAMES[name]
        if not os.environ.get(var):
            self.skipTest('set %s to Golden Gate\'s %s' % (var, name))
        path = Path(os.environ[var])
        data, spec = path.read_bytes(), load(name)
        out, record = engine.apply(spec, data, repo=str(TOP))
        self.assertEqual(out, SCRIPT['add'](data, DYLIB))
        import hashlib
        if hashlib.sha256(data).hexdigest() in spec['expect']:
            self.assertEqual(record['expected'], 'match')
        ident = subprocess.run(['codesign', '-dv', str(path)], capture_output=True, text=True).stderr
        self.assertIn('Identifier=com.apple.%s\n' % name, ident)        # the identifier the spec keeps

    def test_same_bytes_on_corespotlightd(self):
        self.check('corespotlightd')

    def test_same_bytes_on_managedcorespotlightd(self):
        self.check('managedcorespotlightd')


if __name__ == '__main__':
    unittest.main()
