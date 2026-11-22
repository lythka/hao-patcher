"""dev-capture: apply.sh installs what patch.json declares, and the logging profile persists com.hao's Info lines.
python3.14 -I -B -m unittest discover -s patches/dev-capture/tests"""
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PATCH = Path(__file__).resolve().parents[1]
PJ = json.loads((PATCH / 'patch.json').read_text())


APPLE = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
SPEC = json.loads((PATCH / 'edits/config.json').read_text())
OPENCORE_ARGS = 'keepsyms=1 -lilubetaall ipc_control_port_options=0 amfi_get_out_of_my_way=1 amfi_force_policy=8 auxKCAtBoot=false'


def opencore_config():
    """The NVRAM keys hao-opencore leaves: its boot-args and SIP 0x803."""
    return {'NVRAM': {'Add': {APPLE: {'boot-args': OPENCORE_ARGS, 'csr-active-config': (0x803).to_bytes(4, 'little')}}}}


class Recipe(unittest.TestCase):
    def test_declared_boot_args_are_hao_opencores(self):
        opencore = json.loads((PATCH.parent / 'hao-opencore/edits/config.json').read_text())
        declared = [e['args']['value'] for e in opencore['edits'] if e.get('args', {}).get('key', [None])[-1:] == ['boot-args']]
        self.assertEqual(declared, [OPENCORE_ARGS])
        self.assertEqual(SPEC['edits'][0]['args']['value'], OPENCORE_ARGS)   # r5: no hao_crash=1

    def test_profile_persists_info_only(self):
        opts = plistlib.loads((PATCH / 'src/com.hao.plist').read_bytes())['DEFAULT-OPTIONS']
        self.assertEqual(opts, {'Level': {'Enable': 'Info', 'Persist': 'Info'}})   # 27 ignores Enable-Private-Data

    def test_dev_pack_only(self):
        self.assertEqual((PJ['pack'], PJ['status']), ('dev', 'dev'))

    def test_apply_installs_the_declared_files(self):
        text = (PATCH / 'apply.sh').read_text().replace('install -o root -g wheel ', 'install ') \
            .replace('install -d -o root -g wheel ', 'install -d ')
        with tempfile.TemporaryDirectory() as d:
            data, p = Path(d) / 'data', Path(d) / 'p'
            (p / 'inputs').mkdir(parents=True)
            (p / 'src').symlink_to(PATCH / 'src')
            for row in PJ['inputs']:
                (p / 'inputs' / row).write_bytes(row.encode())
            (p / 'inputs/hao_capture_plist').write_bytes(plistlib.dumps({'Label': 'local.hao.capture'}))
            esp = Path(d) / 'esp/EFI/OC'
            esp.mkdir(parents=True)
            (esp / 'config.plist').write_bytes(plistlib.dumps(opencore_config()))
            env = dict(os.environ, DATA=str(data), P=str(p), ESP=str(Path(d) / 'esp'), OCLP_PYTHON=sys.executable)
            subprocess.run(['/bin/sh', '-eu', '-c', text], env=env, check=True, capture_output=True)
            nvram = plistlib.loads((esp / 'config.plist').read_bytes())['NVRAM']['Add'][APPLE]
            self.assertEqual(nvram['boot-args'], SPEC['edits'][0]['args']['value'])   # script and declaration agree
            self.assertEqual(int.from_bytes(nvram['csr-active-config'], 'little'), 0x807)
            for f in (f for f in PJ['files'] if 'edits' not in f):
                row = f['origin'].get('row')
                want = (p / 'inputs' / row) if row else (PATCH / 'src' / Path(f['path']).name)
                self.assertEqual((data / f['path']).read_bytes(), want.read_bytes(), f['path'])
            d = str(data)
            self.assertTrue((Path(d) / 'private/var/db/hao-capture').is_dir())


if __name__ == '__main__':
    unittest.main()
