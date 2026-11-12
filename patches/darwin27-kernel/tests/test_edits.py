"""The declaration of darwin27-kernel: the kernel file, the two kernel collections (kc.link specs) and their copies.
python3.14 -I -B -m unittest discover -s patches/darwin27-kernel/tests

kc.link is carried out by Tahoe's kmutil in the builder VM, so no byte test of the collections is possible on the host.
What the host can prove, and does: every flag and path a spec's args name is the one src/kc-hook.sh passes, and the
declared paths and install modes are the hook's. Real inputs, one variable each, skipped when unset:
  DARWIN27_KERNEL_KERNEL=PATH   the darwin27_kernel row's file
  DARWIN27_KERNEL_BOOT_KC=PATH  a BootKernelExtensions.kc the recipe built around it
They prove the BootKC's com.apple.kernel member has the row kernel's LC_UUID, and that src/members.py lists it (the
hook drops that one identifier before kmutil's second run)."""
import json
from pathlib import Path
import os
import struct
import subprocess
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
import engine   # noqa: E402
import spec as S   # noqa: E402

HOOK = (PATCH / 'src/kc-hook.sh').read_text()
FILES = {f['path']: f for f in json.loads((PATCH / 'patch.json').read_text())['files']}
BOOT = S.load(PATCH / 'edits/boot-kc.json')
SYSTEM = S.load(PATCH / 'edits/system-kc.json')
KC = 'System/Library/KernelCollections/'


def load_commands(data, base=0):
    count = struct.unpack_from('<I', data, base + 16)[0]
    pos = base + 32
    for _ in range(count):
        command, size = struct.unpack_from('<II', data, pos)
        yield command, pos, size
        pos += size


def uuid_of(data, base=0):
    return next(data[p + 8:p + 24].hex() for c, p, _ in load_commands(data, base) if c == 0x1b)


def kernel_member_uuid(kc):
    for command, pos, size in load_commands(kc):
        if command == 0x80000035:
            _, offset, name = struct.unpack_from('<QQI', kc, pos + 8)
            if kc[pos + name:pos + size].split(b'\0')[0] == b'com.apple.kernel':
                return uuid_of(kc, offset)


class Declaration(unittest.TestCase):
    def test_specs_follow_the_format(self):
        for spec, name in ((BOOT, 'boot-kc'), (SYSTEM, 'system-kc')):
            self.assertEqual(S.validate(spec), [])
            self.assertEqual([e['op'] for e in spec['edits']], ['kc.link'])
            self.assertIn({'path': spec['path'], 'edits': 'edits/%s.json' % name, 'install':
                           {'mode': '0644', 'owner': 'root:wheel'}}, FILES.values())

    def test_the_kernel_is_an_origin_row_the_recipe_has(self):
        entry = FILES['System/Library/Kernels/kernel']
        self.assertEqual(entry['origin']['row'], 'darwin27_kernel')
        self.assertIn('darwin27_kernel', json.loads((PATCH / 'patch.json').read_text())['inputs'])
        self.assertIn('inputs/darwin27_kernel', HOOK)
        self.assertIn('cmp -s "$K" "$SYS/System/Library/Kernels/kernel"', HOOK)   # the hook only checks it

    def test_the_arguments_are_what_the_hook_passes_kmutil(self):
        build = BOOT['edits'][0]['args']
        second = SYSTEM['edits'][0]['args']
        shared = set(build) - {'name', 'boot_collection'}
        self.assertEqual({k: build[k] for k in shared}, {k: second[k] for k in shared})   # one "$@" for both runs
        self.assertEqual((build['name'], second['name']), ('boot', 'sys'))
        self.assertIn('B=%s ' % build['build_directory'], HOOK)
        self.assertIn('-a %s' % build['arch'], HOOK)
        self.assertIn('--allow-missing-kdk', HOOK)
        self.assertIn('-R "$SYS"', HOOK)
        self.assertIn('-k "$SYS/%s"' % build['kernel'], HOOK)
        self.assertIn('-V %s' % build['variant'], HOOK)
        self.assertIn('-B "$B/BootKernelExtensions.kc"', HOOK)
        self.assertIn('"$W/kmutil" create -n boot "$@"', HOOK)
        self.assertIn('"$W/kmutil" create -n sys "$@" -S "$B/SystemKernelExtensions.kc"', HOOK)
        self.assertIn("--elide-identifier", HOOK)
        self.assertIn('cp "$H/inputs/%s" "$W/kmutil"' % build['tool_row'], HOOK)
        self.assertIn('codesign -s - -f "$W/kmutil"', HOOK)
        self.assertEqual(build['tool'], 'kmutil')
        self.assertIn(build['tool_row'], json.loads((PATCH / 'patch.json').read_text())['inputs'])

    def test_the_paths_and_modes_are_the_hooks(self):
        self.assertIn('KC=%s' % KC.rstrip('/'), HOOK)
        for name in ('BootKernelExtensions.kc', 'BootKernelExtensions.kc.elides', 'SystemKernelExtensions.kc'):
            self.assertIn(KC + name, FILES)
            self.assertIn(name, HOOK)
            self.assertEqual(FILES[KC + name]['install'], {'mode': '0644', 'owner': 'root:wheel'})
        self.assertIn('install -o root -g wheel -m 644', HOOK)
        for name in ('BootKernelExtensions.kc', 'BootKernelExtensions.kc.elides'):   # Preboot gets these two only
            self.assertIn('Preboot/GROUP/boot/' + KC + name, FILES)
        self.assertNotIn('Preboot/GROUP/boot/' + KC + 'SystemKernelExtensions.kc', FILES)
        self.assertIn('PREBOOT=$(info "$PBD" MountPoint)/$GROUP/boot/$KC', HOOK)
        self.assertIn('for f in BootKernelExtensions.kc BootKernelExtensions.kc.elides; do', HOOK)
        self.assertEqual(sum(1 for p in FILES if p.startswith('Preboot/')), 2)

    def test_kc_link_is_declared_only_the_engine_refuses_to_apply_it(self):
        with self.assertRaises(engine.EditError):
            engine.apply(BOOT, b'', repo=str(TOP))
        self.assertIn('kc.link', '\n'.join(S.describe(BOOT)))

    def test_member_list_drops_only_the_kernel(self):
        self.assertIn('grep -vx com.apple.kernel', HOOK)
        self.assertIn('members.py', HOOK)

    @unittest.skipUnless(os.environ.get('DARWIN27_KERNEL_KERNEL') and os.environ.get('DARWIN27_KERNEL_BOOT_KC'),
                         'set DARWIN27_KERNEL_KERNEL and DARWIN27_KERNEL_BOOT_KC')
    def test_the_real_bootkc_links_the_row_kernel(self):
        kernel = Path(os.environ['DARWIN27_KERNEL_KERNEL']).read_bytes()
        kc_path = os.environ['DARWIN27_KERNEL_BOOT_KC']
        self.assertEqual(kernel_member_uuid(Path(kc_path).read_bytes()), uuid_of(kernel))
        out = subprocess.run([sys.executable, '-I', str(PATCH / 'src/members.py'), kc_path], check=True,
                             capture_output=True, text=True).stdout.split()
        self.assertIn('com.apple.kernel', out)
        self.assertEqual(out, sorted(out))
        self.assertGreater(len(out), 100)


if __name__ == '__main__':
    unittest.main()
