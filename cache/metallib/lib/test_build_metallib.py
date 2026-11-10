import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import build_metallib
import compatibility
import mtlb
from test_inline_private import program
from test_mtlb import fixture


class FullLibraryTests(unittest.TestCase):
    def test_sync_only_preserves_unrelated_private_helpers(self):
        from test_legacy_sync import program as sync_program
        with tempfile.TemporaryDirectory(prefix='shader-sync-only-') as tmp:
            root = Path(tmp)
            source, air = root / 'sync.ll', root / 'sync.air'
            helper = '\ndefine internal i32 @keep(i32* %p) {\n  %x = load i32, i32* %p\n  ret i32 %x\n}\n'
            source.write_text(sync_program().replace('@probe(', '@a(') + helper)
            subprocess.run(['xcrun', 'metal-as', str(source), '-o', str(air)], capture_output=True, check=True)
            library, output = root / 'input.metallib', root / 'built'
            library.write_bytes(mtlb.splice(fixture(shared=False), {'a': air.read_bytes()}))
            with patch.object(sys, 'argv', ['build_metallib.py', str(library), str(output), '--legacy-sync-only']):
                build_metallib.main()
            report = json.loads((output / 'report.json').read_text())
            row = report['functions'][0]
            self.assertEqual(row['calls'], 5)
            self.assertEqual(row['functions'], ['a'])
            self.assertEqual(row['removed_internal_definitions'], [])
            self.assertEqual(row['unchanged_definition_text_count'], 1)
            self.assertTrue(row['shared_module_metadata_equal'])
            emitted = build_metallib.disassemble(output / 'functions/000-a/fixed.air')
            self.assertIn('@air.wg.barrier(i32 2, i32 1)', emitted)
            self.assertIn('define internal i32 @keep', emitted)

    def test_metadata_graph_allows_only_dead_nodes_and_renumbering(self):
        old = '!factory = !{!0}\n!0 = distinct !{!0, !2, !"!77"}\n!1 = !{i32 123}\n!2 = !{i32 42}\n'
        new = '!factory = !{!8}\n!8 = distinct !{!8, !9, !"!77"}\n!9 = !{i32 42}\n'
        self.assertEqual(build_metallib.metadata_graph(old), build_metallib.metadata_graph(new))
        for changed in (new.replace('42', '43'), new.replace('distinct ', ''), new.replace('!77', '!78')):
            self.assertNotEqual(build_metallib.metadata_graph(old), build_metallib.metadata_graph(changed))
        attached = '  store i32 0, i32* %p, !tbaa !1\n' + old
        self.assertNotEqual(build_metallib.metadata_graph(attached), build_metallib.metadata_graph(new))

    def test_golden_gate_preserves_legacy_signatures_and_container(self):
        from test_legacy_sync import program as sync_program
        with tempfile.TemporaryDirectory(prefix='shader-sync-') as tmp:
            root = Path(tmp)
            source, air = root / 'sync.ll', root / 'sync.air'
            source.write_text(sync_program().replace('@probe(', '@a('))
            subprocess.run(['xcrun', 'metal-as', str(source), '-o', str(air)],
                           capture_output=True, check=True)
            library = root / 'input.metallib'
            library.write_bytes(mtlb.splice(fixture(shared=False), {'a': air.read_bytes()}))
            output = root / 'built'
            with patch.object(sys, 'argv', ['build_metallib.py', str(library), str(output), '--golden-gate']):
                build_metallib.main()
            report = json.loads((output / 'report.json').read_text())
            row = report['functions'][0]
            self.assertEqual(row['legacy_sync']['calls'], 5)
            self.assertEqual(row['assembly_command'][:2], ['xcrun', 'metal-as'])
            self.assertTrue(row['shared_module_metadata_equal'])
            blob = (output / 'default.metallib').read_bytes()
            self.assertEqual([f['name'] for f in mtlb.check(blob)['fns']], ['a'])
            self.assertEqual(blob[-4:], b'tail')
            emitted = build_metallib.disassemble(output / 'functions/000-a/fixed.air')
            self.assertIn('@air.wg.barrier(i32 2, i32 1)', emitted)

    def test_removed_helper_report_counts_surviving_definitions(self):
        original = program()
        fixed, _ = compatibility.rewrite(original)
        report = build_metallib.definition_changes(original, fixed)
        self.assertEqual(report['removed_internal_definitions'], ['read'])
        self.assertEqual(report['functions'], ['probe'])
        self.assertEqual(report['unchanged_definition_text_count'], 0)
        with self.assertRaisesRegex(AssertionError, 'public ABI'):
            build_metallib.definition_changes(original, '')

    def test_direct_air_requires_complete_metadata_equality(self):
        # The factory-container route must reject a changed metadata value even
        # when both AIR files assemble successfully.
        with tempfile.TemporaryDirectory(prefix='shader-metadata-control-') as tmp:
            root = Path(tmp)
            source, air = root / 'input.ll', root / 'input.air'
            source.write_text('define void @a() { ret void }\n'
                              '!factory = !{!0}\n!0 = !{i32 193}\n')
            subprocess.run(['xcrun', 'metal-as', str(source), '-o', str(air)],
                           capture_output=True, check=True)
            original = build_metallib.disassemble(air)
            self.assertEqual(build_metallib.assembled_module(air, original, 'a'), air.read_bytes())
            source.write_text(source.read_text().replace('193', '188'))
            subprocess.run(['xcrun', 'metal-as', str(source), '-o', str(air)],
                           capture_output=True, check=True)
            with self.assertRaisesRegex(AssertionError, 'assembled module metadata changed'):
                build_metallib.assembled_module(air, original, 'a')

    def test_plain_helper_full_container_build_uses_assembler_and_removes_helper(self):
        # A complete synthetic MTLB exercises actual LLVM inlining, assembly,
        # disassembly, splicing and report generation without a GPU claim.
        with tempfile.TemporaryDirectory(prefix='shader-library-build-') as tmp:
            root = Path(tmp)
            source = root / 'input.ll'
            source.write_text(program().replace('@probe(', '@a('))
            air = root / 'input.air'
            subprocess.run(['xcrun', 'metal-as', str(source), '-o', str(air)],
                           capture_output=True, check=True)
            library = root / 'input.metallib'
            library.write_bytes(mtlb.splice(fixture(shared=False), {'a': air.read_bytes()}))
            output = root / 'built'
            with patch.object(sys, 'argv', ['build_metallib.py', str(library), str(output)]):
                build_metallib.main()
            report = json.loads((output / 'report.json').read_text())
            row = report['functions'][0]
            self.assertEqual(row['assembly_command'][:2], ['xcrun', 'metal-as'])
            self.assertEqual(row['removed_internal_definitions'], ['read'])
            self.assertEqual(row['unchanged_definition_text_count'], 0)
            self.assertTrue(row['shared_module_metadata_equal'])
            data = (output / 'default.metallib').read_bytes()
            parsed = mtlb.check(data)
            self.assertEqual([f['name'] for f in parsed['fns']], ['a'])
            self.assertEqual(data[-4:], b'tail')
            with patch.object(sys, 'argv', ['build_metallib.py', str(library), str(output)]):
                with self.assertRaises(FileExistsError):
                    build_metallib.main()
