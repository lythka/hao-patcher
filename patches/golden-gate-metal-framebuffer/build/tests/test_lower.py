import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1]/'lib/lower.py'
spec = importlib.util.spec_from_file_location('framebuffer_lower', path)
lower = importlib.util.module_from_spec(spec); spec.loader.exec_module(lower)

IR = '''target triple = "air64_v25-apple-macosx13.0.0"
define <4 x float> @arbitrary_name(<4 x float> %0) local_unnamed_addr #0 {
  %2 = fadd <4 x float> %0, <float 0.25, float 0.25, float 0.25, float 0.25>
  ret <4 x float> %2
}
attributes #0 = { nounwind readnone }
!air.fragment = !{!0}
!0 = !{<4 x float> (<4 x float>)* @arbitrary_name, !1, !3}
!1 = !{!2}
!2 = !{!"air.render_target", i32 2, i32 0, !"air.arg_type_name", !"float4"}
!3 = !{!4}
!4 = !{i32 0, !"air.render_target", i32 2, !"air.arg_type_name", !"float4", !"air.arg_name", !"old"}
'''


class Lower(unittest.TestCase):
    def test_preserves_arithmetic_and_output_attachment(self):
        text, plans = lower.lower(IR)
        self.assertIn(IR.split(' {\n', 1)[1].split('\n}', 1)[0], text)
        self.assertIn('!2 = !{!"air.render_target", i32 2, i32 0, !"air.arg_type_name", !"float4"}', text)
        self.assertEqual(plans[0]['bindings'][0]['attachment'], 2)
        self.assertIn('alwaysinline', text)
        self.assertNotIn('fptoui', text)
        self.assertIn('@air.convert.u.v2i32.f.v2f32', text)

    def test_names_are_not_recognition(self):
        for name in ['x', 'another_export', '"quoted name"']:
            _, plans = lower.lower(IR.replace('arbitrary_name', name))
            self.assertEqual(plans[0]['function'], '@'+name)

    def test_ordinary_fragment_unchanged(self):
        source = IR.replace('i32 0, !"air.render_target", i32 2', 'i32 0, !"air.position", !"air.center"')
        self.assertEqual(lower.lower(source), (source, []))
        self.assertEqual(lower.lower('; no fragment\n'), ('; no fragment\n', []))

    def test_multisample_adds_sample_coordinate(self):
        text, plans = lower.lower(IR, multisample=True)
        self.assertTrue(plans[0]['multisample'])
        self.assertIn('!"air.sample_id"', text)
        self.assertIn('@air.read_texture_2d_ms.v4f32', text)

    def test_requires_specialization(self):
        for change in [IR.replace('i32 0, !"air.render_target"', 'i32 0, !"air.function_constant", !7, !"air.render_target"'),
                       IR.replace('i32 0, !"air.render_target", i32 2', 'i32 0, !"air.render_target", i32 addrspace(2)* @dynamic')]:
            with self.assertRaisesRegex(ValueError, 'Specialize'):
                lower.lower(change)

    def test_half2_swizzle(self):
        text, plans = lower.lower(IR.replace('<4 x float>', '<2 x half>').replace('float4', 'half2'))
        self.assertIn('@air.read_texture_2d.v4f16', text)
        self.assertIn('<2 x i32> <i32 0, i32 1>', text)
        self.assertEqual(plans[0]['bindings'][0]['type'], 'half2')

    def test_texture_slot_does_not_collide(self):
        source = IR.replace('(<4 x float> %0)', '(<4 x float> %0, %struct._texture_2d_t addrspace(1)* %t)')
        source = source.replace('(<4 x float>)*', '(<4 x float>, %struct._texture_2d_t addrspace(1)*)*')
        source = source.replace('!3 = !{!4}', '!3 = !{!4, !5}')
        source += '!5 = !{i32 1, !"air.texture", !"air.location_index", i32 120, i32 8, !"air.read", !"air.arg_type_name", !"texture2d<float, read>", !"air.arg_name", !"user"}\n'
        _, plans = lower.lower(source)
        self.assertEqual(plans[0]['bindings'][0]['texture_index'], 119)

    def test_nested_type_lists(self):
        self.assertEqual(lower.split('<{ <4 x float>, i32 }>, %"type, spaced" addrspace(2)*'),
                         ['<{ <4 x float>, i32 }>', '%"type, spaced" addrspace(2)*'])
        for bad in ['<i32', '(i32]', '"unclosed']:
            with self.assertRaises(ValueError): lower.split(bad)

    def test_distinct_alias_nodes_keep_their_ids(self):
        source = IR + '!5 = distinct !{!5, !"alias scope"}\n'
        text, _ = lower.lower(source)
        self.assertIn('!5 = distinct !{!5, !"alias scope"}', text)
        self.assertEqual(text.count('!5 ='), 1)


if __name__ == '__main__': unittest.main()


VERSIONS = '!air.version = !{!7}\n!air.language_version = !{!8}\n!7 = !{i32 2, i32 8, i32 0}\n!8 = !{!"Metal", i32 3, i32 2, i32 0}\n'


class AirVersion(unittest.TestCase):
    def test_reads_versions_with_and_without_language_name(self):
        self.assertEqual(lower.air_version(IR + VERSIONS, 'air.version'), (2, 8))
        self.assertEqual(lower.air_version(IR + VERSIONS, 'air.language_version'), (3, 2))

    def test_reference_must_start_a_line(self):
        text = IR + '!5 = !{!"x !air.version = !{!9}"}\n' + VERSIONS
        self.assertEqual(lower.air_version(text, 'air.version'), (2, 8))

    def test_missing_and_malformed_fail(self):
        with self.assertRaisesRegex(ValueError, 'Missing AIR version'):
            lower.air_version(IR, 'air.version')
        with self.assertRaisesRegex(ValueError, 'Invalid AIR version'):
            lower.air_version(IR + VERSIONS.replace('i32 2, i32 8', 'i64 2, i32 8'), 'air.version')

    def test_linear_on_large_modules(self):
        # The AIR version lookup must stay linear: RenderBox icon modules are several megabytes.
        import time
        body = ''.join(f'!{n} = !{{!"air.arg_name", !"v{n}", i32 {n}, i32 {n}}}\n' for n in range(10, 400000))
        lower.air_version(IR + VERSIONS, 'air.version')   # builds the native CLI outside the timing
        start = time.monotonic()
        self.assertEqual(lower.air_version(IR + body + VERSIONS, 'air.language_version'), (3, 2))
        self.assertLess(time.monotonic() - start, 5)
