import unittest
import mkfix_half_vec_int as fix
import compatibility


def program(op='and', n=3):
    tail = ('icmp eq' if op == 'icmp' else op)
    return ('define void @probe(<' + str(n) + ' x half> %h) {\n'
            '  %i = bitcast <' + str(n) + ' x half> %h to <' + str(n) + ' x i16>\n'
            '  %v = ' + tail + ' <' + str(n) + ' x i16> %i, zeroinitializer\n'
            '  ret void\n}\n')


class RefusalTests(unittest.TestCase):
    def test_original_bad_operations_refused(self):
        for n in (3, 4):
            for op in fix.OPS.split('|') + ['icmp']:
                with self.subTest(n=n, op=op), self.assertRaises(AssertionError):
                    fix.refuse_bad_state(program(op, n))

    def test_scalarized_operations_are_accepted_and_idempotent(self):
        for n in (3, 4):
            for op in fix.OPS.split('|') + ['icmp']:
                with self.subTest(n=n, op=op):
                    text, report = fix.rewrite(program(op, n))
                    self.assertEqual(report['ops'], 1)
                    fix.refuse_bad_state(text)
                    self.assertEqual(fix.rewrite(text)[0], text)

    def test_preserves_noncrashing_shift_and_half2(self):
        for text in (program('lshr'), program('and', 2)):
            self.assertEqual(fix.rewrite(text)[0], text)

    def test_generated_name_collision_refuses(self):
        text = program().replace('  %i =', '  %mlfx.i.h0 = add i32 1, 2\n  %i =')
        with self.assertRaisesRegex(ValueError, 'duplicate SSA'):
            compatibility.rewrite(text)

    def test_guard_sees_second_function(self):
        with self.assertRaises(AssertionError):
            fix.refuse_bad_state('define void @empty() {\n  ret void\n}\n' + program())

    def test_guard_does_not_ignore_unparsed_operand_form(self):
        with self.assertRaises(AssertionError):
            fix.refuse_bad_state(program().replace('zeroinitializer', '<i16 1, i16 2, i16 3>, !range !1'))


if __name__ == '__main__':
    unittest.main()
