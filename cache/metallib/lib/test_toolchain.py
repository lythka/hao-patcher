import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import toolchain


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.bin = Path(self.folder.name)
        for name in ('llvm-as', 'llvm-dis'):
            (self.bin / name).write_bytes(name.encode())

    def tearDown(self):
        self.folder.cleanup()

    def env(self, value):
        return mock.patch.dict(os.environ, {toolchain.ENV: value} if value else {}, clear=False)

    def test_xcrun_unchanged_without_bundle(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(toolchain.ENV, None)
            argv = ['xcrun', 'metal-as', 'a.ll', '-o', 'a.air']
            self.assertEqual(toolchain.resolve(argv), argv)
            self.assertEqual(toolchain.resolve(['xcrun', 'metal', '-c', 'x']), ['xcrun', 'metal', '-c', 'x'])
            self.assertIsNone(toolchain.identity())

    def test_mapped_tools(self):
        with self.env(str(self.bin)):
            self.assertEqual(toolchain.resolve(['xcrun', 'metal-as', 'a.ll', '-o', Path('a.air')]),
                             [str(self.bin / 'llvm-as'), '-opaque-pointers=0', 'a.ll', '-o', 'a.air'])
            self.assertEqual(toolchain.resolve(['xcrun', 'metal-objdump', '--disassemble', 'a.air']),
                             [str(self.bin / 'llvm-dis'), '-opaque-pointers=0', 'a.air', '-o', '-'])
            self.assertEqual(sorted(toolchain.identity()), ['llvm-as', 'llvm-dis'])

    def test_other_commands_untouched(self):
        with self.env(str(self.bin)):
            self.assertEqual(toolchain.resolve(['codesign', '-s', '-', 'x']), ['codesign', '-s', '-', 'x'])

    def test_xcode_only_tools_refused(self):
        with self.env(str(self.bin)):
            for argv in (['xcrun', 'metal', '-c', 'x'], ['xcrun', 'metallib', 'x'], ['xcrun', 'clang', 'x'],
                         ['xcrun', 'metal-objdump', '--macho', 'x'], ['xcrun']):
                with self.assertRaises(toolchain.Unavailable, msg=argv):
                    toolchain.resolve(argv)

    def test_missing_tool_refused(self):
        with self.env(str(self.bin)):
            with self.assertRaises(toolchain.Unavailable):
                toolchain.resolve(['xcrun', 'metal-opt', '-S', 'x'])


class SourceTests(unittest.TestCase):
    def test_hao_source_without_git(self):
        with tempfile.TemporaryDirectory() as top:
            unit = Path(top) / 'cache/metallib'
            unit.mkdir(parents=True)
            (Path(top) / '.hao-source').write_text(json.dumps({'commit': 'abc123', 'source': 'url'}))
            with mock.patch('subprocess.run', side_effect=AssertionError('git must not run')):
                self.assertEqual(toolchain.source(unit), {'commit': 'abc123', 'dirty': None})

    def test_nothing_known_refused(self):
        with tempfile.TemporaryDirectory() as top:
            with self.assertRaises(SystemExit):
                toolchain.source(Path(top))

    def test_this_checkout_uses_git(self):
        record = toolchain.source(Path(__file__).parent)
        self.assertRegex(record['commit'], r'^[0-9a-f]{40}$')
        self.assertIsInstance(record['dirty'], bool)


if __name__ == '__main__':
    unittest.main()


class PortableOptimizerTests(unittest.TestCase):
    def test_ambiguous_flags_refused_before_running_optimizer(self):
        from unittest.mock import patch
        text = ('target triple = "air64_v29-apple-macosx27.0.0"\n'
                '  %x = fadd nnan ninf nsz arcp contract afn float %a, %b\n')
        with patch.object(toolchain, 'bundled', return_value=Path('/unused')):
            with self.assertRaisesRegex(toolchain.Unavailable, 'ambiguous'):
                toolchain.optimize_ir(text, 'instcombine', link=True)

    def test_missing_triple_refused(self):
        from unittest.mock import patch
        with patch.object(toolchain, 'bundled', return_value=Path('/unused')):
            with self.assertRaisesRegex(toolchain.Unavailable, 'triple'):
                toolchain.optimize_ir('define void @f() { ret void }', 'instcombine', link=True)
