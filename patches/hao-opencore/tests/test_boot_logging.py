import copy
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

APPLE = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
SCRIPT = (Path(__file__).resolve().parents[1]/'apply.sh').read_text().split("<<'PYCONFIG'\n",1)[1].split('\nPYCONFIG',1)[0]


class BootLoggingTests(unittest.TestCase):
    def apply(self, value):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'config.plist'
            path.write_bytes(plistlib.dumps(value))
            subprocess.run([sys.executable, '-c', SCRIPT, str(path)], check=True)
            return plistlib.loads(path.read_bytes())

    def fixture(self):
        return {'Kernel': {'Add': []}, 'NVRAM': {
            'Add': {APPLE: {'boot-args': 'keepsyms=1 -v startup_debug=1',
                            'bootercfg': 'log=1', 'bootercfg-once': 'log=1',
                            'csr-active-config': b'\x03\x08\x00\x00',
                            'efi-boot-device': 'netboot-fixture'},
                    'other-guid': {'untouched': 'yes'}},
            'Delete': {APPLE: ['boot-args'], 'other-guid': ['existing']}},
            'Misc': {'Debug': {'Target': 67, 'DisplayLevel': 0}}}

    def test_targeted_cleanup_preserves_unrelated_variables_and_route(self):
        original = self.fixture(); result = self.apply(copy.deepcopy(original))
        added = result['NVRAM']['Add'][APPLE]
        for name in ('bootercfg', 'bootercfg-once'):
            self.assertNotIn(name, added)
            self.assertIn(name, result['NVRAM']['Delete'][APPLE])
        self.assertEqual(added['efi-boot-device'], original['NVRAM']['Add'][APPLE]['efi-boot-device'])
        self.assertEqual(added['csr-active-config'], original['NVRAM']['Add'][APPLE]['csr-active-config'])
        self.assertEqual(result['NVRAM']['Add']['other-guid'], original['NVRAM']['Add']['other-guid'])
        self.assertEqual(result['NVRAM']['Delete']['other-guid'], ['existing'])
        self.assertIn('hao_crash=1', added['boot-args'].split())
        self.assertNotIn('-v', added['boot-args'].split())
        self.assertEqual(result['Misc']['Debug']['Target'], 65)

    def test_missing_or_consumed_overrides_still_deleted_idempotently(self):
        value = self.fixture()
        for name in ('bootercfg', 'bootercfg-once'): value['NVRAM']['Add'][APPLE].pop(name)
        result = self.apply(value)
        self.assertEqual(self.apply(result), result)
        self.assertEqual(result['NVRAM']['Delete'][APPLE], ['boot-args', 'bootercfg', 'bootercfg-once'])


if __name__ == '__main__': unittest.main()
