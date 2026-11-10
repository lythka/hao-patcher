import unittest

import null_compare
import winograd_matrix
import winograd_offsets
import golden_gate


def matrix_program(size=4, kind='float'):
    bits = 32 if kind == 'float' else 16
    vector = f'<2 x {kind}>'
    return f'''define void @probe({vector} addrspace(3)* %shared, <3 x i32> %local, i16 %lane) {{
  %index = zext i16 %lane to i64
  %pointer = getelementptr {vector}, {vector} addrspace(3)* %shared, i64 %index
  %a = load {vector}, {vector} addrspace(3)* %pointer, align 8
  %result = call {vector} @llvm.agx2.f{bits}matmad{size}x{size}.v2f{bits}({vector} %a, {vector} %a, {vector} zeroinitializer)
  ret void
}}
declare {vector} @llvm.agx2.f{bits}matmad{size}x{size}.v2f{bits}({vector}, {vector}, {vector})
!air.kernel = !{{!0}}
!0 = !{{void ({vector} addrspace(3)*, <3 x i32>, i16)* @probe, !1, !2}}
!1 = !{{}}
!2 = !{{!3, !4, !5}}
!3 = !{{i32 0, !"air.threadgroup"}}
!4 = !{{i32 1, !"air.thread_position_in_threadgroup"}}
!5 = !{{i32 2, !"air.thread_index_in_simdgroup"}}
'''


