"""src/kc_bind.py: tools/kc-binder binds the kernel repairs to the image's BootKC and is checked against the row.
python3.14 -I -B -m unittest discover -s patches/hao-opencore/tests

Always run, on synthetic collections (tools/kc-binder/tests/fixture.py) and the binder as make.sh stages it: the three
modes (binder, binder-only, row) and every refusal. Real inputs, skipped when unset:
  HAO_OPENCORE_BOOT_KC=KC[:KC...]           Boot KCs: the darwin27_boot_kc row, a recorded run's
                                            guest-output/guest/BootKernelExtensions.kc
  HAO_OPENCORE_KERNEL_REPAIR_EFI=DIR        the kernel_repair_efi row
  HAO_OPENCORE_KERNEL_REPAIR_BINDING=DIR    the kernel_repair_binding row (profile.json)
  HAO_OPENCORE_COMMPAGE_ABI=FILE            the intel_commpage_abi row
  HAO_OPENCORE_RUNS=RUN[:RUN...]            recorded golden-run folders (store/runs/...): their BootKC, final ESP
                                            config.plist and the kernel-repair-binding.json hao-opencore kept
  HAO_OPENCORE_GUEST_PYTHON=PATH            run kc_bind.py and repair_binding.py with this interpreter (the builder
                                            VM's python3.12) instead of this one
With the four row variables, every KC goes through apply.sh's two steps exactly (repair_binding.py, then kc_bind.py
with --row-bound) and the config.plist src/repairs.py writes from kc_bind.py's artifact must be byte-identical to the one
it writes from the recorded repairs: today's images do not change."""
import contextlib
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
BINDER = TOP/'tools/kc-binder'
sys.path.insert(0, str(BINDER/'src'))
import compare   # noqa: E402
import kcbind   # noqa: E402

STAGE = runpy.run_path(str(BINDER/'src/stage.py'))
_spec = importlib.util.spec_from_file_location('kc_binder_fixture', BINDER/'tests/fixture.py')
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)
_spec = importlib.util.spec_from_file_location('hao_opencore_repairs', PATCH/'src/repairs.py')
repairs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repairs)
FOUNDATION = runpy.run_path(str(TOP/'tools/kernel-foundation/commpage_build.py'))
KEXT = 'EFI/OC/Kexts/IntelCommpage.kext'
BINARY = KEXT+'/Contents/MacOS/IntelCommpage'
PYTHON = os.environ.get('HAO_OPENCORE_GUEST_PYTHON') or sys.executable


def sha(data):
    return hashlib.sha256(data).hexdigest()


def env_paths(name):
    value = os.environ.get(name)
    return [Path(p) for p in value.split(os.pathsep) if p] if value else []


def base_config():
    """An OCLP-shaped config: Lilu in Kernel.Add, one Kernel.Patch of OCLP's own."""
    return plistlib.dumps({'Kernel': {'Add': [{'Arch': 'Any', 'BundlePath': 'Lilu.kext', 'Enabled': True}],
                                      'Patch': [{'Comment': 'OCLP patch', 'Find': b'\x01', 'Replace': b'\x02'}]},
                           'Misc': {'Debug': {'Target': 3}}})


def installed(artifact, kc, config=None):
    """The config.plist src/repairs.py writes from ARTIFACT for KC (apply.sh's repairs step)."""
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary)/'config.plist'
        path.write_bytes(config or base_config())
        with contextlib.redirect_stdout(io.StringIO()):
            repairs.apply(path, Path(artifact), Path(kc))
        return path.read_bytes()


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='hao-opencore-kc-bind-'))
        self.addCleanup(shutil.rmtree, self.temp)

    def run_kc_bind(self, stage, kc, row, profile, header, row_bound, name='out'):
        out, record = self.temp/name, self.temp/(name+'.json')
        result = subprocess.run([PYTHON, '-I', str(PATCH/'src/kc_bind.py'), '--binder', str(stage), '--boot-kc', str(kc),
                                 '--row', str(row), '--profile', str(profile), '--abi-header', str(header),
                                 '--row-bound', str(row_bound or ''), '--out', str(out), '--record', str(record)],
                                capture_output=True, text=True)
        return result, out, record


