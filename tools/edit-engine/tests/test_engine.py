"""Edit engine: spec validation, the three edit kinds, refusals, postconditions, events and the command line.
python3.14 -I -B -m unittest discover -s tools/edit-engine/tests"""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
sys.path.insert(0, str(HERE))
import engine   # noqa: E402
import macho   # noqa: E402
import spec as S   # noqa: E402
import store as ST   # noqa: E402
import fixture   # noqa: E402

CODE = bytes.fromhex('554889e5b0010fbec05dc3')          # push rbp; mov rbp,rsp; mov al,1; movsx eax,al; pop rbp; ret
SYMS = {'_answer': 0x1100, '_other': 0x1200}


def image(**kw):
    kw.setdefault('symbols', SYMS)
    kw.setdefault('code', {0x1100: CODE, 0x1200: CODE})
    return fixture.dylib(**kw)


def literal(eid='answer-no', **kw):
    e = {'id': eid, 'kind': 'literal', 'base': '_answer', 'limit': 32, 'find': 'B0 01 0F BE', 'replace': 'B0 00 0F BE',
         'reason': 'answer NO'}
    e.update(kw)
    return e


def spec(*edits, **kw):
    s = {'format': 1, 'path': 'usr/lib/libfixture.dylib', 'arch': 'x86_64', 'origin': {'kind': 'apple'},
         'edits': list(edits), 'sign': {'mode': 'adhoc'}}
    s.update(kw)
    return s


class Validation(unittest.TestCase):
    def test_good_spec(self):
        self.assertEqual(S.validate(spec(literal())), [])

    def test_each_rule(self):
        cases = [
            (spec(literal(), path='/abs'), 'path must be a relative path'),
            (spec(literal(), origin={'kind': 'elsewhere'}), 'origin must be'),
            (spec(literal(), extra=1), 'unknown keys extra'),
            (spec(literal(eid='Bad_ID')), 'id must be'),
            (spec(literal(), literal()), 'duplicate id'),
            (spec(literal(reason='')), 'reason is required'),
            (spec(literal(replace='B0 00')), 'same length'),
            (spec(literal(replace='B0 01 0F BE')), 'find and replace are the same'),
            (spec(literal(at='0x10')), 'exactly one of at'),
            (spec(literal(limit=2)), 'base needs limit'),
            (spec(literal(find='ZZ', replace='00')), 'hex bytes'),
            (spec(literal(mask='FF')), 'mask must be as long'),
            (spec({'id': 'm', 'kind': 'matcher', 'reason': 'x'}), 'a matcher needs fn'),
            (spec({'id': 'o', 'kind': 'op', 'op': 'macho.add-dylib', 'args': {'path': 'rel'}, 'reason': 'x'}),
             'path must be absolute'),
            (spec({'id': 'o', 'kind': 'op', 'op': 'macho.add-dylib', 'args': {'path': '/a', 'weak': 1}, 'reason': 'x'}),
             'weak must be bool'),
            (spec({'id': 'o', 'kind': 'op', 'op': 'nope', 'reason': 'x'}), 'op must be one of'),
            (spec(literal(), {'id': 'p', 'kind': 'op', 'op': 'plist.set', 'args': {'key': ['a'], 'value': 1},
                              'reason': 'x'}), 'cannot be mixed'),
            (spec(literal(), expect={'abc': 'def'}), 'expect maps'),
            (spec(literal(), sign={'mode': 'identity'}), 'sign must be'),
        ]
        for s, want in cases:
            self.assertTrue(any(want in p for p in S.validate(s)), '%s not in %s' % (want, S.validate(s)))

    def test_hexbytes_and_offset(self):
        self.assertEqual(S.hexbytes('b0 01\n0F'), b'\xb0\x01\x0f')
        self.assertEqual(S.offset('0x1f'), 31)
        for bad in ('B0 1', '', 'GG'):
            with self.assertRaises(ValueError):
                S.hexbytes(bad)
        for bad in ('0x', '1', -1, True):
            with self.assertRaises(ValueError):
                S.offset(bad)


