import unittest
import compatibility
import sampler_reads
import test_read_sampler


def mixed_module():
    """One function calling both coordinate widths of the sampler-form float read (the invalid-IR case)."""
    texture = '%struct._texture_2d_t addrspace(1)*'
    sampler = '%struct._sampler_t addrspace(2)*'
    return f'''define void @example({texture} %texture, <2 x i32> %wide, <2 x i16> %narrow) {{
  %sampler = call {sampler} @air.get_read_sampler()
  %a = call {{<4 x float>, i8}} @air.read_texture_2d.i16.v4f32({texture} %texture, {sampler} %sampler, <2 x i16> %narrow, <2 x i16> zeroinitializer, i16 0, i32 1)
  %b = call {{<4 x float>, i8}} @air.read_texture_2d.v4f32({texture} %texture, {sampler} %sampler, <2 x i32> %wide, <2 x i32> zeroinitializer, i32 0, i32 1)
  ret void
}}
declare {sampler} @air.get_read_sampler()
declare {{<4 x float>, i8}} @air.read_texture_2d.i16.v4f32({texture}, {sampler}, <2 x i16>, <2 x i16>, i16, i32)
declare {{<4 x float>, i8}} @air.read_texture_2d.v4f32({texture}, {sampler}, <2 x i32>, <2 x i32>, i32, i32)
'''


class SamplerReadsTests(unittest.TestCase):
    def test_depth_sampler_read_becomes_direct(self):
        for width in ('i16', 'i32'):
            with self.subTest(width=width):
                out, report = sampler_reads.rewrite(test_read_sampler.module(width, depth=True))
                self.assertEqual(report['sampler_form_reads']['calls'], 1)
                self.assertEqual(sampler_reads.scan(out), dict(sampler_form_reads=[], mixed_texture_reads=[]))
                self.assertEqual(sampler_reads.refuse_bad_state(out), sampler_reads.scan(out))

    def test_mixed_widths_are_widened_after_sampler_removal(self):
        out, report = sampler_reads.rewrite(mixed_module())
        self.assertEqual(report['sampler_form_reads']['calls'], 2)
        self.assertEqual(report['mixed_texture_reads']['calls'], 1)
        self.assertNotIn('@air.read_texture_2d.i16.v4f32(', out.split('declare')[0])
        self.assertEqual(sampler_reads.scan(out), dict(sampler_form_reads=[], mixed_texture_reads=[]))
        self.assertEqual(sampler_reads.rewrite(out)[0], out)

    def test_untouched_module_is_unchanged(self):
        text = 'define void @plain(i32 %x) {\n  %y = add i32 %x, 1\n  ret void\n}\n'
        self.assertEqual(sampler_reads.rewrite(text), (text, dict(
            sampler_form_reads=dict(calls=0, overloads=[]), mixed_texture_reads=dict(calls=0, overloads=[]))))

    def test_refuses_remaining_sampler_form_and_duplicate_ssa(self):
        with self.assertRaises(ValueError):
            sampler_reads.refuse_bad_state(mixed_module())
        with self.assertRaises(ValueError):
            sampler_reads.refuse_bad_state('define void @f(i32 %x) {\n  %x = add i32 1, 1\n  ret void\n}\n')
        with self.assertRaises(ValueError):
            compatibility.refuse_bad_state('define void @f(i32 %x) {\n  %x = add i32 1, 1\n  ret void\n}\n')

    def test_does_not_run_the_other_passes(self):
        # A device-pointer null compare is the whole suite's business, not this repair's.
        text = ('define void @f(float addrspace(1)* %p) {\n  %n = icmp eq float addrspace(1)* %p, null\n'
                '  ret void\n}\n')
        self.assertEqual(sampler_reads.rewrite(text)[0], text)
        self.assertTrue(compatibility.scan(text)['null_compare'])


if __name__ == '__main__':
    unittest.main()
