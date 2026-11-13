"""Kernel binder: synthetic collections (always), the checked-in recipe and blob (always), today's producers as
oracles (clang), and the recorded 26B131 artifacts (environment variables).
python3.14 -I -B -m unittest discover -s tools/kc-binder/tests

Real inputs, each optional:
  KC_BINDER_BOOT_KC         one or more Boot KCs (separated by ':')
  KC_BINDER_REPAIR_EFI      the kernel_repair_efi artifact folder (EFI/OC/config.plist, build.json)
  KC_BINDER_REPAIR_BINDING  the kernel_repair_binding artifact folder (profile.json)
  KC_BINDER_COMMPAGE_ABI    the commpage_abi.hpp IntelCommpage was compiled with
  KC_BINDER_KERNEL, KC_BINDER_IOSURFACE_LAYOUT   make-recipe inputs (with the KC the layout names)
"""
import json
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
REPO = UNIT.parents[1]
sys.path.insert(0, str(UNIT/'src'))
sys.path.insert(0, str(HERE))
import compare   # noqa: E402
import fixture   # noqa: E402
import kcbind   # noqa: E402

FOUNDATION = REPO/'tools/kernel-foundation'
CLI = UNIT/'kc-binder'


def foundation():
    return runpy.run_path(str(FOUNDATION/'commpage_build.py'))


def have_tool(name):
    if shutil.which('xcrun') is None:
        return False
    return subprocess.run(['xcrun', '--find', name], capture_output=True).returncode == 0


def env_paths(name):
    value = os.environ.get(name)
    return [Path(p) for p in value.split(os.pathsep) if p] if value else []


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='kc-binder-test-'))
        self.addCleanup(shutil.rmtree, self.temp)

    def bound(self, fix=None, recipe_from=None, name='a'):
        """(recipe, collection) for fixture `fix`, with the binder recipe made from `recipe_from`'s layout."""
        fix = fix or fixture.Fixture()
        kc, recipe = (recipe_from or fix).write(self.temp/name)
        (self.temp/name/'BootKernelExtensions.kc').write_bytes(bytes(fix.data))
        return kcbind.Recipe(recipe, REPO), kcbind.Collection(bytes(fix.data))

    def refuses(self, fix, text, recipe_from=None):
        recipe, kc = self.bound(fix, recipe_from or fixture.Fixture())
        with self.assertRaises(kcbind.BindError) as caught:
            kcbind.bind_repairs(recipe, kc)
            kcbind.commpage_table(recipe, kc)
        self.assertIn(text, str(caught.exception))


