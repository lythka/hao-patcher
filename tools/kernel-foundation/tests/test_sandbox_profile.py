"""Host tests for the Sandbox operation layout: sandbox_layout.py and the src/sandbox_profile.h transform.

Run: python3.14 -I -m unittest discover -s tools/kernel-foundation/tests
The transform is compiled from the same header IntelCommpage uses into a host harness. Synthetic profiles always
run; real profiles are compiled by the host's own libsandbox (no target code runs) when its operation names equal
the stored layout's target names (read from its Rosetta x86_64 cache) and the sandbox_op_layout_26A428 row
resolves, else those tests skip.
"""
import ctypes
import json
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LAYOUT = runpy.run_path(str(HERE.parent/'sandbox_layout.py'))
PROFILE = REPO/'tools/kernel-fixes/profiles/26A428/sandbox-op-layout.json'
ROSETTA_CACHE = Path(os.environ['DARWIN27_ROSETTA_CACHE']) if os.environ.get('DARWIN27_ROSETTA_CACHE') else None
HARNESS = None
TEMP = None


def setUpModule():
    global HARNESS, TEMP
    TEMP = tempfile.TemporaryDirectory()
    HARNESS = Path(TEMP.name)/'harness'
    subprocess.run(['xcrun', 'clang', '-std=c99', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(HARNESS),
                    str(HERE/'sandbox_profile_harness.c')], check=True)


def tearDownModule():
    TEMP.cleanup()


def u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


def adapt(data, layout):
    """Run the harness: (status, reason, output bytes). Status 0 pass, 1 adapted, 2 refused."""
    source, target = Path(TEMP.name)/'in.bin', Path(TEMP.name)/'out.bin'
    source.write_bytes(data)
    mig = layout.get('mig')
    extra = [str(mig['filter']), ','.join(str(0xffff if d is None else d) for d in mig['map'])] if mig else []
    out = subprocess.run([str(HARNESS), str(layout['target_operation_count']), str(layout['donor_operation_count']),
                          str(layout['target_name_count']), str(layout['donor']['filter_max']),
                          ','.join(map(str, layout['drop'])) or '', str(source), str(target), *extra],
                         check=True, capture_output=True, text=True).stdout.split()
    return int(out[0]), int(out[1]), target.read_bytes()


def shape(data):
    """Offsets as the donor's profile_init derives them (Sandbox+0x29ac0, 25G330)."""
    tables = u16(data, 0xc)+data[5]+data[6]+data[7]+data[8]+u16(data, 0xe)
    ops = 0x10+2*tables
    nodes = (ops+2*data[4]+7) & ~7
    return dict(ops=ops, nodes=nodes, data=nodes+8*u16(data, 2), count=u16(data, 2))


def modifier_sites(data):
    """Offsets of message-filter modifiers (id 0x15) in terminal nodes."""
    s, sites = shape(data), []
    for n in range(s['count']):
        node = s['nodes']+8*n
        if data[node] != 1:
            continue
        if data[node+3] & 0x80:
            mods = [node+4]
        else:
            mods = [s['data']+8*u16(data, node+6)+4*k for k in range(data[node+4])]
        sites += [m for m in mods if data[m] == 0x15]
    return sites


def mig_objects(data, layout):
    """Offsets of the bitmap objects syscall-mig mask filter nodes refer to (each once)."""
    mig = layout.get('mig')
    if not mig:
        return []
    s, found = shape(data), []
    for n in range(s['count']):
        node = s['nodes']+8*n
        if data[node] == 0 and data[node+1] == mig['filter'] | 0x80:
            at = s['data']+8*u16(data, node+2)
            if at not in found:
                found.append(at)
    return found


