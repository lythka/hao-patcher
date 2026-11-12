"""edits/config.json declares what src/config.py does to the image's OpenCore config: the engine's output is
byte-identical to src/config.py's edit() written with plistlib.dumps(sort_keys=False), as apply() writes it.
python3.14 -I -B -m unittest discover -s patches/golden-gate-live-text-service/tests

Always runs on a synthetic OCLP-shaped config. Real input, set one variable each:
  LIVE_TEXT_SERVICE_INPUT=PATH         the config.plist before this recipe (OCLP + hao-opencore, revblock=media)
  LIVE_TEXT_SERVICE_IMAGE_CONFIG=PATH  the config.plist of an image built with this recipe (revblock=none)

The spec writes revblock=none; src/config.py computes the value (other tokens kept, a revblock boot argument
filtered, revblock appended to NVRAM/Delete if missing). They agree on OCLP's MacBookPro9,2 config (revblock is
exactly "media", already in NVRAM/Delete, no revblock boot argument), which is what the tests use."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
import engine   # noqa: E402
import spec as S   # noqa: E402

SPEC = S.load(PATCH / 'edits/config.json')
_s = importlib.util.spec_from_file_location('live_text_service_config', PATCH / 'src/config.py')
config = importlib.util.module_from_spec(_s)
_s.loader.exec_module(config)


def script_output(data):
    return plistlib.dumps(config.edit(plistlib.loads(data)), sort_keys=False)


def oclp_config():
    delete = ['OCLP-Version', 'OCLP-Model', 'OCLP-Settings', 'revcpu', 'revcpuname', 'revblock', 'revpatch']
    return plistlib.dumps({
        'Kernel': {'Add': [{'BundlePath': 'RestrictEvents.kext', 'Enabled': True}]},
        'NVRAM': {'Add': {config.VENDOR: {'OCLP-Version': '3.0.0', 'revblock': 'media', 'revpatch': 'sbvmm,f16c'},
                          config.APPLE: {'boot-args': 'keepsyms=1 -lilubetaall amfi_get_out_of_my_way=1',
                                         'csr-active-config': b'\x03\x08\x00\x00'}},
                  'Delete': {config.VENDOR: delete}}})


class Declaration(unittest.TestCase):
    def test_spec_follows_the_format(self):
        self.assertEqual(S.validate(SPEC), [])
        self.assertEqual(SPEC['path'], 'EFI/OC/config.plist')
        files = json.loads((PATCH / 'patch.json').read_text())['files']
        self.assertIn({'path': SPEC['path'], 'edits': 'edits/config.json'}, files)

    def test_same_bytes_as_config_py_on_a_synthetic_oclp_config(self):
        data = oclp_config()
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, script_output(data))
        loaded = plistlib.loads(out)
        self.assertEqual(loaded['NVRAM']['Add'][config.VENDOR]['revblock'], 'none')
        self.assertEqual(record['edits'][0]['old'], 'media')

    @unittest.skipUnless(os.environ.get('LIVE_TEXT_SERVICE_INPUT'), 'set LIVE_TEXT_SERVICE_INPUT to the config.plist')
    def test_same_bytes_as_config_py_on_the_real_config(self):
        data = Path(os.environ['LIVE_TEXT_SERVICE_INPUT']).read_bytes()
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, script_output(data))
        self.assertEqual(record['expected'], 'match')

    @unittest.skipUnless(os.environ.get('LIVE_TEXT_SERVICE_INPUT') and os.environ.get('LIVE_TEXT_SERVICE_IMAGE_CONFIG'),
                         'set LIVE_TEXT_SERVICE_INPUT and LIVE_TEXT_SERVICE_IMAGE_CONFIG')
    def test_same_bytes_as_the_config_of_an_image_built_with_the_recipe(self):
        data = Path(os.environ['LIVE_TEXT_SERVICE_INPUT']).read_bytes()
        out, _ = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, Path(os.environ['LIVE_TEXT_SERVICE_IMAGE_CONFIG']).read_bytes())


if __name__ == '__main__':
    unittest.main()