class Literal(unittest.TestCase):
    def test_symbol_anchored_edit(self):
        events = []
        out, rec = engine.apply(spec(literal()), image(), on_event=events.append)
        self.assertEqual(out[0x1104:0x1106], b'\xb0\x00')
        self.assertEqual(out[0x1204:0x1206], b'\xb0\x01')          # _other is outside the 32-byte window
        self.assertEqual(rec['edits'][0]['where'], ['0x1104 (_answer +0x4)'])
        self.assertEqual([e['ev'] for e in events], ['file.start', 'edit.apply', 'file.end'])
        self.assertEqual(rec['edits'][0]['sites'], [{'at': '0x1104', 'before': 'b0010fbe', 'after': 'b0000fbe'}])

    def test_section_count_and_at(self):
        e = literal(section='__TEXT,__text', count=2)
        del e['base'], e['limit']
        out, rec = engine.apply(spec(e), image())
        self.assertEqual((out[0x1104:0x1106], out[0x1204:0x1206]), (b'\xb0\x00', b'\xb0\x00'))
        e = literal(at='0x1204')
        del e['base'], e['limit']
        out, _ = engine.apply(spec(e), image())
        self.assertEqual((out[0x1104:0x1106], out[0x1204:0x1206]), (b'\xb0\x01', b'\xb0\x00'))

    def test_wrong_count_refused(self):
        e = literal(section='__TEXT,__text')
        del e['base'], e['limit']
        with self.assertRaisesRegex(engine.EditError, 'answer-no: found 2 of 1'):
            engine.apply(spec(e), image())

    def test_missing_bytes_and_already_applied(self):
        with self.assertRaisesRegex(engine.EditError, r'found 0 of 1 near _answer'):
            engine.apply(spec(literal(find='B0 02 0F BE', replace='B0 00 0F BE')), image())
        once, _ = engine.apply(spec(literal()), image())
        with self.assertRaisesRegex(engine.EditError, 'already there'):
            engine.apply(spec(literal()), once)

    def test_masks(self):
        # find mov al,<any>; set only the low bit of the immediate to 0
        e = literal(find='B0 00 0F BE', mask='FF 00 FF FF', replace='00 00 00 00', replace_mask='00 01 00 00')
        out, _ = engine.apply(spec(e), image())
        self.assertEqual(out[0x1104:0x1108], bytes.fromhex('b0000fbe'))

    def test_unknown_symbol(self):
        with self.assertRaisesRegex(engine.EditError, 'symbol _missing is not defined'):
            engine.apply(spec(literal(base='_missing')), image())

    def test_overlap_refused(self):
        second = literal(eid='push-changed', find='55 48 89 E5 B0 00', replace='55 48 89 E5 B0 02')
        with self.assertRaisesRegex(engine.EditError, 'push-changed: touches the same bytes as answer-no'):
            engine.apply(spec(literal(), second), image())

    def test_search_helper(self):
        data = b'xxAByyABzzAB'
        self.assertEqual(engine.search(data, 0, len(data), b'AB', b'\xff\xff'), [2, 6, 10])
        self.assertEqual(engine.search(data, 3, 9, b'AB', b'\xff\xff'), [6])
        self.assertEqual(engine.search(data, 0, len(data), b'\x00B', b'\x00\xff'), [2, 6, 10])


class Matcher(unittest.TestCase):
    def test_resolved_sites_for_known_input(self):
        data = image()
        e = {'id': 'm', 'kind': 'matcher', 'reason': 'x',
             'resolved': {engine.sha256(data): [{'at': '0x1205', 'before': '01', 'after': '00'}]}}
        out, rec = engine.apply(spec(e), data)
        self.assertEqual(out[0x1205], 0)
        self.assertEqual(rec['edits'][0]['resolved_by'], 'resolved')

    def test_unknown_input_without_function_refused(self):
        e = {'id': 'm', 'kind': 'matcher', 'reason': 'x', 'resolved': {'0' * 64: [{'at': 0, 'before': '00',
                                                                                    'after': '01'}]}}
        with self.assertRaisesRegex(engine.EditError, 'no resolved sites for this input'):
            engine.apply(spec(e), image())

    def test_function_runs_for_new_input_and_checks_bytes(self):
        d = Path(tempfile.mkdtemp())
        (d / 'find.py').write_text('def sites(data):\n    at = data.index(bytes.fromhex("b0010fbe"))\n'
                                   '    return [(at + 1, b"\\x01", b"\\x00")]\n'
                                   'def wrong(data):\n    return [(0x1105, b"\\x07", b"\\x00")]\n')
        e = {'id': 'm', 'kind': 'matcher', 'reason': 'x', 'fn': 'find.py:sites'}
        out, rec = engine.apply(spec(e), image(), repo=str(d))
        self.assertEqual((out[0x1105], rec['edits'][0]['resolved_by']), (0, 'matcher'))
        e['fn'] = 'find.py:wrong'
        with self.assertRaisesRegex(engine.EditError, r'bytes at 0x1105 are 01, expected 07'):
            engine.apply(spec(e), image(), repo=str(d))