class WinogradTests(unittest.TestCase):
    def test_shared_matrix_keeps_precision_and_is_idempotent(self):
        for size in (4, 8):
            for kind, suffix in [('float', 'f32'), ('half', 'f16')]:
                with self.subTest(size=size, kind=kind):
                    source = matrix_program(size, kind)
                    fixed, report = winograd_matrix.rewrite(source)
                    self.assertEqual(report['calls'], 1)
                    _, calls = null_compare.read_functions(fixed.splitlines())
                    self.assertFalse(any('matmad' in name for name in calls))
                    self.assertEqual(len(calls['air.fma.' + suffix]), size * 2)
                    self.assertNotIn('@air.simd_shuffle', fixed)
                    self.assertEqual(winograd_matrix.rewrite(fixed)[0], fixed)
                    self.assertEqual(source[source.index('!air.kernel'):],
                                     fixed[fixed.index('!air.kernel'):fixed.index('declare ' + kind + ' @air.fma')])

    def test_missing_builtins_and_nonshared_inputs_refuse(self):
        variants = [matrix_program().replace('air.thread_index_in_simdgroup', 'air.other'),
                    matrix_program().replace('addrspace(3)', 'addrspace(1)'),
                    matrix_program().replace('%index = zext i16 %lane to i64',
                                             '%index = call i64 @unknown(i16 %lane)')]
        for source in variants:
            with self.subTest(source=source), self.assertRaises((AssertionError, ValueError)):
                winograd_matrix.rewrite(source)

    def test_builtin_bindings_follow_reordered_internal_arguments(self):
        source = matrix_program().replace('  %index =', '''  call void @helper(i16 %lane, <3 x i32> %local)
  %index =''')
        source += '''define internal void @helper(i16 %x, <3 x i32> %y) {
  ret void
}
'''
        functions, calls = null_compare.read_functions(source.splitlines())
        result = winograd_matrix.kernel_bindings(source, functions, calls)
        self.assertEqual(result['helper'], {'lane': '%x', 'local': '%y'})

    def test_offset_bounds_are_proven_not_assumed(self):
        definitions = {'%a': 'and i16 %unknown, 2', '%b': 'or i16 %a, 1',
                       '%c': 'zext i16 %b to i32'}
        self.assertEqual(winograd_offsets.choices('%c', definitions), {1, 3})
        with self.assertRaisesRegex(ValueError, 'unproven'):
            winograd_offsets.choices('%unknown', definitions)
        with self.assertRaisesRegex(ValueError, 'too many'):
            winograd_offsets.choices('%a', {'%a': 'and i16 %unknown, 7'})

    def test_offset_selection_keeps_residency_and_sampler_arguments(self):
        source = '''define void @probe(i32 %tid, %texture* %t, %sampler* %s, <2 x float> %coord) {
  %x = and i32 %tid, 2
  %y = or i32 %x, 1
  %v = insertelement <2 x i32> undef, i32 %x, i32 0
  %offset = insertelement <2 x i32> %v, i32 %y, i32 1
  %result = call { <4 x float>, i8 } @air.sample_texture_2d.v4f32(%texture* %t, %sampler* %s, <2 x float> %coord, i1 true, <2 x i32> %offset, i1 false, float 0.0, float 0.0, i32 0)
  ret void
}
'''
        fixed, report = winograd_offsets.rewrite(source)
        self.assertEqual(report, {'calls': 1, 'extra_samples': 3})
        _, calls = null_compare.read_functions(fixed.splitlines())
        rows = calls['air.sample_texture_2d.v4f32']
        self.assertEqual(len(rows), 4)
        for _, _, args in rows:
            self.assertEqual(args[:4], ['%texture* %t', '%sampler* %s', '<2 x float> %coord', 'i1 true'])
            self.assertEqual(args[5:], ['i1 false', 'float 0.0', 'float 0.0', 'i32 0'])
        self.assertIn('select i1 %offset0.c3, i8', fixed)
        self.assertEqual(winograd_offsets.rewrite(fixed)[0], fixed)

    def test_private_offset_table_tracks_phi_cycles_and_refuses_escape(self):
        lines = '''  %a = alloca [2 x <2 x i32>], align 8
  %p = getelementptr [2 x <2 x i32>], [2 x <2 x i32>]* %a, i64 0, i64 0
  %q = getelementptr [2 x <2 x i32>], [2 x <2 x i32>]* %a, i64 0, i64 1
  store <2 x i32> zeroinitializer, <2 x i32>* %p, align 8
  store <2 x i32> <i32 -1, i32 1>, <2 x i32>* %q, align 8
  %r = getelementptr [2 x <2 x i32>], [2 x <2 x i32>]* %a, i64 0, i64 %index
  %v = load <2 x i32>, <2 x i32>* %r, align 8'''.splitlines()
        tables = winograd_offsets.private_tables(lines)
        definitions = {'%v': 'load <2 x i32>, <2 x i32>* %r, align 8',
                       '%x': 'phi <2 x i32> [ zeroinitializer, %entry ], [ %y, %loop ]',
                       '%y': 'phi <2 x i32> [ %x, %unchanged ], [ %v, %changed ]'}
        self.assertEqual(winograd_offsets.vector('%x', definitions, tables), [{-1, 0}, {0, 1}])
        for bad in ['  call void @escape(<2 x i32>* %r)',
                    '  %alias = bitcast <2 x i32>* %r to i64*',
                    '  store <2 x i32> %unknown, <2 x i32>* %r, align 8']:
            with self.assertRaises(ValueError):
                winograd_offsets.private_tables(lines + [bad])

    def test_motion_vector_table_and_center_fallback_are_selected(self):
        # The real kernels keep a best-neighbor offset in loop phis; the center
        # reaches the sample both when the loop is skipped and no neighbor wins.
        pairs = [(-1, 0), (1, 0), (0, -1), (0, 1),
                 (-1, -1), (1, -1), (-1, 1), (1, 1)]
        init = '\n'.join(
            f'  %p{i} = getelementptr [8 x <2 x i32>], [8 x <2 x i32>]* %a, i64 0, i64 {i}\n'
            f'  store <2 x i32> <i32 {x}, i32 {y}>, <2 x i32>* %p{i}, align 8'
            for i, (x, y) in enumerate(pairs))
        source = '''define void @NAME(i64 %index, %texture* %t, %sampler* %s, <2 x float> %coord) {
  %a = alloca [8 x <2 x i32>], align 8
INIT
  %p = getelementptr [8 x <2 x i32>], [8 x <2 x i32>]* %a, i64 0, i64 %index
  %v = load <2 x i32>, <2 x i32>* %p, align 8
  %x = phi <2 x i32> [ zeroinitializer, %entry ], [ %y, %loop ]
  %y = phi <2 x i32> [ %x, %unchanged ], [ %v, %changed ]
  %offset = phi <2 x i32> [ %y, %done ], [ zeroinitializer, %skip ]
  %result = call { <4 x half>, i8 } @air.sample_texture_2d.v4f16(%texture* %t, %sampler* %s, <2 x float> %coord, i1 true, <2 x i32> %offset, i1 false, float 0.0, float 0.0, i32 0)
  ret void
}
'''.replace('INIT', init)
        for name in ('bbrnet_build_tensors_low_resolution', 'brnetv3_build_tensors_low_resolution'):
            with self.subTest(name=name):
                fixed, report = golden_gate.rewrite(source.replace('NAME', name))
                self.assertEqual(report['dilate_offsets'], {'calls': 1, 'extra_samples': 8})
                _, calls = null_compare.read_functions(fixed.splitlines())
                rows = calls['air.sample_texture_2d.v4f16']
                self.assertEqual({args[4] for _, _, args in rows},
                                 {f'<2 x i32> <i32 {x}, i32 {y}>' for x, y in pairs + [(0, 0)]})
                self.assertEqual(golden_gate.rewrite(fixed)[0], fixed)
        untouched = source.replace('NAME', 'unrelated_kernel')
        self.assertEqual(golden_gate.rewrite(untouched)[0], untouched)


if __name__ == '__main__':
    unittest.main()
