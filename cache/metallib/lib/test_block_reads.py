"""Check two-texel storage, retained sampling operands and rewrite ordering."""
import unittest
import block_reads
import golden_gate
import texture_coords


def fixture(shape='<i32 2, i32 1>', residency='null'):
    types=block_reads.TYPES
    args=[types[0]+' %texture',types[1]+' %sampler','<2 x i32> %coord',
          'i32 %layer','<2 x i32> <i32 -1, i32 3>','i32 %lod','i32 0',
          '<4 x float>* %out','i8* '+residency,'<2 x i32> '+shape]
    return '''%struct._texture_2d_array_t = type opaque
%struct._sampler_t = type opaque
''' + 'define void @probe('+', '.join([args[0],args[1],args[2],args[3],args[5],args[7]])+') {\n  call void @'+block_reads.BLOCK+'('+', '.join(args)+')\n  ret void\n}\n' + 'declare void @'+block_reads.BLOCK+'('+', '.join(types)+')\n'


class BlockReads(unittest.TestCase):
    def test_image_recipe_does_not_apply_rejected_candidate(self):
        text,_=golden_gate.rewrite(fixture())
        self.assertIn('call void @'+block_reads.BLOCK,text)

    def test_horizontal_and_vertical_retain_sampling_state_and_both_stores(self):
        for shape,step in [('<i32 2, i32 1>','<i32 1, i32 0>'),('<i32 1, i32 2>','<i32 0, i32 1>')]:
            text,report=block_reads.rewrite(fixture(shape))
            self.assertEqual(report['calls'],1)
            self.assertNotIn('@'+block_reads.BLOCK,text)
            self.assertIn('add <2 x i32> %coord, '+step,text)
            self.assertEqual(text.count('i32 %layer, <2 x i32> <i32 -1, i32 3>, i32 %lod, i32 0'),2)
            self.assertEqual(text.count('store <4 x float>'),2)
            self.assertIn('<4 x float>* %out, i32 0',text)
            self.assertIn('<4 x float>* %out, i32 1',text)
            self.assertEqual(block_reads.rewrite(text),(text,{'calls':0}))

    def test_sparse_and_other_shapes_remain_explicitly_unsupported(self):
        for text in [fixture(residency='%residency'),fixture('<i32 2, i32 2>')]:
            with self.assertRaises(ValueError):block_reads.rewrite(text)

    def test_block_expansion_precedes_mixed_coordinate_normalization(self):
        text=fixture()
        declaration='declare { <4 x float>, i8 } @air.read_texture_2d_array.i16.v4f32('+', '.join([block_reads.TYPES[0],block_reads.TYPES[1],'<2 x i16>','i16','<2 x i16>','i16','i32'])+')\n'
        call='  %narrow = call { <4 x float>, i8 } @air.read_texture_2d_array.i16.v4f32('+', '.join([block_reads.TYPES[0]+' %texture',block_reads.TYPES[1]+' %sampler','<2 x i16> zeroinitializer','i16 0','<2 x i16> zeroinitializer','i16 0','i32 0'])+')\n'
        text=text.replace('  ret void',call+'  ret void')+declaration
        text,report=block_reads.rewrite(text)
        self.assertEqual(report['calls'],1)
        text,_=texture_coords.rewrite(text)
        self.assertNotIn('@air.read_texture_2d_array.i16.v4f32',text)


if __name__=='__main__':unittest.main()