def mig_bits(data, at):
    return {i for i in range(8*u16(data, at+2)) if data[at+4+i//8] >> (i % 8) & 1}


def mig_remapped(data, at, layout):
    """Reference remap of one bitmap object: (new length, new bytes over the old length)."""
    length = u16(data, at+2)
    bits = {layout['mig']['map'][i] for i in mig_bits(data, at)} - {None}
    used = max((b//8+1 for b in bits), default=1 if length else 0)
    out = bytearray(length)
    for b in bits:
        out[b//8] |= 1 << (b % 8)
    return used, bytes(out)


class Checks:
    def assert_adapted(self, before, after, layout):
        """Donor view of `after` is `before` renumbered: entries moved, nodes and data shifted, message ops mapped."""
        old, new = shape(before), shape(after)
        self.assertEqual(after[4], layout['donor_operation_count'])
        self.assertEqual(after[:4]+after[5:old['ops']], before[:4]+before[5:old['ops']])
        self.assertEqual(new['ops'], old['ops'])
        self.assertEqual(len(after), len(before)-(old['nodes']-new['nodes']))
        for target, donor in enumerate(layout['map'][:layout['target_operation_count']]):
            if donor is not None:
                self.assertEqual(u16(after, new['ops']+2*donor), u16(before, old['ops']+2*target))
        expected = bytearray(before[old['nodes']:])
        for site in modifier_sites(before):
            expected[site-old['nodes']+1] = layout['map'][before[site+1]]
        for n in range(old['count']) if layout.get('mig') else ():
            node = old['nodes']+8*n
            if before[node] == 0 and before[node+1] == layout['mig']['filter']:
                d = layout['mig']['map'][u16(before, node+2)]
                expected[node-old['nodes']+2:node-old['nodes']+4] = struct.pack('<H', 0xffff if d is None else d)
        for at in mig_objects(before, layout):
            used, remapped = mig_remapped(before, at, layout)
            base = at-old['nodes']
            expected[base+2:base+4] = struct.pack('<H', used)
            expected[base+4:base+4+len(remapped)] = remapped
        self.assertEqual(after[new['nodes']:], bytes(expected))
        for donor in range(layout['donor_operation_count']):
            entry = new['nodes']+8*u16(after, new['ops']+2*donor)
            self.assertLess(entry, len(after))
            self.assertIn(after[entry], (0, 1))


def synthetic_layout():
    names = ['op%d' % i for i in range(204)]+['default-message-filter']+['mf%d' % i for i in range(5)]
    donor = [n for i, n in enumerate(names) if i not in (2, 5, 46, 89)]
    layout = LAYOUT['op_layout'](names, donor, 200)
    layout['donor'] = dict(filter_max=105)
    return layout


def profile(ops=204, flags=0, tables=(0, 0, 0, 0, 0, 0), nodes=(), data=b'', entries=None):
    """A compiled profile: header, zeroed tables, operation entries, nodes at the next 8-byte boundary, data."""
    rx, b5, b6, b7, b8, pol = tables
    head = struct.pack('<HHBBBBBBHHH', flags, len(nodes), ops, b5, b6, b7, b8, 0, 0, rx, pol)
    body = head+b'\0'*2*sum(tables)
    entries = entries or [i % max(len(nodes), 1) for i in range(ops)]
    body += b''.join(struct.pack('<H', e) for e in entries)
    body += b'\0'*(-len(body) % 8)
    return body+b''.join(nodes)+data


def filter_node(filter_id, match, unmatch, argument=0):
    return struct.pack('<BBHHH', 0, filter_id, argument, match, unmatch)


def terminal(action=0, inline=None, count=0, offset=0):
    if inline is not None:
        return bytes([1, action, 0, 0x80])+inline
    return bytes([1, action, 0, 0, count, 0])+struct.pack('<H', offset)


class RemovedOperationRoots(unittest.TestCase):
    layout = synthetic_layout()

    def fixture(self, **changes):
        values = dict(nodes=(filter_node(110, 3, 4), filter_node(1, 2, 4),
                             filter_node(6, 3, 4), terminal(0), terminal(5)),
                      entries=[0 if op == 2 else 1 for op in range(204)], data=b'keptdata')
        values.update(changes)
        return profile(**values)

    def decision(self, data, op, matches):
        s = shape(data)
        index = u16(data, s['ops'] + 2*op)
        for _ in range(s['count']):
            at = s['nodes'] + 8*index
            if data[at] == 1:
                return data[at:at+8]
            index = u16(data, at + (4 if data[at+1] in matches else 6))
        self.fail('cycle in decision graph')

    def test_pruning_preserves_every_retained_operation_decision(self):
        before = self.fixture()
        status, reason, after = adapt(before, self.layout)
        self.assertEqual((status, reason), (1, 0))
        self.assertEqual(shape(after)['count'], 4)
        self.assertEqual(after[shape(after)['data']:], b'keptdata')
        for target, donor in enumerate(self.layout['map'][:204]):
            if donor is None:
                continue
            for matches in (set(), {1}, {6}, {1, 6}):
                self.assertEqual(self.decision(before, target, matches), self.decision(after, donor, matches))

    def test_shared_unsupported_filters_still_refuse_without_writes(self):
        entries = [0 if op in (2, 100) else 1 for op in range(204)]
        cases = [self.fixture(entries=entries),
                 self.fixture(nodes=(filter_node(110, 3, 4), filter_node(1, 0, 4),
                                     filter_node(6, 3, 4), terminal(0), terminal(5))),
                 self.fixture(tables=(0, 0, 0, 0, 0, 1)),
                 self.fixture(tables=(0, 1, 0, 0, 0, 0)),
                 self.fixture(nodes=(filter_node(110, 0, 4), filter_node(1, 2, 4),
                                     filter_node(6, 3, 4), terminal(0), terminal(5)))]
        for before in cases:
            self.assertEqual(adapt(before, self.layout), (2, 4, before))

    def test_later_validation_failure_does_not_partially_prune(self):
        before = self.fixture(nodes=(filter_node(110, 3, 4), filter_node(1, 2, 4),
                                     filter_node(111, 3, 4), terminal(0), terminal(5)))
        self.assertEqual(adapt(before, self.layout), (2, 4, before))

    def test_invalid_retained_edge_refuses_without_writes(self):
        before = self.fixture(nodes=(filter_node(110, 3, 4), filter_node(1, 5, 4),
                                     filter_node(6, 3, 4), terminal(0), terminal(5)))
        self.assertEqual(adapt(before, self.layout), (2, 32, before))


class Generator(unittest.TestCase):
    def test_drops_inserted_operations_and_keeps_order(self):
        layout = synthetic_layout()
        self.assertEqual(layout['drop'], [2, 5, 46, 89])
        self.assertEqual((layout['target_operation_count'], layout['donor_operation_count']), (204, 200))
        self.assertEqual(layout['map'][3], 2)
        self.assertEqual(layout['map'][204:], [200, 201, 202, 203, 204, 205])

    def test_rejects_reordered_or_missing_donor_names(self):
        names = ['default', 'a', 'b', 'default-message-filter']
        with self.assertRaisesRegex(ValueError, 'order'):
            LAYOUT['op_layout'](names, ['default', 'b', 'a', 'default-message-filter'], 3)
        with self.assertRaisesRegex(ValueError, 'missing'):
            LAYOUT['op_layout'](names, ['default', 'a', 'b', 'default-message-filter', 'c'], 3)
        with self.assertRaisesRegex(ValueError, 'count'):
            LAYOUT['op_layout'](names, ['default', 'a', 'default-message-filter'], 3)


def synthetic_mig_layout():
    """Target routines 0-2 keep their index, 3 has no donor routine, 4-20 move down by one."""
    layout = synthetic_layout()
    layout['mig'] = dict(filter=0x4c, map=[0, 1, 2, None]+list(range(3, 20)))
    return layout


def mig_object(bits, length=None):
    raw = bytearray(length if length is not None else max(bits)//8+1)
    for b in bits:
        raw[b//8] |= 1 << (b % 8)
    return bytes([1, 0])+struct.pack('<H', len(raw))+bytes(raw)


class SyntheticMig(unittest.TestCase, Checks):
    layout = synthetic_mig_layout()

    def test_moves_bits_to_donor_routines(self):
        data = b'pad-pad\0'+mig_object({0, 3, 5, 12, 20})+b'\0'*5
        nodes = (filter_node(0x80 | 0x4c, 1, 2, 1), terminal(), terminal(1))
        before = profile(nodes=nodes, data=data)
        status, reason, after = adapt(before, self.layout)
        self.assertEqual((status, reason), (1, 0))
        self.assert_adapted(before, after, self.layout)
        at = shape(after)['data']+8
        self.assertEqual(mig_bits(after, at), {0, 4, 11, 19})
        self.assertEqual(u16(after, at+2), 3)

    def test_shared_object_is_remapped_once(self):
        data = mig_object({4, 5})+b'\0'*2
        nodes = (filter_node(0x80 | 0x4c, 1, 2, 0), filter_node(0x80 | 0x4c, 2, 3, 0), terminal(), terminal(1))
        before = profile(nodes=nodes, data=data)
        status, reason, after = adapt(before, self.layout)
        self.assertEqual((status, reason), (1, 0))
        self.assertEqual(mig_bits(after, shape(after)['data']), {3, 4})
        self.assert_adapted(before, after, self.layout)

    def test_only_dropped_routines_leave_one_zero_byte(self):
        before = profile(nodes=(filter_node(0x80 | 0x4c, 1, 1, 0), terminal()), data=mig_object({3}, 2)+b'\0'*2)
        status, _, after = adapt(before, self.layout)
        self.assertEqual(status, 1)
        at = shape(after)['data']
        self.assertEqual((u16(after, at+2), mig_bits(after, at)), (1, set()))

    def test_inline_routine_indices_move_and_unknown_ones_match_nothing(self):
        nodes = (filter_node(0x4c, 1, 2, 5), filter_node(0x4c, 2, 3, 3), filter_node(0x4c, 3, 3, 1), terminal(),
                 terminal(1))
        before = profile(nodes=nodes, data=b'\0'*8)
        status, reason, after = adapt(before, self.layout)
        self.assertEqual((status, reason), (1, 0))
        self.assert_adapted(before, after, self.layout)
        n = shape(after)['nodes']
        self.assertEqual([u16(after, n+8*k+2) for k in range(3)], [4, 0xffff, 1])

    def test_inline_index_outside_the_map_refuses(self):
        before = profile(nodes=(filter_node(0x4c, 1, 1, 21), terminal()), data=b'\0'*8)
        self.assertEqual(adapt(before, self.layout), (2, 64, before))

    def test_other_filters_keep_their_data(self):
        before = profile(nodes=(filter_node(0x4b, 1, 1, 0), terminal()), data=mig_object({4, 5})+b'\0'*2)
        status, _, after = adapt(before, self.layout)
        self.assertEqual(status, 1)
        self.assertEqual(after[shape(after)['data']:], before[shape(before)['data']:])

    def test_refuses_unmappable_bitmaps_without_writing(self):
        cases = [
            profile(nodes=(filter_node(0x80 | 0x4c, 1, 1, 0), terminal()), data=mig_object({21})+b'\0'*4),
            profile(nodes=(filter_node(0x80 | 0x4c, 1, 1, 0), terminal()), data=bytes([2, 0, 1, 0, 1, 0, 0, 0])),
            profile(nodes=(filter_node(0x80 | 0x4c, 1, 1, 0), terminal()), data=bytes([1, 0, 65, 0])+b'\0'*68),
            profile(nodes=(filter_node(0x80 | 0x4c, 1, 1, 0), terminal()), data=bytes([1, 0, 9, 0, 1, 0, 0, 0])),
            profile(nodes=(filter_node(0x80 | 0x4c, 1, 1, 3), terminal()), data=mig_object({1})+b'\0'*3),
        ]
        for before in cases:
            self.assertEqual(adapt(before, self.layout), (2, 64, before))


class Synthetic(unittest.TestCase, Checks):
    layout = synthetic_layout()

    def test_renumbers_entries_nodes_and_message_filters(self):
        mods = bytes([0x15, 205, 0, 0, 0x15, 206, 1, 0, 0x03, 0, 0, 0])
        nodes = (filter_node(6, 1, 2), terminal(inline=bytes([0x15, 208, 0, 0])),
                 terminal(count=3, offset=0), filter_node(0x80 | 73, 1, 2, 1))
        for tables in ((0, 0, 0, 0, 0, 0), (1, 2, 0, 1, 0, 1), (3, 0, 1, 0, 0, 2)):
            before = profile(tables=tables, nodes=nodes, data=mods+b'tail\0\0\0\0')
            status, reason, after = adapt(before, self.layout)
            self.assertEqual((status, reason), (1, 0), tables)
            self.assert_adapted(before, after, self.layout)
            self.assertEqual(after[-20:-8], bytes([0x15, 201, 0, 0, 0x15, 202, 1, 0, 0x03, 0, 0, 0]))

    def test_passes_collections_donor_count_and_short_input(self):
        for before in (profile(flags=0x8000, nodes=(terminal(),)), profile(ops=200, nodes=(terminal(),)), b'\0'*8):
            self.assertEqual(adapt(before, self.layout), (0, 0, before))

    def test_refuses_without_writing(self):
        cases = [
            (1, profile(nodes=(terminal(),))[:-4]),
            (2, profile(nodes=(bytes([2])+b'\0'*7,))),
            (4, profile(nodes=(filter_node(106, 0, 0), terminal()))),
            (8, profile(nodes=(terminal(inline=bytes([0x15, 7, 0, 0])),))),
            (8, profile(nodes=(terminal(inline=bytes([0x15, 210, 0, 0])),))),
            (16, profile(nodes=(terminal(count=2, offset=5),), data=b'\0'*8)),
            (32, profile(nodes=(terminal(),), entries=[0]*203+[1])),
        ]
        for reason, before in cases:
            self.assertEqual(adapt(before, self.layout), (2, reason, before), reason)


def host_profiles(sources):
    """Compile SBPL strings and files with the host's libsandbox; returns {name: bytes}."""
    lib = ctypes.CDLL('/usr/lib/libsandbox.1.dylib')

    class Profile(ctypes.Structure):
        _fields_ = [('type', ctypes.c_uint32), ('pad', ctypes.c_uint32),
                    ('data', ctypes.c_void_p), ('size', ctypes.c_size_t)]
    for name in ('sandbox_compile_string', 'sandbox_compile_file'):
        getattr(lib, name).restype = ctypes.POINTER(Profile)
        getattr(lib, name).argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
    lib.sandbox_create_params.restype = ctypes.c_void_p
    lib.sandbox_free_profile.argtypes = [ctypes.POINTER(Profile)]
    out = {}
    for name, (kind, value) in sources.items():
        error = ctypes.c_char_p()
        compiled = getattr(lib, 'sandbox_compile_'+kind)(value.encode(), lib.sandbox_create_params(), ctypes.byref(error))
        if compiled:
            out[name] = ctypes.string_at(compiled.contents.data, compiled.contents.size)
            lib.sandbox_free_profile(compiled)
    return out


def stored_layout():
    return json.loads(Path(os.environ.get('DARWIN27_SANDBOX_OP_LAYOUT', PROFILE)).read_text())


MESSAGE_FILTERS = {
    'iokit': '(version 1)(deny default)(allow iokit-open-user-client (iokit-user-client-class "X")'
             '(apply-message-filter (deny iokit-async-external-method)'
             '(allow iokit-async-external-method (iokit-method-number 1))'
             '(deny iokit-external-method) (allow iokit-external-method (iokit-method-number 2 3))))',
    'mach': '(version 1)(deny default)(allow mach-lookup (global-name "com.example")'
            '(apply-message-filter (deny mach-message-send) (allow mach-message-send (message-number 7))))',
    'plain': '(version 1)(deny default)(allow file-read* (subpath "/usr"))(allow fs-info)(allow coalition-info)',
}


@unittest.skipUnless(sys.platform == 'darwin', 'needs the host libsandbox')
class HostCompiled(unittest.TestCase, Checks):
    @classmethod
    def setUpClass(cls):
        cls.layout = stored_layout()
        if cls.layout is None:
            raise unittest.SkipTest('target layout is unavailable')
        if ROSETTA_CACHE is None or not ROSETTA_CACHE.exists():
            raise unittest.SkipTest('no host Rosetta x86_64 cache to compare operation names')
        host = LAYOUT['target_names'](LAYOUT['Cache'](ROSETTA_CACHE), '/usr/lib/libsandbox.1.dylib')
        if host['names'] != cls.layout['target']['names']:
            raise unittest.SkipTest('host libsandbox operation names differ from the layout target')
        sources = {k: ('string', v) for k, v in MESSAGE_FILTERS.items()}
        for folder in ('/System/Library/Sandbox/Profiles', '/usr/share/sandbox'):
            sources.update({p.name: ('file', str(p)) for p in sorted(Path(folder).glob('*.sb'))})
        cls.profiles = host_profiles(sources)

    def test_host_compiles_target_operation_count(self):
        for name in MESSAGE_FILTERS:
            self.assertEqual(self.profiles[name][4], self.layout['target_operation_count'], name)

    def test_message_filters_carry_absolute_operation_numbers(self):
        names = self.layout['target']['names']
        sites = {self.profiles['iokit'][s+1] for s in modifier_sites(self.profiles['iokit'])}
        self.assertEqual(sites, {names.index('iokit-async-external-method'), names.index('iokit-external-method')})
        status, _, after = adapt(self.profiles['iokit'], self.layout)
        self.assertEqual(status, 1)
        donor = self.layout['donor']['names']
        self.assertEqual({after[s+1] for s in modifier_sites(after)},
                         {donor.index('iokit-async-external-method'), donor.index('iokit-external-method')})

    def test_every_compiled_profile_adapts_exactly(self):
        self.assertGreater(len(self.profiles), 100)
        for name, before in self.profiles.items():
            status, reason, after = adapt(before, self.layout)
            self.assertEqual((status, reason), (1, 0), name)
            self.assert_adapted(before, after, self.layout)

    def test_mig_rules_follow_routine_names(self):
        mig = self.layout.get('mig')
        if not mig:
            self.skipTest('layout has no kernel-mig-routine map')
        host = LAYOUT['target_mig'](LAYOUT['Cache'](ROSETTA_CACHE), '/usr/lib/libsandbox.1.dylib')
        host_names = {n: i for n, i in host['entries']}
        if any(host_names.get(n) != i for i, n in enumerate(mig['target_names']) if n):
            self.skipTest('host kernel-mig-routine table differs from the layout target')
        routines = ['host_info', 'mach_port_get_refs', 'mach_port_set_attributes', 'task_info_from_user',
                    'io_service_get_wait_info']
        compiled = host_profiles({'mig': ('string', '(version 1)(allow default)(deny syscall-mig)'
                                          '(allow syscall-mig (kernel-mig-routine %s))' % ' '.join(routines))})['mig']
        (at,) = mig_objects(compiled, self.layout)
        self.assertEqual(mig_bits(compiled, at) - {50, 51, 52}, {host_names[n] for n in routines})
        status, reason, after = adapt(compiled, self.layout)
        self.assertEqual((status, reason), (1, 0))
        self.assert_adapted(compiled, after, self.layout)
        donor = {n: i for i, n in enumerate(mig['donor_names']) if n}
        (new,) = mig_objects(after, self.layout)
        expected = {donor[n] for n in routines if n in donor} | {mig['map'][i] for i in (50, 51, 52)}
        self.assertEqual(mig_bits(after, new), expected)
        self.assertEqual(donor['mach_port_get_refs'], 155)

    def test_named_operations_keep_their_rules(self):
        target, donor = self.layout['target']['names'], self.layout['donor']['names']
        before = self.profiles['plain']
        _, _, after = adapt(before, self.layout)
        old, new = shape(before), shape(after)
        for op in ('file-read-data', 'file-read*', 'mach-lookup', 'sysctl-read', 'process-exec-update-label'):
            a = old['nodes']+8*u16(before, old['ops']+2*target.index(op))
            b = new['nodes']+8*u16(after, new['ops']+2*donor.index(op))
            self.assertEqual(a-b, old['nodes']-new['nodes'], op)
            self.assertEqual(before[a:a+8], after[b:b+8], op)


if __name__ == '__main__':
    unittest.main()
