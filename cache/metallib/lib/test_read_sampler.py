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


def offsetless(kind, width='i32'):
    """SceneKit's texturecube_copy_kernel / adaptative_lum shapes: cube (coord, face, lod, access) and 1D (coord,
    lod, access) sampler-form reads, which have no offset operand."""
    name = 'air.read_texture_' + kind + '.' + ('i16.' if width == 'i16' else '') + 'v4f32'
    texture = '%struct._texture_' + kind + '_t addrspace(1)*'
    sampler = '%struct._sampler_t addrspace(2)*'
    rest = ([('<2 x ' + width + '>', '%coord'), (width, '%face')] if kind == 'cube' else [(width, '%coord')]) + [
        (width, '0'), ('i32', '1')]
    types = [texture, sampler] + [t for t, _ in rest]
    values = ['%texture', '%sampler'] + [v for _, v in rest]
    args = ', '.join(t + ' ' + v for t, v in zip(types, values))
    params = ', '.join([texture + ' %texture'] + [t + ' ' + v for t, v in rest if v.startswith('%')])
    return f'''define void @example({params}) {{
  %sampler = call {sampler} @air.get_read_sampler()
  %value = call {{<4 x float>, i8}} @{name}({args})
  ret void
}}
declare {sampler} @air.get_read_sampler()
declare {{<4 x float>, i8}} @{name}({', '.join(types)})
''', name


class ReadSamplerTests(unittest.TestCase):
    def test_cube_and_1d_drop_only_the_sampler(self):
        for kind, width in (('cube', 'i32'), ('cube', 'i16'), ('1d', 'i32')):
            with self.subTest(kind=kind, width=width):
                text, name = offsetless(kind, width)
                out, report = read_sampler.rewrite(text)
                self.assertEqual(report, dict(calls=1, overloads=[name]))
                self.assertNotIn('_sampler_t addrspace(2)* %sampler', out)
                self.assertNotIn('addrspace(2)*, ', out.split('declare {<4 x float>')[1])
                tail = '%face, ' + width + ' 0, i32 1)' if kind == 'cube' else '%coord, ' + width + ' 0, i32 1)'
                self.assertIn('(%struct._texture_' + kind + '_t addrspace(1)* %texture, ', out)
                self.assertIn(tail, out)
                self.assertEqual(read_sampler.scan(out), [])

    def test_cube_signature_must_match(self):
        text, _ = offsetless('cube')
        bad = text.replace('<2 x i32>, i32, i32, i32)', '<2 x i32>, <2 x i32>, i32, i32, i32)')
        with self.assertRaisesRegex(ValueError, 'unrecognized sampler-form read signature'):
            read_sampler.rewrite(bad)

    def test_2d_array_and_3d_untouched(self):
        decl = ('declare {<4 x float>, i8} @air.read_texture_3d.v4f32(%struct._texture_3d_t addrspace(1)*, '
                '%struct._sampler_t addrspace(2)*, <3 x i32>, <3 x i32>, i32, i32)\n')
        self.assertEqual(read_sampler.scan(decl), [])

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