class Synthetic(Case):
    """A synthetic BootKC, a row recorded from it, the binder staged as make.sh stages it."""

    def stage(self, recipe):
        stage = self.temp/'stage'
        STAGE['stage'](stage)
        shutil.copyfile(recipe, stage/'tools/kc-binder/recipes/fixture.json')
        record = json.loads((stage/'stage.json').read_text())
        record['files']['tools/kc-binder/recipes/fixture.json'] = sha(recipe.read_bytes())
        (stage/'stage.json').write_text(json.dumps(record))
        return stage

    def recorded(self, fix, name, table_kext=True, edit=None, kernel_uuid=None):
        """A kernel_repair_efi-shaped row, its binding profile and the commpage_abi.hpp, all for FIX's layout.
        The row's IntelCommpage has the load-time table (for KERNEL_UUID if given), or with table_kext=False, the
        collection addresses compiled in (no table). EDIT changes the header text."""
        kc, recipe_path = fix.write(self.temp/('fix-'+name))
        recipe, collection = kcbind.Recipe(recipe_path, TOP), kcbind.Collection(bytes(fix.data))
        patches, capture, _ = kcbind.bind_repairs(recipe, collection)
        table = kcbind.commpage_table(recipe, collection)
        row = self.temp/('row-'+name)
        (row/KEXT/'Contents/MacOS').mkdir(parents=True)
        (row/KEXT/'Contents/Info.plist').write_bytes(b'<plist/>')
        data = kcbind.table_bytes(recipe, table, FOUNDATION)
        if kernel_uuid:
            data = data[:32]+kernel_uuid+data[48:]
        (row/BINARY).write_bytes(fixture.kext_binary(data if table_kext else None, code=name.encode()))
        (row/'EFI/OC/config.plist').write_bytes(kcbind.config_plist(patches))
        replacements = [dict(comment=p['Comment'], find=p['Find'].hex(), replace=p['Replace'].hex()) for p in patches]
        (row/'build.json').write_text(json.dumps(dict(inputs=dict(early_vm_repair=dict(capture=capture,
                                                                                       replacements=replacements)))))
        header = self.temp/('commpage_abi-'+name+'.hpp')
        header.write_text((edit or (lambda t: t))('\n'.join(kcbind.abi_lines(table, FOUNDATION).values())+'\n'))
        profile = dict(abi_header_sha256=sha(header.read_bytes()), reference_collection_sha256=collection.sha256,
                       payload_files={str(p.relative_to(row)): sha(p.read_bytes())
                                      for p in sorted(row.rglob('*')) if p.is_file()},
                       patches=[dict(comment=p['Comment'], identifier=p['Identifier'],
                                     offset=collection.data.index(p['Find']), length=len(p['Find']),
                                     sha256=sha(p['Find']), replacement_sha256=sha(p['Replace'])) for p in patches],
                       abi_bindings=[dict(address=hex(a), **b) for a, b in
                                     sorted(kcbind.table_addresses(table, collection).items())])
        profile_path = self.temp/('profile-'+name+'.json')
        profile_path.write_text(json.dumps(profile))
        return kc, recipe_path, row, profile_path, header

    def row_bound(self, row, kc, name='bound'):
        """What repair_binding.py writes when the recorded repairs bind to KC: the row, its capture naming KC."""
        bound = self.temp/name
        shutil.copytree(row, bound)
        record = json.loads((bound/'build.json').read_text())
        record['inputs']['early_vm_repair']['capture']['collection_sha256'] = sha(Path(kc).read_bytes())
        (bound/'build.json').write_text(json.dumps(record))
        return bound

    def setUp(self):
        super().setUp()
        self.fix = fixture.Fixture()
        self.kc, recipe, self.row, self.profile, self.header = self.recorded(self.fix, 'a')
        self.staged = self.stage(recipe)

    def test_binder_equal_to_the_row_installs_the_same_bytes(self):
        bound = self.row_bound(self.row, self.kc)
        result, out, record = self.run_kc_bind(self.staged, self.kc, self.row, self.profile, self.header, bound)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(record.read_text())
        self.assertEqual(record['mode'], 'binder')
        self.assertEqual(record['binder'], json.loads((self.staged/'stage.json').read_text())['files'])
        self.assertEqual((out/'EFI/OC/config.plist').read_bytes(), (self.row/'EFI/OC/config.plist').read_bytes())
        self.assertEqual(installed(out, self.kc), installed(bound, self.kc))
        self.assertEqual({p.name for p in (out/KEXT).rglob('*') if p.is_file()}, {'Info.plist', 'IntelCommpage'})
        self.assertEqual(len(plistlib.loads(installed(out, self.kc))['Kernel']['Patch']), 1+4)

    def test_refuses_a_difference_from_the_bound_row(self):
        bound = self.row_bound(self.row, self.kc)
        config = plistlib.loads((bound/'EFI/OC/config.plist').read_bytes())
        replace = config['Kernel']['Patch'][1]['Replace']
        config['Kernel']['Patch'][1]['Replace'] = replace[:-1]+bytes([replace[-1] ^ 1])
        (bound/'EFI/OC/config.plist').write_bytes(plistlib.dumps(config))
        result, out, _ = self.run_kc_bind(self.staged, self.kc, self.row, self.profile, self.header, bound)
        self.assertEqual(result.returncode, 1)
        self.assertIn('config.plist differs from the recorded repairs', result.stderr)
        self.assertFalse(out.exists())

    def test_refuses_a_header_the_profile_does_not_pin_and_a_changed_kext(self):
        bound = self.row_bound(self.row, self.kc)
        other = self.temp/'other.hpp'
        other.write_text(self.header.read_text()+'// changed\n')
        result, _, _ = self.run_kc_bind(self.staged, self.kc, self.row, self.profile, other, bound)
        self.assertIn('not the one the binding profile', result.stderr)
        (self.row/KEXT/'Contents/MacOS/IntelCommpage').write_bytes(b'other')
        result, _, _ = self.run_kc_bind(self.staged, self.kc, self.row, self.profile, self.header, bound, 'o2')
        self.assertEqual(result.returncode, 1)
        self.assertIn('IntelCommpage.kext in the row differs', result.stderr)

    def test_refuses_a_changed_staged_binder(self):
        path = self.staged/'tools/kc-binder/src/kcbind.py'
        path.write_text(path.read_text()+'\n# changed\n')
        result, _, _ = self.run_kc_bind(self.staged, self.kc, self.row, self.profile, self.header,
                                        self.row_bound(self.row, self.kc))
        self.assertEqual(result.returncode, 1)
        self.assertIn('staged kc-binder file tools/kc-binder/src/kcbind.py differs', result.stderr)

    def test_refuses_a_collection_the_binder_refuses_even_when_the_row_bound_it(self):
        fix = fixture.Fixture()
        fix.data[fixture.SEGMENTS['__TEXT'][2]+fixture.CODE['site']+0x21] = 0xb9
        kc = self.temp/'changed.kc'
        kc.write_bytes(bytes(fix.data))
        result, _, _ = self.run_kc_bind(self.staged, kc, self.row, self.profile, self.header, self.row_bound(self.row, kc))
        self.assertEqual(result.returncode, 1)
        self.assertIn('site: bytes', result.stderr)

    def test_binder_only_when_the_row_refused_and_intelcommpage_still_fits(self):
        """A relinked collection where kmutil moved only data the commpage does not use: the row (recorded for the
        old layout) refuses it, kc-binder binds it, and IntelCommpage's compiled lines still hold."""
        moved = fixture.Fixture(moves={'__KLDDATA': 0x72000})
        kc = self.temp/'moved.kc'
        kc.write_bytes(bytes(moved.data))
        result, out, record = self.run_kc_bind(self.staged, kc, self.row, self.profile, self.header, None)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(record.read_text())
        self.assertEqual(record['mode'], 'binder-only')
        self.assertEqual(record['commpage_table']['restamped'], False)
        self.assertEqual((out/BINARY).read_bytes(), (self.row/BINARY).read_bytes())
        recipe = kcbind.Recipe(self.staged/'tools/kc-binder/recipes/fixture.json', TOP)
        patches, _, _ = kcbind.bind_repairs(recipe, kcbind.Collection(bytes(moved.data)))
        self.assertEqual((out/'EFI/OC/config.plist').read_bytes(), kcbind.config_plist(patches))
        self.assertNotEqual(kcbind.config_plist(patches), (self.row/'EFI/OC/config.plist').read_bytes())
        config = plistlib.loads(installed(out, kc))
        self.assertEqual(config['Kernel']['Patch'][1:], patches)
        self.assertEqual(config['Kernel']['Add'][-1]['BundlePath'], 'IntelCommpage.kext')

    def moved(self):
        kc = self.temp/'moved.kc'
        kc.write_bytes(bytes(fixture.Fixture(moves=Synthetic_MOVES).data))
        return kc

    def test_binder_only_writes_the_table_for_a_moved_layout(self):
        """Every segment moved: IntelCommpage gets the moved collection's table in its binary, nothing else
        changes, and the installed repairs are the moved collection's."""
        kc = self.moved()
        result, out, record = self.run_kc_bind(self.staged, kc, self.row, self.profile, self.header, None)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(record.read_text())
        self.assertEqual(record['mode'], 'binder-only')
        self.assertTrue(record['commpage_table']['load_time'])
        self.assertTrue(record['commpage_table']['restamped'])
        recipe = kcbind.Recipe(self.staged/'tools/kc-binder/recipes/fixture.json', TOP)
        collection = kcbind.Collection(kc.read_bytes())
        table = kcbind.table_bytes(recipe, kcbind.commpage_table(recipe, collection), FOUNDATION)
        row, installed_binary = (self.row/BINARY).read_bytes(), (out/BINARY).read_bytes()
        offset, size = FOUNDATION['find_table'](row)
        self.assertEqual(installed_binary[offset:offset+size], table)
        self.assertNotEqual(row[offset:offset+size], table)
        self.assertEqual(installed_binary[:offset]+installed_binary[offset+size:], row[:offset]+row[offset+size:])
        self.assertEqual(record['commpage_table']['sha256'], sha(table))
        self.assertEqual((out/KEXT/'Contents/Info.plist').read_bytes(), (self.row/KEXT/'Contents/Info.plist').read_bytes())
        patches, _, _ = kcbind.bind_repairs(recipe, collection)
        self.assertEqual(plistlib.loads(installed(out, kc))['Kernel']['Patch'][1:], patches)

    def test_binder_only_refuses_a_kext_without_the_table_compiled_for_another_layout(self):
        kc, _, row, profile, header = self.recorded(fixture.Fixture(), 'legacy', table_kext=False)
        result, out, _ = self.run_kc_bind(self.staged, self.moved(), row, profile, header, None)
        self.assertEqual(result.returncode, 1)
        self.assertIn('IntelCommpage.kext was compiled for another collection layout', result.stderr)
        self.assertIn('addr_printf', result.stderr)
        self.assertFalse(out.exists())

    def test_binder_only_refuses_other_kernel_code_or_a_table_for_another_kernel(self):
        prefix = lambda t: t.replace('iosurface_prefix[] = {85,', 'iosurface_prefix[] = {86,')  # noqa: E731
        _, _, row, profile, header = self.recorded(fixture.Fixture(), 'code', edit=prefix)
        result, out, _ = self.run_kc_bind(self.staged, self.moved(), row, profile, header, None)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('compiled for another collection kernel code', result.stderr)
        self.assertIn('iosurface_prefix', result.stderr)
        self.assertNotIn('addr_printf', result.stderr)
        _, _, row, profile, header = self.recorded(fixture.Fixture(), 'uuid', kernel_uuid=bytes(range(0x30, 0x40)))
        result, out, _ = self.run_kc_bind(self.staged, self.moved(), row, profile, header, None, 'o2')
        self.assertEqual(result.returncode, 1)
        self.assertIn('IntelCommpage.kext table: IntelCommpage was compiled for kernel 303132', result.stderr)
        self.assertFalse(out.exists())

    def test_binder_refuses_a_kext_whose_table_is_not_this_collections(self):
        """The recorded repairs bound, yet the kext's own table differs from the one kc-binder computes."""
        recipe = kcbind.Recipe(self.staged/'tools/kc-binder/recipes/fixture.json', TOP)
        other = kcbind.Collection(self.moved().read_bytes())
        (self.row/BINARY).write_bytes(fixture.kext_binary(
            kcbind.table_bytes(recipe, kcbind.commpage_table(recipe, other), FOUNDATION), code=b'a'))
        profile = json.loads(self.profile.read_text())
        profile['payload_files'][BINARY] = sha((self.row/BINARY).read_bytes())
        self.profile.write_text(json.dumps(profile))
        result, out, _ = self.run_kc_bind(self.staged, self.kc, self.row, self.profile, self.header,
                                          self.row_bound(self.row, self.kc))
        self.assertEqual(result.returncode, 1)
        self.assertIn('this BootKC gives another table than the kext holds', result.stderr)
        self.assertFalse(out.exists())

    def test_row_mode_when_no_binder_recipe_names_the_kernel(self):
        other = fixture.Fixture(kernel_uuid=bytes(range(0x30, 0x40)))
        kc = self.temp/'other.kc'
        kc.write_bytes(bytes(other.data))
        bound = self.row_bound(self.row, kc)
        result, out, record = self.run_kc_bind(self.staged, kc, self.row, self.profile, self.header, bound)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(record.read_text())['mode'], 'row')
        self.assertEqual(installed(out, kc), installed(bound, kc))
        result, _, _ = self.run_kc_bind(self.staged, kc, self.row, self.profile, self.header, None, 'none')
        self.assertEqual(result.returncode, 1)
        self.assertIn('MISSING', result.stderr)
        self.assertIn('did not bind to this BootKC either', result.stderr)


