import unittest

import legacy_sync


def program():
    return '''define void @probe(i32 addrspace(1)* %global, i32 addrspace(3)* %local, i32 %value) {
  call void @air.wg.barrier(i32 2, i32 5, i32 1)
  call void @air.simdgroup.barrier(i32 2, i32 5, i32 4)
  %old = call i32 @air.atomic.global.add.u.i32(i32 addrspace(1)* %global, i32 %value, i32 0, i32 2, i32 0, i1 true)
  %v = call i32 @air.atomic.local.load.i32(i32 addrspace(3)* %local, i32 0, i32 1, i32 0, i1 false)
  call void @air.atomic.local.store.i32(i32 addrspace(3)* %local, i32 %old, i32 0, i32 1, i32 0, i1 false)
  ret void
}
declare void @air.wg.barrier(i32, i32, i32)
declare void @air.simdgroup.barrier(i32, i32, i32)
declare i32 @air.atomic.global.add.u.i32(i32 addrspace(1)*, i32, i32, i32, i32, i1)
declare i32 @air.atomic.local.load.i32(i32 addrspace(3)*, i32, i32, i32, i1)
declare void @air.atomic.local.store.i32(i32 addrspace(3)*, i32, i32, i32, i32, i1)
!factory = !{!0}
!0 = !{i32 42}
'''


class LegacySyncTests(unittest.TestCase):
    def test_compare_exchange_preserves_expected_desired_and_both_orders(self):
        for memory, space in [('global', 1), ('local', 3)]:
            for kind in ['weak', 'strong']:
                name = f'air.atomic.{memory}.cmpxchg.{kind}.i32'
                pointer = f'i32 addrspace({space})*'
                source = f'''define i32 @probe({pointer} %object, i32* %expected) {{
  %old = call i32 @{name}({pointer} %object, i32* %expected, i32 9, i32 0, i32 0, i32 2, i32 0, i1 true)
  ret i32 %old
}}
declare i32 @{name}({pointer}, i32*, i32, i32, i32, i32, i32, i1)
'''
                fixed, report = legacy_sync.rewrite(source)
                self.assertEqual(report['calls'], 1)
                self.assertIn('i32* %expected, i32 9, i32 0, i32 0, i32 2, i1 true)', fixed)
                self.assertEqual(legacy_sync.rewrite(fixed)[0], fixed)
                for operands in ['i32 2, i32 0, i32 2, i32 0',
                                 'i32 0, i32 2, i32 2, i32 0',
                                 'i32 0, i32 0, i32 2, i32 1']:
                    with self.subTest(memory=memory, kind=kind, operands=operands):
                        with self.assertRaisesRegex(ValueError, 'unrepresentable'):
                            legacy_sync.rewrite(source.replace('i32 0, i32 0, i32 2, i32 0', operands))

    def test_exchange_drops_flags_and_keeps_order_scope_volatility(self):
        # 27.2 VFX particle_fill_grid: the 26.0 form is (ptr, value, order, scope, volatile).
        source = '''define i32 @probe(i32 addrspace(1)* %object, i32 %value) {
  %old = tail call i32 @air.atomic.global.xchg.i32(i32 addrspace(1)* nocapture %object, i32 %value, i32 0, i32 2, i32 0, i1 false)
  ret i32 %old
}
declare i32 @air.atomic.global.xchg.i32(i32 addrspace(1)* nocapture, i32, i32, i32, i32, i1)
'''
        fixed, report = legacy_sync.rewrite(source)
        self.assertEqual(report['calls'], 1)
        self.assertIn('(i32 addrspace(1)* nocapture %object, i32 %value, i32 0, i32 2, i1 false)', fixed)
        self.assertIn('declare i32 @air.atomic.global.xchg.i32(i32 addrspace(1)* nocapture, i32, i32, i32, i1)', fixed)
        self.assertEqual(legacy_sync.rewrite(fixed)[0], fixed)
        legacy_sync.refuse_bad_state(fixed)
        with self.assertRaisesRegex(ValueError, 'unrepresentable'):
            legacy_sync.rewrite(source.replace('i32 0, i32 2, i32 0, i1 false)', 'i32 0, i32 2, i32 1, i1 false)'))

    def test_keeps_values_flags_scope_volatility_and_metadata(self):
        text, report = legacy_sync.rewrite(program())
        self.assertEqual(report['calls'], 5)
        self.assertIn('@air.wg.barrier(i32 2, i32 1)', text)
        self.assertIn('@air.simdgroup.barrier(i32 2, i32 4)', text)
        self.assertIn('@air.atomic.global.add.u.i32(i32 addrspace(1)* %global, i32 %value, i32 0, i32 2, i1 true)', text)
        self.assertIn('@air.atomic.local.load.i32(i32 addrspace(3)* %local, i32 0, i32 1, i1 false)', text)
        self.assertIn('@air.atomic.local.store.i32(i32 addrspace(3)* %local, i32 %old, i32 0, i32 1, i1 false)', text)
        self.assertTrue(text.endswith('!factory = !{!0}\n!0 = !{i32 42}\n'))
        self.assertEqual(legacy_sync.rewrite(text)[0], text)
        legacy_sync.refuse_bad_state(text)

    def test_refuses_extra_fences_or_unproven_orders(self):
        for old, new in [('i32 2, i32 5, i32 1', 'i32 2, i32 0, i32 1'),
                         ('i32 0, i32 2, i32 0, i1 true', 'i32 2, i32 2, i32 0, i1 true'),
                         ('i32 0, i32 2, i32 0, i1 true', 'i32 0, i32 2, i32 1, i1 true')]:
            with self.subTest(new=new), self.assertRaisesRegex(ValueError, 'unrepresentable'):
                legacy_sync.rewrite(program().replace(old, new))

    def test_escaped_address_and_musttail_are_not_rewritten(self):
        for text in [program() + '@escaped = global void (i32, i32, i32)* @air.wg.barrier\n',
                     program().replace('call void @air.wg.barrier', 'musttail call void @air.wg.barrier')]:
            with self.assertRaises(ValueError):
                legacy_sync.rewrite(text)

    def test_unknown_and_legacy_intrinsics_are_unchanged(self):
        text = 'declare void @air.other(i32, i32, i32)\ndeclare void @air.wg.barrier(i32, i32)\n'
        self.assertEqual(legacy_sync.rewrite(text)[0], text)

    def test_quoted_names_and_call_metadata(self):
        source = program().replace('@air.wg.barrier', '@"air.wg.barrier"')
        source = source.replace('i32 2, i32 5, i32 1)', 'i32 2, i32 5, i32 1), !dbg !0')
        fixed, _ = legacy_sync.rewrite(source)
        self.assertIn('@"air.wg.barrier"(i32 2, i32 1), !dbg !0', fixed)


if __name__ == '__main__':
    unittest.main()