class Synthetic(Case):
    MOVES = {'__TEXT': 0x10000, '__DATA': 0x31000, '__KLDDATA': 0x72000, '__DATA_CONST': -0x8000}

    def test_binds_points_through_member_segments(self):
        recipe, kc = self.bound()
        patches, capture, report = kcbind.bind_repairs(recipe, kc)
        self.assertEqual([p['Comment'] for p in patches], list(kcbind.COMMENTS.values()))
        self.assertEqual(report['site']['address'], hex(fixture.BASE+0x100000+0x1000))
        self.assertEqual(capture['addresses']['map_stolen_data'], hex(fixture.BASE+0x600000+0x80))
        self.assertEqual(capture['darwin_version'], '27.2.0')
        for p in patches:
            self.assertEqual(kc.data.count(p['Find']), 1)
            self.assertEqual(len(p['Find']), len(p['Replace']))
            self.assertEqual((p['MinKernel'], p['MaxKernel'], p['Identifier']), ('27.2.0', '27.2.0', 'kernel'))

    def test_moved_segments_bind_with_the_same_recipe(self):
        a = fixture.Fixture()
        b = fixture.Fixture(moves=self.MOVES)
        pa, ca, _ = kcbind.bind_repairs(*self.bound(a, a, 'a'))
        pb, cb, _ = kcbind.bind_repairs(*self.bound(b, a, 'b'))
        self.assertNotEqual(pa[0]['Find'], pb[0]['Find'])        # relocated fields differ
        self.assertNotEqual(pa[2]['Replace'], pb[2]['Replace'])
        self.assertEqual(int(cb['addresses']['map_stolen_data'], 0)-int(ca['addresses']['map_stolen_data'], 0),
                         self.MOVES['__KLDDATA'])
        self.assertEqual(int(cb['pv_aliases']['cave']['address'], 0)-int(ca['pv_aliases']['cave']['address'], 0),
                         self.MOVES['__TEXT'])

    def test_site_and_range_call_equal_early_vm_producer(self):
        """tools/kernel-foundation/early_vm.py (today's producer) on the binder's own capture."""
        generate = runpy.run_path(str(FOUNDATION/'early_vm.py'))['generate']
        for name, fix in (('a', fixture.Fixture()), ('b', fixture.Fixture(moves=self.MOVES))):
            recipe, kc = self.bound(fix, fixture.Fixture(), name)
            patches, capture, _ = kcbind.bind_repairs(recipe, kc)
            capture = dict(capture)
            del capture['pv_aliases']
            path = self.temp/name/'capture.json'
            path.write_text(json.dumps(capture))
            produced, _ = generate(path, self.temp/name/'BootKernelExtensions.kc')
            self.assertEqual(produced, patches[:2])

    @unittest.skipUnless(have_tool('clang'), 'needs xcrun clang (pv_bootstrap.py assembles)')
    def test_all_four_equal_todays_producers(self):
        generate = runpy.run_path(str(FOUNDATION/'early_vm.py'))['generate']
        for name, fix in (('a', fixture.Fixture()), ('b', fixture.Fixture(moves=self.MOVES))):
            recipe, kc = self.bound(fix, fixture.Fixture(), name)
            patches, capture, _ = kcbind.bind_repairs(recipe, kc)
            path = self.temp/name/'capture.json'
            path.write_text(json.dumps(capture))
            produced, _ = generate(path, self.temp/name/'BootKernelExtensions.kc')
            self.assertEqual(produced, patches)

    def test_config_plist_is_the_build_repairs_layout(self):
        recipe, kc = self.bound()
        patches, _, _ = kcbind.bind_repairs(recipe, kc)
        config = plistlib.loads(kcbind.config_plist(patches))
        self.assertEqual(list(config), ['Kernel'])
        self.assertEqual(config['Kernel']['Patch'], patches)
        self.assertEqual(config['Kernel']['Add'][0]['BundlePath'], 'IntelCommpage.kext')

    def test_link_blob(self):
        blob = dict(code='e800000000c3488b0500000000', fixups=[dict(at=1, symbol='f'), dict(at=9, symbol='v')])
        code = kcbind.link_blob(blob, 0x1000, dict(f=0x2000, v=0x800))
        self.assertEqual(struct.unpack_from('<i', code, 1)[0], 0x2000-0x1005)
        self.assertEqual(struct.unpack_from('<i', code, 9)[0], 0x800-0x100d)
        with self.assertRaisesRegex(kcbind.BindError, 'needs v'):
            kcbind.link_blob(blob, 0, dict(f=0))
        with self.assertRaisesRegex(kcbind.BindError, 'rel32'):
            kcbind.link_blob(blob, 0, dict(f=1 << 40, v=0))
        with self.assertRaisesRegex(kcbind.BindError, 'not zero'):
            kcbind.link_blob(dict(blob, code='e801000000c3488b0500000000'), 0, dict(f=0, v=0))