Synthetic_MOVES = {'__TEXT': 0x10000, '__DATA': 0x31000, '__KLDDATA': 0x72000, '__DATA_CONST': -0x8000}


class Staging(Case):
    def test_make_sh_stages_the_binder_closure_and_the_stage_verifies(self):
        out = self.temp/'build'
        subprocess.run(['sh', str(PATCH/'make.sh'), str(out)], check=True, capture_output=True)
        record = STAGE['verify'](out)
        self.assertEqual(set(record['files']), set(STAGE['closure']()))
        for path in ('tools/kc-binder/src/kcbind.py', 'tools/kc-binder/src/compare.py',
                     'tools/kernel-foundation/commpage_build.py', 'tools/kc-binder/recipes/26B131.json'):
            self.assertEqual(record['files'][path], sha((TOP/path).read_bytes()))
        for recipe in (TOP/'tools/kc-binder/recipes').glob('*.json'):
            pins = json.loads(recipe.read_text())
            self.assertIn(pins['kc_recipe']['path'], record['files'])
            self.assertIn(pins['pv_blob']['path'], record['files'])

    def test_apply_sh_binds_with_both_and_installs_kc_binders_artifact(self):
        script = (PATCH/'apply.sh').read_text()
        row = script.index('"$P/src/repair_binding.py" bind')
        mine = script.index('"$P/src/kc_bind.py"')
        self.assertLess(row, mine)
        self.assertTrue(script[:row].rstrip().endswith('if /private/var/ssdshare/py/bin/python3.12 -I'))
        for argument in ('--binder "$P/build"', '--row "$P/inputs/kernel_repair_efi"',
                         '--profile "$P/inputs/kernel_repair_binding/profile.json"',
                         '--abi-header "$P/inputs/intel_commpage_abi"', '--row-bound "$ROW_BOUND"',
                         '--out "$REPAIR_WORK/bound"'):
            self.assertIn(argument, script[mine:])
        self.assertIn('ditto "$REPAIR_WORK/bound/EFI/OC/Kexts/IntelCommpage.kext"', script)
        self.assertNotIn('$REPAIR_WORK/artifact/EFI', script)
        inputs = json.loads((PATCH/'patch.json').read_text())['inputs']
        self.assertIn('intel_commpage_abi', inputs)


