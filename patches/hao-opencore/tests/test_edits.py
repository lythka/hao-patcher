"""The declaration of hao-opencore: edits/config.json and the two kexts it copies into the EFI partition.
python3.14 -I -B -m unittest discover -s patches/hao-opencore/tests

edits/config.json declares the keys apply.sh's embedded script (the heredoc named PYCONFIG) sets to fixed values. It
does not declare the rest of what the recipe changes in the config (see the spec's origin note and the README): the
four kernel repairs in Kernel.Patch and the IntelCommpage and HaoExec entries of Kernel.Add. So the tests prove, not
byte equality of the whole file, but: every declared key holds the value the script writes, and the script's other
changes to the config are exactly Kernel.Add.

Always run, on a synthetic OCLP-shaped config. Real inputs, one variable each, skipped when unset:
  HAO_OPENCORE_INPUT=PATH               OCLP's base config.plist for the image (sha256 4be999f8..., made on the host
                                        by pipeline/stages/build-efi.py, as golden-run does)
  HAO_OPENCORE_IMAGE_CONFIG=PATH        the config.plist of an image built with the recipe (a dev build)
  HAO_OPENCORE_KERNEL_REPAIR_EFI=DIR    the kernel_repair_efi row
  HAO_OPENCORE_HAO_RUNTIME=DIR          the hao_runtime row
  HAO_OPENCORE_IMAGE_EFI=DIR            the payload folder of that image's EFI partition (holds EFI/OC)"""
import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct
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
FILES = {f['path']: f for f in json.loads((PATCH / 'patch.json').read_text())['files']}
SCRIPT = (PATCH / 'apply.sh').read_text().split("<<'PYCONFIG'\n", 1)[1].split('\nPYCONFIG', 1)[0]
APPLE = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
DEV_ARGS = 'hao_nx_stats=1 hao_bridge_stats=1'


def script_output(data, dev=None):
    """The config apply.sh's script writes for DATA (as plistlib.dump writes it), IMAGE_DEV_BUILD=DEV."""
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'config.plist'
        path.write_bytes(data)
        env = {k: v for k, v in os.environ.items() if k != 'IMAGE_DEV_BUILD'}
        if dev is not None:
            env['IMAGE_DEV_BUILD'] = dev
        subprocess.run([sys.executable, '-c', SCRIPT, str(path)], check=True, env=env)
        return path.read_bytes()


def oclp_config():
    """The shape of OCLP's MacBookPro9,2 config for the keys the script touches (values as in the real one)."""
    return plistlib.dumps({
        'Kernel': {'Add': [{'Arch': 'Any', 'BundlePath': 'Lilu.kext', 'Enabled': True}], 'Patch': []},
        'Misc': {'Debug': {'AppleDebug': True, 'DisplayLevel': 2147483650, 'Target': 3}},
        'NVRAM': {'Add': {APPLE: {'boot-args': 'keepsyms=1 debug=0x100 -lilubetaall ipc_control_port_options=0 '
                                               '-nokcmismatchpanic', 'csr-active-config': b'\x03\x08\x00\x00'}},
                  'Delete': {APPLE: ['boot-args', 'csr-active-config']}}})


def without_kernel_add(config):
    config = copy.deepcopy(config)
    del config['Kernel']['Add']
    return config


class Config(unittest.TestCase):
    def test_spec_follows_the_format(self):
        self.assertEqual(S.validate(SPEC), [])
        self.assertEqual(SPEC['path'], 'EFI/OC/config.plist')
        self.assertEqual(FILES['EFI/OC/config.plist'], {'path': SPEC['path'], 'edits': 'edits/config.json'})
        self.assertEqual({e['op'] for e in SPEC['edits']}, {'plist.set'})

    def check(self, data):
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        code = script_output(data)
        engine_config, code_config = plistlib.loads(out), plistlib.loads(code)
        # Every declared key holds the value the script writes.
        for e in SPEC['edits']:
            for config in (engine_config, code_config):
                node = config
                for key in e['args']['key']:
                    node = node[key]
                self.assertEqual(node, e['args']['value'], e['id'])
        # What the script changes besides: Kernel.Add, only.
        self.assertEqual(without_kernel_add(engine_config), without_kernel_add(code_config))
        self.assertEqual(code_config['Kernel']['Add'][:-1], plistlib.loads(data)['Kernel']['Add'])
        self.assertEqual(code_config['Kernel']['Add'][-1]['BundlePath'], 'HaoExec.kext')
        # With the same Kernel.Add, the bytes are the same.
        engine_config['Kernel']['Add'] = code_config['Kernel']['Add']
        self.assertEqual(plistlib.dumps(engine_config), code)
        return out, record

    def test_same_values_as_apply_sh_on_a_synthetic_oclp_config(self):
        self.check(oclp_config())

    def test_dev_builds_add_two_statistics_arguments_after_hao_crash(self):
        data = oclp_config()
        release = plistlib.loads(script_output(data))['NVRAM']['Add'][APPLE]['boot-args']
        dev = plistlib.loads(script_output(data, '1'))['NVRAM']['Add'][APPLE]['boot-args']
        self.assertEqual(release, SPEC['edits'][2]['args']['value'])
        self.assertEqual(dev, release.replace('hao_crash=1 ', 'hao_crash=1 %s ' % DEV_ARGS))

    @unittest.skipUnless(os.environ.get('HAO_OPENCORE_INPUT'), 'set HAO_OPENCORE_INPUT to the base config.plist')
    def test_same_values_as_apply_sh_on_the_real_base_config(self):
        data = Path(os.environ['HAO_OPENCORE_INPUT']).read_bytes()
        self.check(data)

    @unittest.skipUnless(os.environ.get('HAO_OPENCORE_INPUT') and os.environ.get('HAO_OPENCORE_IMAGE_CONFIG'),
                         'set HAO_OPENCORE_INPUT and HAO_OPENCORE_IMAGE_CONFIG')
    def test_the_config_of_an_image_built_with_the_recipe_has_every_declared_value(self):
        base = plistlib.loads(Path(os.environ['HAO_OPENCORE_INPUT']).read_bytes())
        image = plistlib.loads(Path(os.environ['HAO_OPENCORE_IMAGE_CONFIG']).read_bytes())
        out, _ = engine.apply(SPEC, Path(os.environ['HAO_OPENCORE_INPUT']).read_bytes(), repo=str(TOP))
        declared = plistlib.loads(out)
        # The image is a dev build: its boot-args carry the two statistics arguments, nothing else differs.
        args = declared['NVRAM']['Add'][APPLE]['boot-args']
        declared['NVRAM']['Add'][APPLE]['boot-args'] = args.replace('hao_crash=1 ', 'hao_crash=1 %s ' % DEV_ARGS)
        # Not declared: the four kernel repairs and the two Kernel.Add entries.
        patches = image['Kernel']['Patch']
        self.assertEqual(patches[:len(base['Kernel']['Patch'])], base['Kernel']['Patch'])
        self.assertEqual(len(patches) - len(base['Kernel']['Patch']), 4)
        added = image['Kernel']['Add']
        self.assertEqual(added[:len(base['Kernel']['Add'])], base['Kernel']['Add'])
        self.assertEqual([e['BundlePath'] for e in added[len(base['Kernel']['Add']):]],
                         ['IntelCommpage.kext', 'HaoExec.kext'])
        for config in (declared, image):
            config['Kernel'] = {}
        self.assertEqual(declared, image)