class AddDylib(unittest.TestCase):
    OP = {'id': 'load-extra', 'kind': 'op', 'op': 'macho.add-dylib', 'args': {'path': '/usr/lib/libextra.dylib'},
          'reason': 'x'}

    def test_adds_one_command_and_keeps_order(self):
        out, rec = engine.apply(spec(dict(self.OP, args={'path': '/usr/lib/libextra.dylib', 'weak': True})), image())
        names = macho.Image(out).dylib_names()
        self.assertEqual(names[-1], (macho.LC_LOAD_WEAK_DYLIB, '/usr/lib/libextra.dylib'))
        self.assertEqual(rec['edits'][0]['command'], 'LC_LOAD_WEAK_DYLIB')

    def test_refusals(self):
        with self.assertRaisesRegex(engine.EditError, 'already referenced'):
            engine.apply(spec(dict(self.OP, args={'path': '/usr/lib/libfixture.dylib'})), image())
        with self.assertRaisesRegex(engine.EditError, 'not zero'):
            engine.apply(spec(self.OP), image(padding_dirty=True))
        with self.assertRaisesRegex(engine.EditError, 'padding too small'):
            engine.apply(spec(self.OP), image(text_at=0x110, text_size=0x1000,
                                              symbols={'_answer': 0x150}, code={0x150: CODE}))

    def test_universal_changes_only_the_x86_slice(self):
        thin = image()
        fat, at = fixture.universal(thin)
        out, _ = engine.apply(spec(literal(), self.OP), fat)
        self.assertEqual(out[:at], fat[:at])
        thin_out, _ = engine.apply(spec(literal(), self.OP), thin)
        self.assertEqual(out[at:], thin_out)

    def test_two_dylibs_do_not_conflict(self):
        second = dict(self.OP, id='load-more', args={'path': '/usr/lib/libmore.dylib'})
        out, _ = engine.apply(spec(self.OP, second), image())
        self.assertEqual([n for _, n in macho.Image(out).dylib_names()][-2:],
                         ['/usr/lib/libextra.dylib', '/usr/lib/libmore.dylib'])


class Plist(unittest.TestCase):
    def ps(self, *edits):
        return {'format': 1, 'path': 'EFI/OC/config.plist', 'origin': {'kind': 'hao'}, 'edits': list(edits)}

    def op(self, eid, key, value):
        return {'id': eid, 'kind': 'op', 'op': 'plist.set', 'args': {'key': key, 'value': value}, 'reason': 'x'}

    def test_set_keeps_format(self):
        root = {'NVRAM': {'boot-args': '-v'}, 'Kernel': {'Patch': [{'Enabled': False}]}}
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            out, rec = engine.apply(self.ps(self.op('args', ['NVRAM', 'boot-args'], '-v keepsyms=1'),
                                            self.op('patch-on', ['Kernel', 'Patch', 0, 'Enabled'], True)),
                                    plistlib.dumps(root, fmt=fmt))
            self.assertEqual(out.startswith(b'bplist00'), fmt == plistlib.FMT_BINARY)
            got = plistlib.loads(out)
            self.assertEqual((got['NVRAM']['boot-args'], got['Kernel']['Patch'][0]['Enabled']), ('-v keepsyms=1', True))
            self.assertEqual(rec['edits'][0]['old'], '-v')

    def test_same_key_twice_refused(self):
        data = plistlib.dumps({'NVRAM': {'boot-args': ''}})
        with self.assertRaisesRegex(engine.EditError, 'b: touches the same key as a'):
            engine.apply(self.ps(self.op('a', ['NVRAM'], {}), self.op('b', ['NVRAM', 'boot-args'], 'x')), data)


