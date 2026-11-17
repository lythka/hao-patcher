"""src/model.py on a fixture ESP; every element of OCLP's generated MacBookPro12,1 config has a reason and every kext a
pinned provenance (hao-opencore's, plus the target's platform_data); the pinned OCLP payload when the store has it."""
from pathlib import Path
import hashlib
import io
import json
import plistlib
import runpy
import shutil
import tarfile
import tempfile
import unittest
import zipfile

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
M = runpy.run_path(str(PATCH/'src/model.py'))
CR = runpy.run_path(str(TOP/'patches/hao-opencore/src/config_reasons.py'))
TARGET = json.loads((TOP/'pipeline/targets/mbp121.json').read_text())
ELEMENTS = [l for l in (PATCH/'src/oclp-elements.txt').read_text().splitlines() if l]
OCLP_ROW = TOP/'store/artifacts/oclp-source-fd83bda09-9d111ed9/oclp-source.tar'


def oclp_tar(path):
    with tarfile.open(path, 'w') as tar:
        z = io.BytesIO()
        with zipfile.ZipFile(z, 'w') as zf:
            zf.writestr('CryptexFixup.kext/Contents/Info.plist', b'plist')
            zf.writestr('CryptexFixup.kext/Contents/MacOS/CryptexFixup', b'fixup')
            zf.writestr('CryptexFixup.kext.dSYM/Contents/Info.plist', b'dsym')
        info = tarfile.TarInfo(M['CRYPTEXFIXUP_ZIP']); info.size = len(z.getvalue())
        tar.addfile(info, io.BytesIO(z.getvalue()))
    return path


def esp(root, model='MacBookPro12,1', lilu=True):
    (root/'EFI/OC/Kexts').mkdir(parents=True)
    add = ([{'BundlePath': 'Lilu.kext', 'Enabled': True}] if lilu else []) + [{'BundlePath': 'RestrictEvents.kext',
                                                                               'Enabled': True}]
    (root/'EFI/OC/config.plist').write_bytes(plistlib.dumps({
        'Kernel': {'Add': add}, 'NVRAM': {'Add': {'4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102': {'OCLP-Model': model}}}}))


class Model(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp)
        self.oclp = oclp_tar(self.tmp/'oclp.tar')

    def run_model(self, **kw):
        esp(self.tmp/'esp', **kw)
        return M['main'](['model.py', str(self.tmp/'esp'), str(self.oclp)])

    def test_cryptexfixup_after_lilu(self):
        self.assertEqual(self.run_model(), 0)
        config = plistlib.loads((self.tmp/'esp/EFI/OC/config.plist').read_bytes())
        self.assertEqual([k['BundlePath'] for k in config['Kernel']['Add']],
                         ['Lilu.kext', 'CryptexFixup.kext', 'RestrictEvents.kext'])
        self.assertEqual((self.tmp/'esp/EFI/OC/Kexts/CryptexFixup.kext/Contents/MacOS/CryptexFixup').read_bytes(),
                         b'fixup')
        self.assertFalse((self.tmp/'esp/EFI/OC/Kexts/CryptexFixup.kext.dSYM').exists())

    def test_refuses_another_models_efi(self):
        with self.assertRaises(SystemExit):
            self.run_model(model='MacBookPro9,2')

    def test_refuses_without_lilu(self):
        with self.assertRaises(SystemExit):
            self.run_model(lilu=False)


class Reasons(unittest.TestCase):
    """S18 for the 12,1: OCLP's elements (src/oclp-elements.txt: config_reasons.py list of the config
    pipeline/stages/build-efi.py makes for MacBookPro12,1 at the pinned OCLP) plus CryptexFixup."""

    def reasons(self):
        return dict(CR['load_reasons'](), **TARGET['platform_data']['opencore_reasons'])

    def test_every_element_has_a_reason(self):
        reasons = self.reasons()
        elements = ELEMENTS + ['kext:CryptexFixup.kext']
        self.assertEqual([e for e in elements if e not in reasons], [])
        self.assertEqual([e for e in elements if reasons[e].startswith('generic image only')], [])

    def test_ivy_and_usb_topcase_elements_are_absent(self):
        # the 12,1's facts: no f16c revpatch, no USB top case; Broadwell uses XCPM, not AppleIntelCPUPowerManagement
        for e in ELEMENTS:
            self.assertNotIn('f16c', e)
            for kext in ('AppleUSBTopCase', 'AppleUSBMultitouch', 'AppleIntelCPUPowerManagement', 'CryptexFixup'):
                self.assertNotIn(kext, e)
        for e in ('uefi-quirk:EnableVmx', 'kext:AppleHSSPISupport.kext', 'kext:AppleHSSPIHIDDriver.kext',
                  'kext:AppleTopCaseInjector.kext'):
            self.assertIn(e, ELEMENTS)                   # the 12,1's facts: enable-vmx, spi-topcase

    def test_model_reasons_name_their_source(self):
        for e, why in TARGET['platform_data']['opencore_reasons'].items():
            self.assertTrue(why.startswith(('OCLP', 'opencore-mbp121', 'RestrictEvents')), e)

    def test_every_kext_has_provenance(self):
        prov = dict(json.loads((TOP/'patches/hao-opencore/src/kext-provenance.json').read_text()),
                    **TARGET['platform_data']['kext_provenance'])
        bundles = {e[len('kext:'):].split('/Contents/')[0] for e in ELEMENTS if e.startswith('kext:')}
        self.assertEqual(sorted(b for b in bundles | {'CryptexFixup.kext'} if b not in prov), [])
        for name in ('USB-Map.kext', 'USB-Map-Tahoe.kext'):        # generated for this model, not the 9,2's
            self.assertIn('MacBookPro12,1', TARGET['platform_data']['kext_provenance'][name]['source'])


@unittest.skipUnless(OCLP_ROW.is_file(), 'needs the store (row oclp_source)')
class PinnedPayload(unittest.TestCase):
    def test_cryptexfixup_is_the_pinned_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            esp(Path(tmp)/'esp')
            M['main'](['model.py', str(Path(tmp)/'esp'), str(OCLP_ROW)])
            kext = Path(tmp)/'esp/EFI/OC/Kexts/CryptexFixup.kext'
            have = {str(p.relative_to(kext)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(kext.rglob('*')) if p.is_file()}
        pinned = json.loads((TOP/'patches/hao-opencore/src/kext-provenance.json').read_text())['CryptexFixup.kext']
        self.assertEqual(have, pinned['files'])


if __name__ == '__main__':
    unittest.main()