REAL = ('HAO_OPENCORE_KERNEL_REPAIR_EFI', 'HAO_OPENCORE_KERNEL_REPAIR_BINDING', 'HAO_OPENCORE_COMMPAGE_ABI')


@unittest.skipUnless(all(os.environ.get(n) for n in REAL) and (os.environ.get('HAO_OPENCORE_BOOT_KC')
                                                                or os.environ.get('HAO_OPENCORE_RUNS')),
                     'set %s and HAO_OPENCORE_BOOT_KC or HAO_OPENCORE_RUNS' % ', '.join(REAL))
class Recorded(Case):
    """apply.sh's two binding steps on recorded 26B131 collections, with the real rows."""

    def setUp(self):
        super().setUp()
        self.row = Path(os.environ['HAO_OPENCORE_KERNEL_REPAIR_EFI'])
        self.profile = Path(os.environ['HAO_OPENCORE_KERNEL_REPAIR_BINDING'])/'profile.json'
        self.header = Path(os.environ['HAO_OPENCORE_COMMPAGE_ABI'])
        self.staged = self.temp/'build'
        subprocess.run(['sh', str(PATCH/'make.sh'), str(self.staged)], check=True, capture_output=True)

    def both(self, kc, name):
        """(repair_binding.py's artifact, kc_bind.py's artifact, kc_bind.py's record) for KC, as apply.sh runs them."""
        bound = self.temp/(name+'-artifact')
        subprocess.run([PYTHON, '-I', str(PATCH/'src/repair_binding.py'), 'bind', '--profile', str(self.profile),
                        '--artifact', str(self.row), '--boot-kc', str(kc), '--output', str(bound)],
                       check=True, capture_output=True)
        result, out, record = self.run_kc_bind(self.staged, kc, self.row, self.profile, self.header, bound, name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return bound, out, json.loads(record.read_text())

    def check(self, kc, name, image_config=None):
        bound, out, record = self.both(kc, name)
        self.assertEqual(record['mode'], 'binder', kc)
        self.assertEqual(record['boot_kc_sha256'], sha(Path(kc).read_bytes()))
        self.assertEqual((out/'EFI/OC/config.plist').read_bytes(), (self.row/'EFI/OC/config.plist').read_bytes(), kc)
        self.assertEqual((out/'EFI/OC/config.plist').read_bytes(), (bound/'EFI/OC/config.plist').read_bytes(), kc)
        for p in sorted((bound/KEXT).rglob('*')):
            if p.is_file():
                self.assertEqual((out/p.relative_to(bound)).read_bytes(), p.read_bytes(), p)
        for config in (None, image_config):
            if config is not None or image_config is None:
                self.assertEqual(installed(out, kc, config), installed(bound, kc, config), kc)
        # Had repair_binding.py refused this KC, kc-binder alone binds it to the same bytes (IntelCommpage holds).
        result, alone, record = self.run_kc_bind(self.staged, kc, self.row, self.profile, self.header, None, name+'-alone')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(record.read_text())
        self.assertEqual(record['mode'], 'binder-only')
        self.assertEqual(installed(alone, kc), installed(bound, kc), kc)
        # A load-time IntelCommpage: the table kc-binder writes for this KC is the one the row's kext holds.
        self.assertFalse(record['commpage_table']['restamped'], kc)
        self.assertEqual((alone/BINARY).read_bytes(), (self.row/BINARY).read_bytes(), kc)
        return out

    def test_every_boot_kc_binds_byte_identical_to_the_row(self):
        kcs = env_paths('HAO_OPENCORE_BOOT_KC')
        if not kcs:
            self.skipTest('HAO_OPENCORE_BOOT_KC not set')
        for n, kc in enumerate(kcs):
            self.check(kc, 'kc%d' % n)

    def test_recorded_images_have_exactly_the_repairs_kc_binder_computes(self):
        """For each recorded run: its BootKC binds as above, the image's final config.plist carries exactly
        kc-binder's repairs and IntelCommpage entry, and src/repairs.py run on that config writes the same bytes
        from kc-binder's artifact as from the recorded one."""
        runs = env_paths('HAO_OPENCORE_RUNS')
        if not runs:
            self.skipTest('HAO_OPENCORE_RUNS not set')
        for n, run in enumerate(runs):
            guest = run/'guest-output/guest'
            kc, image = guest/'BootKernelExtensions.kc', guest/'boot-efi/payload/EFI/OC/config.plist'
            manifest = json.loads((guest/'boot-efi/manifest.json').read_text())
            kept = json.loads((guest/'post/hao-opencore/kernel-repair-binding.json').read_text())
            self.assertEqual(manifest['kernel_collection_sha256'], sha(kc.read_bytes()), run)
            self.assertEqual(kept['inputs']['repair_binding']['target_collection_sha256'], sha(kc.read_bytes()), run)
            out = self.check(kc, 'run%d' % n, image.read_bytes())
            mine = plistlib.loads((out/'EFI/OC/config.plist').read_bytes())
            built = plistlib.loads(image.read_bytes())
            comments = {p['Comment'] for p in mine['Kernel']['Patch']}
            self.assertEqual([p for p in built['Kernel']['Patch'] if p['Comment'] in comments],
                             mine['Kernel']['Patch'], run)
            self.assertEqual([e for e in built['Kernel']['Add'] if e['BundlePath'] == 'IntelCommpage.kext'],
                             mine['Kernel']['Add'], run)
            image_kext = guest/'boot-efi/payload'/KEXT
            if FOUNDATION['find_table']((image_kext/'Contents/MacOS/IntelCommpage').read_bytes()) is None:
                # Built before the load-time table (r41, addresses compiled in): tools/kc-binder's Recorded
                # test_table_equals_the_compiled_values checks this KC's table against those compiled lines.
                continue
            for p in sorted((out/KEXT).rglob('*')):
                if p.is_file():
                    self.assertEqual((image_kext/p.relative_to(out/KEXT)).read_bytes(), p.read_bytes(), p)


if __name__ == '__main__':
    unittest.main()