class Results(unittest.TestCase):
    def test_external_op_refused(self):
        e = {'id': 'rw', 'kind': 'op', 'op': 'metallib.rewrite', 'args': {'profile': 'x'}, 'reason': 'x'}
        with self.assertRaisesRegex(engine.EditError, 'applied by cache/metallib'):
            engine.apply(spec(e), image())

    def test_expect_is_a_note_not_a_stop(self):
        data = image()
        good, _ = engine.apply(spec(literal()), data)
        _, rec = engine.apply(spec(literal(), expect={engine.sha256(data): engine.sha256(good)}), data)
        self.assertEqual(rec['expected'], 'match')
        _, rec = engine.apply(spec(literal(), expect={engine.sha256(data): '0' * 64}), data)
        self.assertEqual(rec['expected'], 'differs')
        _, rec = engine.apply(spec(literal()), data)
        self.assertEqual(rec['expected'], 'unknown-input')

    def test_result_must_still_parse(self):
        e = {'id': 'break-header', 'kind': 'literal', 'at': '0x10', 'find': '03 00 00 00', 'replace': 'FF 00 00 00',
             'reason': 'x'}
        with self.assertRaisesRegex(engine.EditError, 'not a valid x86_64 Mach-O'):
            engine.apply(spec(e), image())

    def test_input_is_not_modified(self):
        data = bytearray(image())
        before = bytes(data)
        engine.apply(spec(literal()), data)
        self.assertEqual(bytes(data), before)


