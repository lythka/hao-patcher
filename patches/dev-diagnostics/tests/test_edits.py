"""dev-diagnostics: apply.sh's script and edits/config.json write the same config, after hao-opencore's."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
import engine   # noqa: E402
import spec as S   # noqa: E402

SPEC = S.load(PATCH / 'edits/config.json')
OPENCORE = S.load(TOP / 'patches/hao-opencore/edits/config.json')
APPLE = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
DEV_ARGS = ['hao_nx_stats=1', 'hao_bridge_stats=1', 'hao_diag_time=1']   # dev images only


def script(name):
    text = (TOP / 'patches' / name / 'apply.sh').read_text()
    return text.split("<<'PYCONFIG'\n", 1)[1].split('\nPYCONFIG', 1)[0]


def run(name, data):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'config.plist'
        path.write_bytes(data)
        env = {k: v for k, v in os.environ.items() if k != 'IMAGE_DEV_BUILD'}
        subprocess.run([sys.executable, '-c', script(name), str(path)], check=True, env=env)
        return path.read_bytes()


def end_user_config():
    """OCLP's MacBookPro9,2 config keys after hao-opencore's plist edits (what this recipe starts from)."""
    base = plistlib.dumps({
        'Kernel': {'Add': [], 'Patch': []},
        'Misc': {'Debug': {'AppleDebug': True, 'DisplayLevel': 2147483650, 'Target': 3}},
        'NVRAM': {'Add': {APPLE: {'boot-args': 'keepsyms=1 debug=0x100 -lilubetaall ipc_control_port_options=0 '
                                               '-nokcmismatchpanic', 'csr-active-config': b'\x03\x08\x00\x00'}},
                  'Delete': {APPLE: ['boot-args', 'csr-active-config']}}})
    out, _ = engine.apply(dict(OPENCORE, edits=[e for e in OPENCORE['edits'] if e['op'] == 'plist.set']), base,
                          repo=str(TOP))
    return out


class Edits(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(S.validate(SPEC), [])
        self.assertEqual({e['op'] for e in SPEC['edits']}, {'plist.set'})
        files = json.loads((PATCH / 'patch.json').read_text())['files']
        self.assertEqual(files, [{'path': SPEC['path'], 'edits': 'edits/config.json'}])

    def test_script_and_spec_agree(self):
        data = end_user_config()
        out, _ = engine.apply(SPEC, data, repo=str(TOP))
        script_out = plistlib.loads(run('dev-diagnostics', data)); declared = plistlib.loads(out)
        # csr-active-config is a data value, which plist.set cannot declare yet: the script alone writes it.
        self.assertEqual(script_out['NVRAM']['Add'][APPLE].pop('csr-active-config'), b'\x27\x08\x00\x00')
        self.assertEqual(declared['NVRAM']['Add'][APPLE].pop('csr-active-config'), b'\x03\x08\x00\x00')
        self.assertEqual(declared, script_out)

    def test_adds_only_the_measurement_settings(self):
        before = plistlib.loads(end_user_config())
        after = plistlib.loads(run('dev-diagnostics', end_user_config()))
        args = after['NVRAM']['Add'][APPLE]['boot-args'].split()
        i = args.index(DEV_ARGS[0])
        self.assertEqual(args[i:i + 3], DEV_ARGS)
        self.assertEqual(args[i + 3], 'auxKCAtBoot=false')
        self.assertNotIn('hao_crash=1', args)   # r11: it stalls a dev image's first boot
        self.assertEqual([a for a in args if a not in DEV_ARGS], before['NVRAM']['Add'][APPLE]['boot-args'].split())
        self.assertEqual(after['Misc']['Debug']['Target'], 0x41)
        self.assertEqual(before['Misc']['Debug']['Target'], 0)       # the end-user image logs nothing
        for k in ('Target', 'DisplayLevel'):
            after['Misc']['Debug'].pop(k), before['Misc']['Debug'].pop(k)
        after['NVRAM']['Add'][APPLE].pop('boot-args'), before['NVRAM']['Add'][APPLE].pop('boot-args')
        # SIP: the end-user value plus task-for-pid and unrestricted DTrace, nothing else (never Apple internal, 0x10)
        self.assertEqual(before['NVRAM']['Add'][APPLE].pop('csr-active-config'), b'\x03\x08\x00\x00')
        self.assertEqual(after['NVRAM']['Add'][APPLE].pop('csr-active-config'), b'\x27\x08\x00\x00')
        self.assertIn('csr-active-config', after['NVRAM']['Delete'][APPLE])   # OpenCore replaces a stored value
        self.assertEqual(after, before)

    def test_idempotent(self):
        once = run('dev-diagnostics', end_user_config())
        self.assertEqual(run('dev-diagnostics', once), once)


if __name__ == '__main__':
    unittest.main()
