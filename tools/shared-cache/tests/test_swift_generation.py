from pathlib import Path
import struct
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dyld_rebuild.exports import Refusal
from dyld_rebuild.swift_generation import plan, identify_inputs
from dyld_rebuild.swift_metadata import recipe_text
from dyld_rebuild.metadata_graph import MetadataGraph

UUID='00000000-0000-0000-0000-000000000000'
class Family:
    main=SimpleNamespace(data=bytes(128),mappings=[SimpleNamespace(address=0x1000)])
    def read(self,address,size):
        for d,params,count in [(0x1100,1,1),(0x1200,1,2),(0x1300,2,2)]:
            if address==d:return struct.pack('<I',17|128)
            if address==d+36:return struct.pack('<4H',params,0,count,0)
            if address==d+44:return b'\x80'*size
        raise AssertionError(address)
class References:
    def __init__(self,family):pass
    def decode(self,ref):return ref['offset']

def node(key,name,args=()):
    return dict(node=key,name=name,kind_name='struct',arguments=list(args))
def root(key,descriptor=0x1100):
    return dict(root=key,type=key,observed_name=f'Type{key}Sg',
                descriptor_reference=dict(section='__const', segment='__TEXT', image_uuid='a'*32, image='/Swift',offset=descriptor))
def graph():
    return dict(engine='SwiftRemoteMirror',identity=dict(cache_uuid=UUID),
                nodes={'1':node(1,'Parent',[2]),'2':node(2,'Child',[3]),'3':node(3,'Si')},
                roots=[root(1),root(2)])
class GenerationTests(unittest.TestCase):
    def test_reflected_binary_identity_is_bound_and_must_have_been_captured(self):
        from dyld_rebuild.metadata_expand import reused_graph
        import json
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'application'
            import uuid
            binary.write_bytes(struct.pack('<8I', 0xfeedfacf, 0x01000007, 3, 2, 1, 24, 0, 0) +
                               struct.pack('<II', 0x1b, 24) + uuid.UUID(UUID).bytes)
            document=MetadataGraph().document
            reflected=dict(images=[dict(uuid=UUID)])
            identify_inputs(document,reflected,[binary])
            output=Path(folder)/'graph.json';output.write_text(json.dumps(document))
            self.assertEqual(reused_graph(output,[binary]).document,document)
            with self.assertRaisesRegex(Refusal,'was not captured'):
                identify_inputs(document,dict(images=[]),[binary])
    def test_opaque_protocol_recipe_preserves_subject_context_and_order(self):
        recipe = dict(schema=1, kind='generic-values', cache_uuid=UUID,
            definitions=[dict(id='1', operation='resolve', name='Su'),
                dict(id='2', operation='opaque', descriptor=256, context=128, ordinal=0,
                     key_arguments=2, arguments=['1'], requirements=[dict(subject_address=512,
                     subject_length=1, protocol=768)])],
            records=[dict(name='opaque', owner='/Provider', dependency='2')])
        self.assertIn('@opaque\t2\t100\t80\t0\t2\t1\t200:1:300\n', recipe_text(recipe, True))
    def plan(self,g):
        with patch('dyld_rebuild.swift_generation.References',References):
            document, report = plan(Family(),g,dict(loads=['/Swift']))
        with patch('dyld_rebuild.swift_metadata.References', References):
            return MetadataGraph(document).bind(Family()), report
    def test_child_constructors_precede_parent_without_heap_addresses(self):
        recipe,report=self.plan(graph())
        self.assertEqual(report['candidates'],2)
        self.assertEqual([d['id'] for d in recipe['definitions']],['1','2','3'])
        text=recipe_text(recipe,True)
        self.assertIn('@type\t1\tSi\n@generic\t2\t100\t1\n@generic\t3\t100\t2',text)
        self.assertIn('Type1Sg\t/Swift\t&3',text)
    def test_witness_keys_are_constructed_natively_from_reflected_type_arguments(self):
        g=graph();g['roots'][1]=root(2,0x1200)
        recipe,report=self.plan(g)
        self.assertEqual(report['dependency_blockers'],{})
        self.assertEqual(report['candidates'],2)
        self.assertEqual(report['entries'][1]['witness_arguments'],1)
        self.assertIn('@generic\t2\t200\t1',recipe_text(recipe,True))
        self.assertEqual(len(report['entries']),2)
    def test_missing_outer_arguments_are_not_guessed(self):
        g=graph();g['roots'][1]=root(2,0x1300)
        _,report=self.plan(g)
        self.assertEqual(report['dependency_blockers'],{'reflection_arguments_incomplete':2})
    def test_anonymous_leaf_and_variant_ambiguity_stay_explicit(self):
        g=graph();g['nodes']['3']['name']='Child13$7ff123456789LL'
        _,report=self.plan(g)
        self.assertEqual(report['dependency_blockers'],{'anonymous_dependency_without_descriptor':2})
        g=graph();g['roots'].append(dict(root(2),root=4))
        _,report=self.plan(g)
        self.assertEqual(report['candidates'],0)
        self.assertEqual(report['dependency_blockers'],{'multiple_metadata_variants_for_typeref':3})
    def test_private_leaf_uses_unique_observed_file_identity(self):
        g=graph();g['nodes']['3']['name']='Child13$7ff123456789LL'
        ref=root(3)['descriptor_reference']
        g['conformances']=[dict(type=3,metadata=0x9999,metadata_reference=ref)]
        recipe,report=self.plan(g)
        self.assertEqual(report['candidates'],2)
        self.assertIn('@reference\t1\t100',recipe_text(recipe,True))
        g['conformances'].append(dict(type=3,metadata=0x8888,metadata_reference=None))
        self.assertEqual(self.plan(g)[1]['candidates'],0)
        g['conformances']=[]
        self.assertEqual(self.plan(g)[1]['dependency_blockers'],{'anonymous_dependency_without_descriptor':2})
    def test_recipe_rejects_forward_dependency(self):
        recipe,_=self.plan(graph());recipe['definitions'].reverse()
        with self.assertRaisesRegex(Refusal,'before parent'):recipe_text(recipe,True)