class Refusals(Case):
    def tampered(self, offset, value):
        fix = fixture.Fixture()
        fix.data[offset] = value
        return fix

    def text(self, item, at=0):
        return fixture.SEGMENTS['__TEXT'][2]+fixture.CODE[item]+at

    def test_changed_code_byte(self):
        self.refuses(self.tampered(self.text('site', 0x21), 0xb9), 'site: bytes')
        self.refuses(self.tampered(self.text('pv_hook', 0xc), 0x90), 'pv_hook: bytes')
        self.refuses(self.tampered(self.text('text', 1), 0x90), 'pv_cave_tail: bytes')

    def test_field_reaching_another_target(self):
        self.refuses(self.tampered(self.text('site', 3), 0x55), 'site: field +0x3 reaches')
        self.refuses(self.tampered(self.text('range_call', 7), 0x55), 'range_call: field +0x7 reaches')

    def test_cave_not_padding(self):
        self.refuses(self.tampered(self.text('cave', 0x10), 1), 'not zero padding')

    def test_find_not_unique(self):
        fix = fixture.Fixture()
        start = self.text('range_call')
        copy = fixture.SEGMENTS['__DATA'][2]+0xc00
        fix.data[copy:copy+11] = fix.data[start:start+11]
        self.refuses(fix, 'Find is not unique')

    def test_bounds_already_ascending(self):
        fix = fixture.Fixture(reversed_bounds=False)
        self.refuses(fix, 'does not pass reversed bounds', recipe_from=fix)

    def test_other_kernel_or_version(self):
        self.refuses(fixture.Fixture(kernel_uuid=bytes(16)), 'not the kc-recipe kernel')
        self.refuses(fixture.Fixture(version='27.3.0'), 'Darwin 27.3.0')

    def test_commpage_hook_changed(self):
        fix = fixture.Fixture()
        fix.data[self.text('hook_b', 5)] = 0x90
        recipe, kc = self.bound(fix, fixture.Fixture())
        kcbind.bind_repairs(recipe, kc)
        with self.assertRaisesRegex(kcbind.BindError, 'commpage hook hook_b'):
            kcbind.commpage_table(recipe, kc)

    def test_pins(self):
        fix = fixture.Fixture()
        _, recipe = fix.write(self.temp/'p')
        (self.temp/'p/kc-recipe.json').write_text((self.temp/'p/kc-recipe.json').read_text()+'\n')
        with self.assertRaisesRegex(kcbind.BindError, 'differs from the sha256'):
            kcbind.Recipe(recipe, REPO)

    def test_select_by_kernel_uuid(self):
        recipe, kc = self.bound()
        self.assertIs(kcbind.select([recipe], kc), recipe)
        other = kcbind.Collection(bytes(fixture.Fixture(kernel_uuid=bytes(16)).data))
        with self.assertRaisesRegex(kcbind.BindError, 'MISSING'):
            kcbind.select([recipe], other)


class Commpage(Case):
    def test_table_and_header_lines(self):
        recipe, kc = self.bound()
        table = kcbind.commpage_table(recipe, kc)
        self.assertEqual(table['hooks'][0]['address'], hex(fixture.BASE+0x100000+0x2800))
        self.assertEqual(table['iosurface']['uuid'], fixture.IOSURFACE_UUID.hex())
        lines = kcbind.abi_lines(table, foundation())
        self.assertEqual(lines['pe_state_bootargs_offset'], 'static constexpr uintptr_t pe_state_bootargs_offset = 0xa0;')
        self.assertEqual(lines['boot_args_csr_offset'], 'static constexpr uintptr_t boot_args_csr_offset = 0x498;')
        header = '\n'.join(['static constexpr unsigned amfi_donor_size = 528;', *lines.values()])
        self.assertEqual({k: v for k, v in kcbind.header_definitions(header).items() if k in lines}, lines)
        rows = compare.abi_header(table, header, foundation())
        self.assertTrue(all(same for _, same, _ in rows))
        changed = header.replace(lines['map_slots'], lines['map_slots'].replace('20ULL', '28ULL'))
        bad = [what for what, same, _ in compare.abi_header(table, changed, foundation()) if not same]
        self.assertEqual(bad, ['commpage_abi.hpp map_slots'])

    def test_table_addresses(self):
        recipe, kc = self.bound()
        addresses = kcbind.table_addresses(kcbind.commpage_table(recipe, kc), kc)
        self.assertEqual(addresses[fixture.BASE+0x100000], dict(member='com.apple.kernel', segment='__TEXT',
                                                                 offset=0x1000))
        self.assertEqual(addresses[fixture.BASE+0x900200]['member'], 'com.apple.iokit.IOSurface')
        self.assertEqual(len(addresses), 3+1+1+2+1+2)


