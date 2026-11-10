import unittest
import read_sampler


def module(width='i32', depth=False):
    op = 'depth' if depth else 'texture'
    result = 'float' if depth else '<4 x float>'
    name = 'air.read_' + op + '_2d.' + ('i16.' if width == 'i16' else '') + ('f32' if depth else 'v4f32')
    texture = '%struct._' + op + '_2d_t addrspace(1)*'
    sampler = '%struct._sampler_t addrspace(2)*'
    coord = '<2 x ' + width + '>'
    types = [texture, sampler] + (['i32'] if depth else []) + [coord, coord, width, 'i32']
    values = ['%texture', '%sampler'] + (['1'] if depth else []) + ['%coord', 'zeroinitializer', '0', '1']
    args = ', '.join(t + ' ' + v for t, v in zip(types, values))
    return f'''define void @example({texture} %texture, {coord} %coord) {{
  %sampler = call {sampler} @air.get_read_sampler()
  %value = call {{{result}, i8}} @{name}({args})
  ret void
}}
declare {sampler} @air.get_read_sampler()
declare {{{result}, i8}} @{name}({', '.join(types)})
'''


class ReadSamplerTests(unittest.TestCase):
    def test_both_widths_and_depth(self):
        for width in ('i16', 'i32'):
            for depth in (False, True):
                with self.subTest(width=width, depth=depth):
                    out, report = read_sampler.rewrite(module(width, depth))
                    self.assertEqual(report['calls'], 1)
                    self.assertNotIn(' zeroinitializer', out)
                    self.assertEqual(read_sampler.scan(out), [])
                    self.assertEqual(read_sampler.rewrite(out)[0], out)

    def test_sampler_names_are_function_local(self):
        first = module()
        second = first[:first.index('declare ')].replace('@example', '@other').replace(
            'call %struct._sampler_t addrspace(2)* @air.get_read_sampler()',
            'inttoptr i64 1 to %struct._sampler_t addrspace(2)*')
        with self.assertRaisesRegex(ValueError, 'local builtin'):
            read_sampler.rewrite(first + second)

    def test_local_name_reuse_with_correct_producers(self):
        text = module()
        second = text[:text.index('declare ')].replace('@example', '@other')
        self.assertEqual(read_sampler.rewrite(text + second)[1]['calls'], 2)

    def test_no_sampler_assumption_for_parameter(self):
        text = module().replace('call %struct._sampler_t addrspace(2)* @air.get_read_sampler()',
                                'inttoptr i64 2 to %struct._sampler_t addrspace(2)*')
        with self.assertRaisesRegex(ValueError, 'local builtin'):
            read_sampler.rewrite(text)

    def test_zero_and_nonzero_offsets(self):
        text = module().replace('zeroinitializer', '<i32 0, i32 0>')
        self.assertEqual(read_sampler.rewrite(text)[1]['calls'], 1)
        with self.assertRaisesRegex(ValueError, 'offset is not zero'):
            read_sampler.rewrite(text.replace('<i32 0, i32 0>', '<i32 1, i32 0>'))

    def test_suffix_and_quoted_intrinsic_preserved(self):
        text = module().replace('@air.read_texture_2d.v4f32(', '@"air.read_texture_2d.v4f32"(')
        text = text.replace('%struct._texture_2d_t', '%"struct._texture_2d_t"')
        text = text.replace('i32 0, i32 1)\n', 'i32 0, i32 1) #2, !alias.scope !9 ; keep\n')
        out, _ = read_sampler.rewrite(text)
        self.assertIn('i32 0, i32 1) #2, !alias.scope !9 ; keep', out)

    def test_comments_and_strings_are_not_uses(self):
        text = module()
        text = text[text.index('declare '):]
        text += '; @air.read_texture_2d.v4f32()\n!0 = !{!"@air.read_texture_2d.v4f32()"}\n'
        self.assertEqual(read_sampler.rewrite(text)[0], text)

    def test_common_guard(self):
        import compatibility
        with self.assertRaisesRegex(ValueError, 'sampler_form_reads'):
            compatibility.refuse_bad_state(module())
        out, _ = compatibility.rewrite(module())
        self.assertFalse(compatibility.refuse_bad_state(out)['sampler_form_reads'])


if __name__ == '__main__':
    unittest.main()