def tree(root):
    root = Path(root)
    return {str(p.relative_to(root.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def sections(binary, name):
    """(file offset, size) of the section called NAME in a thin 64-bit Mach-O."""
    pos = 32
    for _ in range(struct.unpack_from('<I', binary, 16)[0]):
        command, size = struct.unpack_from('<II', binary, pos)
        if command == 0x19:
            for k in range(struct.unpack_from('<I', binary, pos + 64)[0]):
                section = pos + 72 + 80 * k
                if binary[section:section + 16].rstrip(b'\0').decode() == name:
                    return struct.unpack_from('<I', binary, section + 48)[0], struct.unpack_from('<Q', binary, section + 40)[0]
        pos += size


class Kexts(unittest.TestCase):
    def test_declared_as_whole_folders_of_rows_the_recipe_has(self):
        inputs = json.loads((PATCH / 'patch.json').read_text())['inputs']
        for kext, row in (('IntelCommpage', 'kernel_repair_efi'), ('HaoExec', 'hao_runtime')):
            entry = FILES['EFI/OC/Kexts/%s.kext' % kext]
            self.assertTrue(entry['tree'])
            self.assertEqual(entry['origin']['row'], row)
            self.assertIn(row, inputs)
        script = (PATCH / 'apply.sh').read_text()
        self.assertIn('ditto "$REPAIR_WORK/artifact/EFI/OC/Kexts/IntelCommpage.kext" "$ESP/EFI/OC/Kexts/IntelCommpage.kext"', script)
        self.assertIn('ditto "$P/inputs/hao_runtime/HaoExec.kext" "$ESP/EFI/OC/Kexts/HaoExec.kext"', script)

    @unittest.skipUnless(os.environ.get('HAO_OPENCORE_KERNEL_REPAIR_EFI') and os.environ.get('HAO_OPENCORE_IMAGE_EFI'),
                         'set HAO_OPENCORE_KERNEL_REPAIR_EFI and HAO_OPENCORE_IMAGE_EFI')
    def test_intelcommpage_in_the_image_is_the_rows_folder_byte_for_byte(self):
        row = tree(Path(os.environ['HAO_OPENCORE_KERNEL_REPAIR_EFI']) / 'EFI/OC/Kexts/IntelCommpage.kext')
        image = tree(Path(os.environ['HAO_OPENCORE_IMAGE_EFI']) / 'EFI/OC/Kexts/IntelCommpage.kext')
        self.assertEqual(len(row), 3)
        self.assertEqual(row, image)

    @unittest.skipUnless(os.environ.get('HAO_OPENCORE_HAO_RUNTIME') and os.environ.get('HAO_OPENCORE_IMAGE_EFI'),
                         'set HAO_OPENCORE_HAO_RUNTIME and HAO_OPENCORE_IMAGE_EFI')
    def test_haoexec_in_the_image_is_the_rows_folder_except_the_identity_stamp(self):
        row_root = Path(os.environ['HAO_OPENCORE_HAO_RUNTIME']) / 'HaoExec.kext'
        image_root = Path(os.environ['HAO_OPENCORE_IMAGE_EFI']) / 'EFI/OC/Kexts/HaoExec.kext'
        row, image = tree(row_root), tree(image_root)
        self.assertEqual(sorted(row), sorted(image))
        executable = 'HaoExec.kext/Contents/MacOS/HaoExec'
        self.assertEqual({k for k in row if row[k] != image[k]}, {executable})
        a, b = (root / 'Contents/MacOS/HaoExec' for root in (row_root, image_root))
        a, b = a.read_bytes(), b.read_bytes()
        self.assertEqual(len(a), len(b))
        changed = [i for i in range(len(a)) if a[i] != b[i]]
        start, size = sections(a, '__hao_identity')
        self.assertTrue(changed)
        self.assertTrue(start <= changed[0] and changed[-1] < start + size)   # only inside __DATA,__hao_identity


if __name__ == '__main__':
    unittest.main()