class Table(Case):
    """IntelCommpage's load-time table (__DATA,__ic_table): what the binder writes for a collection."""

    def table(self, fix=None, recipe_from=None, name='a'):
        recipe, kc = self.bound(fix, recipe_from, name)
        return recipe, kc, kcbind.commpage_table(recipe, kc)

    def test_bytes_hold_every_table_entry_and_read_back(self):
        f = foundation()
        recipe, kc, table = self.table()
        data = kcbind.table_bytes(recipe, table, f)
        layout = kcbind.table_layout(recipe, f)
        self.assertEqual(layout['hooks'], ['hook_a', 'hook_b'])
        self.assertEqual(len(data), f['table_size'](layout))
        self.assertEqual(data[:4], b'ICTB')
        values = f['read_table'](data, layout)
        self.assertEqual(values, kcbind.table_values(table))
        self.assertEqual(values['symbols']['_printf'], fixture.BASE+0x100000+0x2c00)
        self.assertEqual(values['hook_original'][1], bytes.fromhex('554889e55dc3662e0f1f84000000'))
        self.assertEqual(values['kernel_uuid'], fixture.KERNEL_UUID)
        # The layout id names the slots: another order is another layout.
        other = dict(layout, symbols=list(reversed(layout['symbols'])))
        self.assertNotEqual(f['layout_id'](other), f['layout_id'](layout))
        with self.assertRaisesRegex(ValueError, 'layout differ'):
            f['read_table'](data, other)

    def test_every_table_line_of_the_header_is_in_the_table(self):
        """The header lines a table entry replaces are exactly the collection-dependent lines that are not
        CODE_LINES; their values are the table's."""
        f = foundation()
        recipe, kc, table = self.table()
        lines = kcbind.abi_lines(table, f)
        entries = set(lines)-set(kcbind.CODE_LINES)
        self.assertEqual(entries, {'addr'+n for n in table['required']} | set(f['TABLE_DEFINITIONS']))
        values = f['read_table'](kcbind.table_bytes(recipe, table, f), kcbind.table_layout(recipe, f))
        self.assertIn(f'{values["symbols"]["_PE_state"]:#x}ULL', lines['addr_PE_state'])
        self.assertIn(f'{values["hook_sites"][0]:#x}ULL', lines['hook_sites'])

    def test_stamp_writes_a_moved_collections_table(self):
        f = foundation()
        recipe, kc, table = self.table()
        reference = kcbind.table_bytes(recipe, table, f)
        kext = fixture.kext_binary(reference)
        stamped, data = kcbind.stamp(recipe, table, f, kext)
        self.assertEqual(stamped, kext)   # the collection the kext was built for: nothing changes
        moved = fixture.Fixture(moves=Synthetic.MOVES)
        recipe2, kc2, table2 = self.table(moved, fixture.Fixture(), 'moved')
        stamped, data = kcbind.stamp(recipe2, table2, f, kext)
        self.assertNotEqual(data, reference)
        offset, size = f['find_table'](stamped)
        self.assertEqual(stamped[offset:offset+size], data)
        self.assertEqual(stamped[:offset]+stamped[offset+size:], kext[:offset]+kext[offset+size:])
        values = f['read_table'](data, kcbind.table_layout(recipe2, f))
        self.assertEqual(values['symbols']['_printf'], fixture.BASE+0x100000+0x10000+0x2c00)
        self.assertEqual(values['map_slots'], [fixture.BASE+0x200000-0x8000+0x20])

    def test_stamp_refusals(self):
        f = foundation()
        recipe, kc, table = self.table()
        reference = kcbind.table_bytes(recipe, table, f)
        cases = {'no IntelCommpage table': fixture.kext_binary(None),
                 'magic/version': fixture.kext_binary(reference[:4]+struct.pack('<I', 2)+reference[8:]),
                 'layout differ': fixture.kext_binary(reference[:16]+bytes(16)+reference[32:]),
                 'compiled for kernel': fixture.kext_binary(reference[:32]+bytes(range(0x30, 0x40))+reference[48:])}
        for text, kext in cases.items():
            with self.subTest(text), self.assertRaises(kcbind.BindError) as caught:
                kcbind.stamp(recipe, table, f, kext)
            self.assertIn(text, str(caught.exception))

    def test_command_line_stamp(self):
        f = foundation()
        recipe, kc, table = self.table()
        kext = self.temp/'IntelCommpage.kext'
        (kext/'Contents/MacOS').mkdir(parents=True)
        binary = kext/'Contents/MacOS/IntelCommpage'
        binary.write_bytes(fixture.kext_binary(kcbind.table_bytes(recipe, table, f)))
        moved = fixture.Fixture(moves=Synthetic.MOVES)
        kc_path, recipe_path = fixture.Fixture().write(self.temp/'cli')
        kc_path.write_bytes(bytes(moved.data))
        run = lambda *a: subprocess.run([sys.executable, '-I', str(CLI), *map(str, a)],  # noqa: E731
                                        capture_output=True, text=True)
        result = run('stamp', '--boot-kc', kc_path, '--recipe', recipe_path, '--kext', kext)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run('bind', '--boot-kc', kc_path, '--recipe', recipe_path, '--out', self.temp/'out')
        self.assertEqual(result.returncode, 0, result.stderr)
        offset, size = f['find_table'](binary.read_bytes())
        self.assertEqual(binary.read_bytes()[offset:offset+size], (self.temp/'out/commpage-table.bin').read_bytes())
        binary.write_bytes(fixture.kext_binary(None))
        result = run('stamp', '--boot-kc', kc_path, '--recipe', recipe_path, '--kext', kext)
        self.assertEqual(result.returncode, 1)
        self.assertIn('no IntelCommpage table', result.stderr)


