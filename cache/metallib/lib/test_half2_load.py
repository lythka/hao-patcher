import random
import re
import unittest
import half2_load
import compatibility


def module(extra='', stores=None, count=4):
    if stores is None:
        stores = ['<half 0xHBC00, half 0xHBC00>', '<half 0xH3C00, half 0xHBC00>',
                  '<half 0xH3C00, half 0xH3C00>', '<half 0xHBC00, half 0xH3C00>'][:count]
    init = []
    for i, value in enumerate(stores):
        init += [f'  %p{i} = getelementptr inbounds [{count} x <2 x half>], [{count} x <2 x half>]* %a, i64 0, i64 {i}',
                 f'  store <2 x half> {value}, <2 x half>* %p{i}, align 4']
    return '\n'.join(['define <2 x half> @example(i64 %index) {',
                      f'  %a = alloca [{count} x <2 x half>], align 4', *init, extra,
                      f'  %q = getelementptr inbounds [{count} x <2 x half>], [{count} x <2 x half>]* %a, i64 0, i64 %index',
                      '  %v = load <2 x half>, <2 x half>* %q, align 4',
                      '  ret <2 x half> %v', '}', ''])


class Half2Tests(unittest.TestCase):
    def test_table_all_indices_and_half_bits(self):
        rng = random.Random(921)
        for count in [1, 2, 4, 8]:
            values = [f'<half 0xH{rng.randrange(65536):04X}, half 0xH{rng.randrange(65536):04X}>' for _ in range(count)]
            # Include signed zero and NaN payloads: selection never converts them.
            values[0] = '<half 0xH8000, half 0xH7E01>'
            original = module(stores=values, count=count)
            fixed, report = half2_load.rewrite(original)
            self.assertEqual(report['loads'], 1)
            self.assertEqual(half2_load.scan(fixed), [])
            for index in range(count):
                result = {}
                for line in fixed.splitlines():
                    m = re.match(r'  (%\S+) = icmp eq i64 %index, (\d+)$', line)
                    if m:
                        result[m[1]] = index == int(m[2])
                    m = re.match(r'  (%\S+) = select i1 (%\S+|true), <2 x half> (.*?), <2 x half> (.*)$', line)
                    if m:
                        flag = True if m[2] == 'true' else result[m[2]]
                        arm = m[3] if flag else m[4]
                        result[m[1]] = result.get(arm, arm)
                self.assertEqual(result['%v'], values[index])
            self.assertEqual(half2_load.rewrite(fixed)[0], fixed)

    def test_refuses_incomplete_initialization(self):
        text = module(stores=['zeroinitializer'])
        self.assertEqual(half2_load.rewrite(text)[0], text)
        with self.assertRaises(ValueError):
            compatibility.refuse_bad_state(text)

    def test_refuses_escape(self):
        text = module('  call void @escape([4 x <2 x half>]* %a)')
        self.assertEqual(half2_load.rewrite(text)[0], text)

    def test_refuses_alias_write(self):
        text = module('  %alias = getelementptr inbounds [4 x <2 x half>], [4 x <2 x half>]* %a, i64 0, i64 0\n  store <2 x half> zeroinitializer, <2 x half>* %alias, align 4')
        self.assertEqual(half2_load.rewrite(text)[0], text)

    def test_refuses_conditional_initialization(self):
        text = module().replace('  %p0 =', '  br label %init\ninit:\n  %p0 =')
        self.assertEqual(half2_load.rewrite(text)[0], text)

    def test_refuses_later_mutation(self):
        text = module().replace('  ret ', '  store <2 x half> zeroinitializer, <2 x half>* %p0, align 4\n  ret ')
        self.assertEqual(half2_load.rewrite(text)[0], text)

    def test_refuses_derived_alias(self):
        text = module('  %alias = getelementptr inbounds [4 x <2 x half>], [4 x <2 x half>]* %a, i64 0, i64 0\n  %more = getelementptr inbounds [4 x <2 x half>], [4 x <2 x half>]* %alias, i64 0, i64 0')
        self.assertEqual(half2_load.rewrite(text)[0], text)

    def test_unrelated_half2_load_unchanged(self):
        text = 'define <2 x half> @f(<2 x half> addrspace(1)* %p) {\n  %v = load <2 x half>, <2 x half> addrspace(1)* %p, align 4\n  ret <2 x half> %v\n}\n'
        self.assertEqual(compatibility.rewrite(text)[0], text)


if __name__ == '__main__':
    unittest.main()
