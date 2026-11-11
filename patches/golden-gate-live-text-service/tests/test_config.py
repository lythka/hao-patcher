"""src/config.py on authored OpenCore configs and an authored System (no Apple file)."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

p = Path(__file__).resolve().parents[1]/'src/config.py'
s = importlib.util.spec_from_file_location('text_service_config', p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
UMBRELLA = '/usr/lib/hao-live-text/LiveText.dylib'


class ConfigTests(unittest.TestCase):
    def test_only_media_removed_other_policy_unchanged_and_idempotent(self):
        source = {'NVRAM': {'Add': {m.VENDOR: {'revblock': 'gmux,media,pci', 'revpatch': 'sbvmm,f16c'},
                                    m.APPLE: {'boot-args': 'keep  revblock=pci,media,gmux tail',
                                              'csr-active-config': b'original'}},
                            'Delete': {m.VENDOR: ['other']}},
                  'Kernel': {'Add': [{'BundlePath': 'RestrictEvents.kext', 'Enabled': True}]}}
        original = copy.deepcopy(source)
        changed = m.edit(source)
        expected = copy.deepcopy(source)
        expected['NVRAM']['Add'][m.VENDOR]['revblock'] = 'gmux,pci'
        expected['NVRAM']['Add'][m.APPLE]['boot-args'] = 'keep  revblock=pci,gmux tail'
        expected['NVRAM']['Delete'][m.VENDOR].append('revblock')
        self.assertEqual(changed, expected)
        self.assertEqual(source, original)
        self.assertEqual(m.edit(changed), changed)

    def test_oclp_mbp92_config(self):
        """OCLP 3.0.0's MacBookPro9,2 config: revblock=media, revblock already in NVRAM/Delete."""
        delete = ['OCLP-Version', 'OCLP-Model', 'OCLP-Settings', 'revcpu', 'revcpuname', 'revblock', 'revpatch']
        config = {'NVRAM': {'Add': {m.VENDOR: {'revblock': 'media', 'revpatch': 'sbvmm,f16c'},
                                    m.APPLE: {'boot-args': 'keepsyms=1 -lilubetaall'}},
                            'Delete': {m.VENDOR: list(delete)}}}
        changed = m.edit(config)
        self.assertEqual(changed['NVRAM']['Add'][m.VENDOR], {'revblock': 'none', 'revpatch': 'sbvmm,f16c'})
        self.assertEqual(changed['NVRAM']['Delete'][m.VENDOR], delete)
        self.assertEqual(changed['NVRAM']['Add'][m.APPLE]['boot-args'], 'keepsyms=1 -lilubetaall')

    def test_bytes_defaults_and_ambiguous_tokens(self):
        self.assertEqual(m.without_media(b'media\0'), b'none')
        self.assertEqual(m.without_media('pci,media,media'), 'pci')
        self.assertEqual(m.without_media('none'), 'none')
        self.assertEqual(m.edit({})['NVRAM']['Add'][m.VENDOR]['revblock'], 'auto')
        with self.assertRaises(ValueError):
            m.without_media('somemedia')
        with self.assertRaises(ValueError):
            m.edit({'NVRAM': {'Add': {m.APPLE: {'boot-args': 'revblock=media revblock=pci'}}}})

    def test_requires_the_installed_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            system = Path(tmp).resolve()
            with self.assertRaisesRegex(ValueError, 'MISSING'):
                m.installed_runtime(system)
            runtime = system/m.RUNTIME
            runtime.mkdir(parents=True)
            (runtime/m.UMBRELLA).write_bytes(b'umbrella')
            (runtime/'build.json').write_text(json.dumps(dict(
                unit='tools/live-text', compiler_mode='shared', install_root='/' + m.RUNTIME, load_paths=[UMBRELLA],
                files={m.UMBRELLA: hashlib.sha256(b'umbrella').hexdigest()})))
            (system/m.STARTUP).write_bytes(b'mach-o without the load command')
            with self.assertRaisesRegex(ValueError, 'does not load'):
                m.installed_runtime(system)
            (system/m.STARTUP).write_bytes(b'header ' + UMBRELLA.encode() + b'\0 tail')
            self.assertEqual(m.installed_runtime(system), '/' + m.RUNTIME)
            (runtime/m.UMBRELLA).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'differs'):
                m.installed_runtime(system)


if __name__ == '__main__':
    unittest.main()