class Compare(Case):
    def test_repairs_rows_name_the_field(self):
        recipe, kc = self.bound()
        patches, capture, _ = kcbind.bind_repairs(recipe, kc)
        efi = self.temp/'efi'
        (efi/'EFI/OC').mkdir(parents=True)
        (efi/'EFI/OC/config.plist').write_bytes(kcbind.config_plist(patches))
        (efi/'build.json').write_text(json.dumps(dict(inputs=dict(early_vm_repair=dict(capture=capture)))))
        self.assertTrue(all(same for _, same, _ in compare.repairs(patches, capture, kc, efi)))
        recorded = [dict(p) for p in patches]
        recorded[1]['Replace'] = recorded[1]['Replace'][:-1]+bytes([recorded[1]['Replace'][-1] ^ 1])
        (efi/'EFI/OC/config.plist').write_bytes(kcbind.config_plist(recorded))
        bad = [what for what, same, _ in compare.repairs(patches, capture, kc, efi) if not same]
        self.assertEqual(bad, ['Pass early kernel-map reservation bounds in ascending order: Replace',
                               'config.plist bytes'])

    def test_command_line(self):
        fix = fixture.Fixture()
        kc, recipe = fix.write(self.temp/'cli')
        out = self.temp/'out'
        run = lambda *a: subprocess.run([sys.executable, '-I', str(CLI), *map(str, a)],  # noqa: E731
                                        capture_output=True, text=True)
        result = run('bind', '--boot-kc', kc, '--recipe', recipe, '--out', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((out/'bind.json').read_text())
        self.assertEqual(len(record['patches']), 4)
        self.assertIn('EFI/OC/config.plist', record['files'])
        result = run('compare', '--boot-kc', kc, '--recipe', recipe, '--repair-efi', out)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(' 0 differ', result.stdout)
        fix.data[fixture.SEGMENTS['__TEXT'][2]+fixture.CODE['site']+0x21] = 0xb9
        kc.write_bytes(bytes(fix.data))
        result = run('bind', '--boot-kc', kc, '--recipe', recipe, '--out', self.temp/'out2')
        self.assertEqual(result.returncode, 1)
        self.assertIn('site: bytes', result.stderr)


class Stage(Case):
    """`kc-binder stage`: the run-time closure at repository-relative paths, checked by stage.json."""

    def test_staged_binder_binds_as_the_checkout_does(self):
        import stage
        out = self.temp/'stage'
        result = subprocess.run([sys.executable, '-I', str(CLI), 'stage', str(out)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = stage.verify(out)
        self.assertEqual(list(record['files']), sorted(stage.closure()))
        self.assertIn('tools/kernel-fixes/profiles/26B131/kc-recipe.json', record['files'])
        for path in sorted((out/'tools/kc-binder/recipes').glob('*.json')):
            kcbind.Recipe(path, out)   # every pin resolves inside the stage
        kc, recipe = fixture.Fixture().write(self.temp/'fix')
        for name, cli in (('checkout', CLI), ('staged', out/'tools/kc-binder/kc-binder')):
            result = subprocess.run([sys.executable, '-I', str(cli), 'bind', '--boot-kc', str(kc), '--recipe',
                                     str(recipe), '--out', str(self.temp/name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.temp/'checkout/EFI/OC/config.plist').read_bytes(),
                         (self.temp/'staged/EFI/OC/config.plist').read_bytes())
        (out/'tools/kc-binder/src/compare.py').write_text('# changed\n')
        with self.assertRaisesRegex(ValueError, 'compare.py differs from stage.json'):
            stage.verify(out)


class CheckedIn(unittest.TestCase):
    """recipes/ and blobs/: pins hold, no Apple bytes, and the blob is today's assembly."""

    def test_recipes_load_and_hold_only_masks_hashes_and_points(self):
        recipes = sorted((UNIT/'recipes').glob('*.json'))
        self.assertTrue(recipes)
        for path in recipes:
            recipe = kcbind.Recipe(path, REPO)
            checks = list(recipe.binder['code'].values())+recipe.binder['commpage']['hooks']
            checks.append(recipe.binder['commpage']['iosurface']['entry'])
            for check in checks:
                mask = bytes.fromhex(check['mask'])
                self.assertEqual(len(mask), check['length'])
                self.assertLessEqual(set(mask), {0, 0xff})
                self.assertEqual(len(check['masked_sha256']), 64)
            self.assertFalse({'original', 'bytes', 'find', 'Find'} & set(json.dumps(recipe.binder).split('"')))

    def test_blob_source_pin(self):
        blob = json.loads((UNIT/'blobs/pv-alias.json').read_text())
        self.assertEqual(kcbind.sha256((REPO/blob['source']['path']).read_bytes()), blob['source']['sha256'])
        self.assertEqual(sorted(f['symbol'] for f in blob['fixups']), sorted(blob['symbols']))

    @unittest.skipUnless(have_tool('clang'), 'needs xcrun clang')
    def test_blob_reassembles(self):
        import make_recipe
        blob = json.loads((UNIT/'blobs/pv-alias.json').read_text())
        self.assertEqual(make_recipe.make_blob(REPO/blob['source']['path'], blob['symbols']), blob)


@unittest.skipUnless(os.environ.get('KC_BINDER_BOOT_KC'), 'KC_BINDER_BOOT_KC not set')
class Recorded(unittest.TestCase):
    """The recorded 26B131 artifacts, field for field."""

    def collections(self):
        for path in env_paths('KC_BINDER_BOOT_KC'):
            kc = kcbind.Collection(path.read_bytes())
            recipe = kcbind.select([kcbind.Recipe(p, REPO) for p in sorted((UNIT/'recipes').glob('*.json'))], kc)
            patches, capture, _ = kcbind.bind_repairs(recipe, kc)
            yield path, kc, recipe, patches, capture

    def assertRows(self, rows, path):
        bad = [f'{what}: {detail}' for what, same, detail in rows if not same]
        self.assertEqual(bad, [], path)
        self.assertTrue(rows)

    @unittest.skipUnless(os.environ.get('KC_BINDER_REPAIR_EFI'), 'KC_BINDER_REPAIR_EFI not set')
    def test_kernel_patches_byte_identical(self):
        efi = Path(os.environ['KC_BINDER_REPAIR_EFI'])
        for path, kc, _, patches, capture in self.collections():
            self.assertEqual(kcbind.config_plist(patches), (efi/'EFI/OC/config.plist').read_bytes(), path)
            self.assertRows(compare.repairs(patches, capture, kc, efi), path)

    @unittest.skipUnless(os.environ.get('KC_BINDER_REPAIR_BINDING'), 'KC_BINDER_REPAIR_BINDING not set')
    def test_binding_profile_sites_and_abi_bindings(self):
        profile = json.loads((Path(os.environ['KC_BINDER_REPAIR_BINDING'])/'profile.json').read_text())
        for path, kc, recipe, patches, _ in self.collections():
            self.assertRows(compare.binding(patches, kcbind.commpage_table(recipe, kc), kc, profile), path)

    @unittest.skipUnless(os.environ.get('KC_BINDER_COMMPAGE_ABI'), 'KC_BINDER_COMMPAGE_ABI not set')
    def test_commpage_abi_header_lines(self):
        text = Path(os.environ['KC_BINDER_COMMPAGE_ABI']).read_text()
        for path, kc, recipe, _, _ in self.collections():
            self.assertRows(compare.abi_header(kcbind.commpage_table(recipe, kc), text, foundation()), path)

    @unittest.skipUnless(os.environ.get('KC_BINDER_COMMPAGE_KEXT') and os.environ.get('KC_BINDER_COMMPAGE_ABI'),
                         'KC_BINDER_COMMPAGE_KEXT and KC_BINDER_COMMPAGE_ABI not set')
    def test_table_equals_the_compiled_values(self):
        """The table the binder writes for each collection is byte for byte the one IntelCommpage.kext holds, and
        its entries read back as the lines of the header (r41's IntelCommpage compiled exactly these lines)."""
        f = foundation()
        binary = (Path(os.environ['KC_BINDER_COMMPAGE_KEXT'])/'Contents/MacOS/IntelCommpage').read_bytes()
        recorded = kcbind.header_definitions(Path(os.environ['KC_BINDER_COMMPAGE_ABI']).read_text())
        for path, kc, recipe, _, _ in self.collections():
            table = kcbind.commpage_table(recipe, kc)
            stamped, data = kcbind.stamp(recipe, table, f, binary)
            self.assertEqual(stamped, binary, path)
            v = f['read_table'](data, kcbind.table_layout(recipe, f))
            words = lambda seq: ','.join(f'{x:#x}ULL' for x in seq)  # noqa: E731
            lines = {f'addr{n}': f'static constexpr uintptr_t addr{n} = {a:#x}ULL;' for n, a in v['symbols'].items()}
            lines.update(
                addr_kernel_header=f'static constexpr uintptr_t addr_kernel_header = {v["kernel_header"]:#x}ULL;',
                addr_iosurface_header=f'static constexpr uintptr_t addr_iosurface_header = {v["iosurface_header"]:#x}ULL;',
                addr_iosurface_set_value=('static constexpr uintptr_t addr_iosurface_set_value = '
                                          f'{v["iosurface_set_value"]:#x}ULL;'),
                alias_slots='static const uintptr_t alias_slots[] = {'+words(v['alias_slots'])+'};',
                map_slots='static const uintptr_t map_slots[] = {'+words(v['map_slots'])+'};',
                hook_sites='static const uintptr_t hook_sites[] = {'+words(v['hook_sites'])+'};',
                hook_original='static const unsigned char hook_original[][14] = {'+','.join(
                    '{'+','.join(str(b) for b in h)+'}' for h in v['hook_original'])+'};',
                target_uuid='static const unsigned char target_uuid[] = {'+','.join(map(str, v['kernel_uuid']))+'};')
            self.assertEqual(len(lines), len(table['required'])+len(f['TABLE_DEFINITIONS'])+1)
            self.assertEqual({k: recorded.get(k) for k in lines}, lines, path)

    @unittest.skipUnless(os.environ.get('KC_BINDER_KERNEL') and os.environ.get('KC_BINDER_IOSURFACE_LAYOUT')
                         and have_tool('llvm-objdump'), 'KC_BINDER_KERNEL, KC_BINDER_IOSURFACE_LAYOUT, llvm-objdump')
    def test_make_recipe_reproduces_checked_in(self):
        import make_recipe
        layout = Path(os.environ['KC_BINDER_IOSURFACE_LAYOUT'])
        reference = json.loads(layout.read_text())['collection_sha256']
        kc = next((p for p in env_paths('KC_BINDER_BOOT_KC') if kcbind.sha256(p.read_bytes()) == reference), None)
        if kc is None:
            self.skipTest('no KC_BINDER_BOOT_KC is the IOSurface layout collection')
        checked_in = UNIT/'recipes/26B131.json'
        recorded = json.loads(checked_in.read_text())
        made = make_recipe.make_recipe(REPO/recorded['kc_recipe']['path'], REPO/'tools/kernel-fixes/profiles/26B131',
                                       REPO/recorded['pv_blob']['path'], Path(os.environ['KC_BINDER_KERNEL']), kc,
                                       layout)
        self.assertEqual(made, recorded)


if __name__ == '__main__':
    unittest.main()
