"""mps-sift: deterministic inputs, kernel-case generation and keypoint comparison (host only, no GPU)."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mps-sift'))
import compare  # noqa: E402
import gen  # noqa: E402
import diagnose_width  # noqa: E402
import diagnose_logical32  # noqa: E402


def record(x, y, scale, orientation, response, ix, iy, octave, layer):
    return struct.pack('<5f4i', x, y, scale, orientation, response, ix, iy, octave, layer)


class Inputs(unittest.TestCase):
    def test_sources_are_fixed_bytes(self):
        # Integer-only generation: these digests hold on any host and Python build.
        field = gen.intensity(17, 23, 17023)
        self.assertEqual(hashlib.sha256(gen.encode(field, gen.R8UNORM)).hexdigest()[:16], DIGEST_R8)
        self.assertEqual(len(gen.encode(field, gen.R32FLOAT)), 17 * 23 * 4)
        self.assertEqual(len(gen.encode(field, gen.R16FLOAT)), 17 * 23 * 2)
        self.assertEqual(gen.encode([[65535, 0]], gen.R16UNORM), b'\xff\xff\x00\x00')

    def test_sources_and_kernel_cases(self):
        with tempfile.TemporaryDirectory() as t:
            out, ref = Path(t) / 'in', Path(t) / 'ref'
            gen.sources(out, ['16x16'], ['r32float', 'r8unorm'], True)
            cases = json.loads((out / 'api-cases.json').read_text())
            self.assertEqual(len(cases), 4)
            fused = json.loads((out / 'api-fused-cases.json').read_text())
            self.assertTrue(all(c['fused'] for c in fused) and len(fused) == 2)
            # A reference run with one octave dispatch and its dumps.
            ref.mkdir()
            params = bytearray(76)
            struct.pack_into('<H', params, 24, 5)
            d = dict(function=gen.MULTIFETCH + '|124.UInt=3;127.UInt=26316799', bytes0=params.hex(),
                     texture0=dict(width=16, height=16, format=22, array=5), texture1=dict(width=16, height=16, format=20, array=6),
                     texture0_dump='a.bin', texture1_dump='b.bin', grid=[1, 3, 1], threads=[32, 1, 1],
                     sampler0=dict(normalized=False, s=4, t=4, r=4, min=0, mag=0, mip=0))
            (ref / 'a.bin').write_bytes(b'\0' * 16 * 16 * 5 * 2)
            (ref / 'b.bin').write_bytes(b'\0' * 16 * 16 * 6 * 2)
            (ref / 'api-results.json').write_text(json.dumps([dict(case='api-16x16-r32float-fused', dispatches=[d])]))
            gen.kernel_cases(out, ref)
            rows = json.loads((out / 'kernel-cases.json').read_text())
            names = [r['name'] for r in rows]
            self.assertEqual(names, ['kernel-16x16-o0-multifetch-apple', 'kernel-16x16-o0-multifetch-intel',
                                     'kernel-16x16-o0-multifetch-mps0', 'kernel-16x16-o0-multifetch-mps1',
                                     'kernel-16x16-o0-fused-apple', 'kernel-16x16-o0-fused-intel'])
            self.assertEqual(rows[0]['constants'], '124.UInt=3;127.UInt=26316799')
            self.assertEqual(rows[1]['constants'], '124.UInt=3;127.UInt=137')
            self.assertTrue((out / 'a.bin').exists())
            diag = json.loads((out / 'width8-cases.json').read_text())
            self.assertEqual([r['name'] for r in diag], ['kernel-16x16-o0-multifetch-mps1'])
            self.assertEqual(diag[0]['recordedDispatch']['threads'], [8, 1, 1])
            self.assertEqual(diag[0]['recordedDispatch']['grid'], [1, 3, 1])
            self.assertEqual(rows[0]['recordedDispatch']['threads'], [32, 1, 1])
            blocks = json.loads((out / 'block-cases.json').read_text())
            self.assertEqual([(b['name'], b['texture'], b['format'], b['layers']) for b in blocks], [('block-16x16-o0', 'a.bin', 22, 5)])


class Compare(unittest.TestCase):
    def test_orientation_wraps_in_degrees(self):
        self.assertAlmostEqual(compare.angle_error(359.9, 0.1), 0.2)
        self.assertEqual(compare.angle_error(90, 270), 180)

    def test_multiset_retains_duplicate_counts(self):
        a, b = record(1, 2, 3, 4, 5, 1, 2, 0, 1), record(6, 7, 8, 9, 10, 6, 7, 0, 1)
        self.assertFalse(compare.compare([a, a, b], [a, b, b])['bitwise_identical'])

    def test_missing_output_is_not_an_empty_success(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(FileNotFoundError):
                compare.records(Path(t) / 'missing.keypoints')

    def test_late_failed_repeat_is_not_a_success(self):
        with tempfile.TemporaryDirectory() as t:
            ref, test = Path(t) / 'ref', Path(t) / 'test'
            ref.mkdir(); test.mkdir()
            for path, statuses in ((ref, [4, 4]), (test, [4, 5])):
                (path / 'kernel-results.json').write_text(json.dumps([
                    dict(case='one', runs=[dict(status=s) for s in statuses])]))
            rows = compare.pair(ref, test, Path(t) / 'report.json')
            self.assertEqual(rows[0]['result'], 'error')

    def test_no_cases_is_not_a_success(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError, 'no test cases'):
                compare.pair(Path(t), Path(t), Path(t) / 'report.json')

    def test_group_keys(self):
        self.assertEqual(compare.group('kernel-97x61-o2-multifetch-apple-tg8'), 'multifetch-apple-tg8')
        self.assertEqual(compare.group('kernel-97x61-o0-fused-intel'), 'fused-intel')
        self.assertEqual(compare.group('api-97x61-r8unorm-fused'), 'api-fused')

    def test_positions_separate_detection_from_orientation(self):
        a = [record(1, 2, 3, 10, 5, 1, 2, 0, 1), record(1, 2, 3, 200, 5, 1, 2, 0, 1)]
        b = [record(1, 2, 3, 99, 5.0001, 1, 2, 0, 1)]
        r = compare.compare(a, b)
        self.assertEqual((r['positions_reference'], r['positions_test'], r['positions_common']), (1, 1, 1))
        self.assertAlmostEqual(r['response_max_relative'], 0.0001 / 5, places=6)

    def test_bitwise_multiset_ignores_order(self):
        a, b = record(1, 2, 3, 4, 5, 1, 2, 0, 1), record(6, 7, 8, 9, 10, 6, 7, 0, 1)
        r = compare.compare([a, b], [b, a])
        self.assertTrue(r['bitwise_identical'])
        self.assertEqual(r['bitwise_equal'], 2)

    def test_float_only_difference_is_paired_with_ulps(self):
        a = record(1.0, 2, 3, 90.0, 5, 1, 2, 0, 1)
        b = record(1.0, 2, 3, struct.unpack('<f', struct.pack('<I', struct.unpack('<I', struct.pack('<f', 90.0))[0] + 2))[0], 5, 1, 2, 0, 1)
        r = compare.compare([a], [b])
        self.assertFalse(r['bitwise_identical'])
        self.assertEqual((r['paired_inexact'], r['unpaired_reference'], r['unpaired_test']), (1, 0, 0))
        self.assertEqual(r['max_error']['orientation']['ulp'], 2)

    def test_missing_records_are_unpaired(self):
        a, b = record(1, 2, 3, 4, 5, 1, 2, 0, 1), record(1, 2, 3, 4, 5, 9, 9, 0, 1)
        r = compare.compare([a], [b])
        self.assertEqual((r['unpaired_reference'], r['unpaired_test']), (1, 1))

    def test_ulp_across_zero(self):
        self.assertEqual(compare.ulp(0.0, -0.0), 0)
        self.assertEqual(compare.ulp(1.0, 1.0), 0)


# The evidence run's src-17x23-r8unorm.bin (sha256 f825280d79030994...).
DIGEST_R8 = 'f825280d79030994'


class WidthDiagnostic(unittest.TestCase):
    def test_tiling_and_loops_change_but_shift_masks_do_not(self):
        source = ('  %a = mul nuw nsw i32 %g, 62\n'
                  '  %b = icmp ult i16 %t, 31\n'
                  '  %octave = and i32 %o, 31\n')
        source += ''.join(f'  %x{i} = add i16 %v{i}, 32\n' for i in range(16))
        source += ''.join(f'  %m{i} = and i32 %n{i}, 65504\n' for i in range(4))
        result, _ = diagnose_width.rewrite(source)
        self.assertIn('%g, 14', result)
        self.assertIn('%t, 7', result)
        self.assertIn('%octave = and i32 %o, 31', result)
        self.assertEqual(result.count(', 8\n'), 16)
        self.assertEqual(result.count(', 65528\n'), 4)

    def test_unknown_shader_is_not_partially_rewritten(self):
        with self.assertRaises(ValueError):
            diagnose_width.rewrite('define void @unrelated() { ret void }')

class Logical32Diagnostic(unittest.TestCase):
    @staticmethod
    def chains():
        lines = []
        for i in range(4):
            lines += [f'  %max{i} = tail call fast float @air.simd_max.f32(float %input{i}) #13',
                      f'  %fold{i} = tail call fast float @air.fast_fmax.f32(float %local{i}, float %max{i}) #14',
                      f'  %local{i} = phi float [ %fold{i}, %body{i} ], [ {diagnose_logical32.NEUTRAL}, %entry{i} ]',
                      f'  %exit{i} = phi float [ {diagnose_logical32.NEUTRAL}, %entry{i} ], [ %fold{i}, %body{i} ]',
                      f'  %frozen{i} = freeze float %exit{i}',
                      f'  %broadcast{i} = tail call fast float @air.simd_broadcast.f32(float %frozen{i}, i16 0) #13']
        return '\n'.join(lines)

    def test_maximum_chain_is_recognized(self):
        self.assertEqual(diagnose_logical32.maximum_chains(self.chains()),
                         {f'%broadcast{i}' for i in range(4)})

    def test_reduction_with_another_consumer_is_refused(self):
        for extra in ('%extra = fadd float %max0, 1.0', 'store float %max0, float* %out'):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'additional consumer'):
                diagnose_logical32.maximum_chains(self.chains() + '\n  ' + extra)

    def test_different_broadcast_lane_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'lane-zero'):
            diagnose_logical32.maximum_chains(self.chains().replace('i16 0)', 'i16 1)', 1))

    def test_maximum_with_a_different_neutral_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'neutral'):
            diagnose_logical32.maximum_chains(self.chains().replace(diagnose_logical32.NEUTRAL, '0.0', 1))

    def test_reduction_after_reconvergence_keeps_partial_last_round(self):
        # Lane zero runs every iteration in the original strided loop. Its fold
        # receives each active-lane maximum, including the last four of 36 bins.
        for bins in (0, 1, 4, 31, 32, 33, 36, 64, 71):
            for peak in range(max(1, bins)):
                values = [-float(i + 1) for i in range(bins)]
                if values:
                    values[peak] = 100.0
                neutral = -3.4028234663852886e38
                original = neutral
                for start in range(0, bins, 32):
                    original = max(original, max(values[start:start + 32]))
                local = [max([neutral] + values[lane::32]) for lane in range(32)]
                self.assertEqual(original, max(local))

    def test_helper_barriers_keep_the_input_abi_and_threadgroup_scope(self):
        for modern, signature in ((False, 'i32 2, i32 1'), (True, 'i32 2, i32 5, i32 1')):
            text = diagnose_logical32.helpers(modern)
            self.assertIn(f'@air.wg.barrier({signature})', text)
            self.assertNotIn('simdgroup.barrier', text)
            self.assertNotIn('readnone', text)

    def test_unknown_shader_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'multifetch module'):
            diagnose_logical32.rewrite('define void @unrelated() { ret void }')


if __name__ == '__main__':
    unittest.main()
