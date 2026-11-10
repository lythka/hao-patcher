import unittest
import texture_coords as fix


def module(coord='%uv', extra=''):
    return '''define void @example(<2 x i16> %uv) {
EXTRA
  %r = call {<4 x float>, i8} @air.read_texture_2d.i16.v4f32(<2 x i16> COORD, i16 0)
  ret void
}
declare {<4 x float>, i8} @air.read_texture_2d.i16.v4f32(<2 x i16>, i16)
declare {<4 x float>, i8} @air.read_texture_2d.v4f32(<2 x i32>, i32)
'''.replace('COORD', coord).replace('EXTRA', extra)


class TextureCoordinateTests(unittest.TestCase):
    def test_widen_and_idempotence(self):
        out, report = fix.rewrite(module())
        self.assertEqual(report['calls'], 1)
        self.assertIn('zext <2 x i16> %uv to <2 x i32>', out)
        self.assertNotIn('.i16.', out)
        self.assertEqual(fix.rewrite(out)[0], out)

    def test_vector_constants_and_high_bit(self):
        out, _ = fix.rewrite(module('<i16 32768, i16 65535>'))
        self.assertIn('<i32 32768, i32 65535>', out)

    def test_attributes(self):
        out, _ = fix.rewrite(module('noundef %uv'))
        self.assertIn('<2 x i32> noundef %tcw', out)

    def test_collision(self):
        out, _ = fix.rewrite(module(extra='  %tcw1.r = add i32 1, 2'))
        self.assertIn('%tcw2.r = zext', out)

    def test_negative_and_overflow_refuse(self):
        for value in ['<i16 -1, i16 0>', '<i16 65536, i16 0>', 'signext %uv']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                fix.rewrite(module(value))

    def test_arity_mismatch_refuses(self):
        with self.assertRaises(ValueError):
            fix.rewrite(module().replace('(<2 x i32>, i32)', '(<2 x i32>)'))

    def test_unknown_use_refuses(self):
        with self.assertRaises(ValueError):
            fix.rewrite(module() + '@alias = alias @air.read_texture_2d.i16.v4f32\n')

    def test_musttail_refuses(self):
        with self.assertRaises(ValueError):
            fix.rewrite(module().replace(' = call ', ' = musttail call '))

    def test_common_guard_refuses_observed_mixed_read(self):
        import compatibility
        with self.assertRaisesRegex(ValueError, 'mixed_texture_reads'):
            compatibility.refuse_bad_state(module())
        out, _ = compatibility.rewrite(module())
        self.assertEqual(compatibility.refuse_bad_state(out)['mixed_texture_reads'], [])

    def test_only_narrow_unchanged(self):
        text = module().replace('declare {<4 x float>, i8} @air.read_texture_2d.v4f32(<2 x i32>, i32)\n', '')
        self.assertEqual(fix.rewrite(text)[0], text)


if __name__ == '__main__':
    unittest.main()