class Store(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.store = ST.Store(self.d / 'store')

    def run_stored(self, s, data, repo=None):
        events = []
        out, rec = engine.apply_stored(s, data, self.store, repo=repo or str(self.d), on_event=events.append)
        return out, rec, [e['ev'] for e in events]

    def test_second_apply_reuses_the_stored_result(self):
        out1, rec1, ev1 = self.run_stored(spec(literal()), image())
        out2, rec2, ev2 = self.run_stored(spec(literal()), image())
        self.assertEqual(out1, out2)
        self.assertEqual((rec1['reused'], rec2['reused']), (False, True))
        self.assertIn('edit.apply', ev1)
        self.assertEqual(ev2, ['file.start', 'file.reused'])

    def test_side_by_side_view(self):
        data = image()
        out, rec, _ = self.run_stored(spec(literal()), data)
        view = self.d / 'store/view/usr/lib/libfixture.dylib'
        self.assertEqual(((view / 'original').read_bytes(), (view / 'patched').read_bytes()), (data, out))
        self.assertEqual(json.loads((view / 'record.json').read_text())['output_sha256'], rec['output_sha256'])
        self.assertIn('why    answer NO', (view / 'explain.txt').read_text())
        self.assertEqual(ST.changed_ranges(data, out), [(0x1105, 0x1106)])

    def test_damaged_or_changed_inputs_apply_again(self):
        out, rec, _ = self.run_stored(spec(literal()), image())
        (self.d / 'store/objects' / rec['output_sha256']).write_bytes(b'damaged')
        _, rec, ev = self.run_stored(spec(literal()), image())
        self.assertFalse(rec['reused'])
        self.assertIn('edit.apply', ev)
        _, rec, _ = self.run_stored(spec(literal(reason='a new reason')), image())   # spec changed: new key
        self.assertFalse(rec['reused'])
        other = bytearray(image()); other[0x2fff] ^= 0xff                                # input changed: new key
        _, rec, _ = self.run_stored(spec(literal()), bytes(other))
        self.assertFalse(rec['reused'])

    def test_matcher_source_is_part_of_the_key(self):
        (self.d / 'm.py').write_text('def f(data):\n    return [(0x1105, b"\\x01", b"\\x00")]\n')
        s = spec({'id': 'm', 'kind': 'matcher', 'fn': 'm.py:f', 'reason': 'x'})
        k1 = ST.key(s, 'a' * 64, str(self.d))
        (self.d / 'm.py').write_text('def f(data):\n    return [(0x1105, b"\\x01", b"\\x02")]\n')
        self.assertNotEqual(k1, ST.key(s, 'a' * 64, str(self.d)))

    def test_changed_ranges(self):
        self.assertEqual(ST.changed_ranges(b'abcdef', b'abXdeY'), [(2, 3), (5, 6)])
        self.assertEqual(ST.changed_ranges(b'abc', b'abcde'), [(3, 5)])
        self.assertEqual(ST.changed_ranges(b'a' * 9000, b'a' * 4095 + b'bb' + b'a' * 4903, block=4096), [(4095, 4097)])


class CommandLine(unittest.TestCase):
    TOOL = HERE.parent / 'edit-engine'

    def run_tool(self, *args):
        return subprocess.run([sys.executable, '-I', str(self.TOOL), *map(str, args)], capture_output=True, text=True)

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / 'in').write_bytes(image())
        (self.d / 'spec.json').write_text(json.dumps(spec(literal(asm={'before': 'mov al,1', 'after': 'mov al,0'}))))

    def test_levels(self):
        r = self.run_tool('apply', self.d / 'spec.json', self.d / 'in', self.d / 'out', '--record', self.d / 'rec.json')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('edit 1/1  answer-no', r.stdout)
        self.assertEqual(json.loads((self.d / 'rec.json').read_text())['edits'][0]['found'], 1)
        r = self.run_tool('apply', '--show-edits', self.d / 'spec.json', self.d / 'in', self.d / 'out')
        self.assertIn('asm    mov al,1  ->  mov al,0', r.stdout)
        self.assertIn('why    answer NO', r.stdout)
        r = self.run_tool('apply', '--quiet', self.d / 'spec.json', self.d / 'in', self.d / 'out')
        self.assertEqual(r.stdout, '')
        r = self.run_tool('apply', '--log', 'json', self.d / 'spec.json', self.d / 'in', self.d / 'out')
        self.assertEqual([json.loads(l)['ev'] for l in r.stdout.splitlines()], ['file.start', 'edit.apply', 'file.end'])

    def test_refusal_writes_nothing(self):
        (self.d / 'twice').write_bytes(engine.apply(spec(literal()), image())[0])
        r = self.run_tool('apply', self.d / 'spec.json', self.d / 'twice', self.d / 'never')
        self.assertEqual(r.returncode, 1)
        self.assertIn('refused: answer-no: found 0 of 1', r.stderr)
        self.assertFalse((self.d / 'never').exists())

    def test_store_and_inspect(self):
        store = self.d / 'store'
        r = self.run_tool('apply', '--store', store, self.d / 'spec.json', self.d / 'in', self.d / 'out')
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_tool('apply', '--store', store, self.d / 'spec.json', self.d / 'in', self.d / 'out2')
        self.assertIn('reused', r.stdout)
        self.assertEqual((self.d / 'out').read_bytes(), (self.d / 'out2').read_bytes())
        r = self.run_tool('inspect', store, 'usr/lib/libfixture.dylib')
        self.assertIn('changed   1 range, 1 bytes', r.stdout)
        self.assertIn('0x00001105', r.stdout)
        self.assertEqual(self.run_tool('inspect', store, 'usr/lib/none').returncode, 1)

    def test_check_and_explain(self):
        (self.d / 'bad.json').write_text(json.dumps(spec(literal(reason=''))))
        r = self.run_tool('check', self.d / 'spec.json', self.d / 'bad.json')
        self.assertEqual(r.returncode, 1)
        self.assertIn('bad.json: answer-no: reason is required', r.stdout)
        r = self.run_tool('explain', self.d / 'spec.json')
        self.assertIn('1. answer-no  literal near _answer', r.stdout)
        self.assertIn('bytes  B0 01 0F BE  ->  B0 00 0F BE', r.stdout)


if __name__ == '__main__':
    unittest.main()
