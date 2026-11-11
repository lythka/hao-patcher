"""The compiler-service repair's load-order premise and its refusals, on authored x86_64 fixtures only."""
import tempfile
from pathlib import Path
import unittest

from common import FIXTURES, clang, run, x86_runs
import compiler_service


@unittest.skipUnless(x86_runs(), 'needs an x86_64-capable host (Rosetta on Apple silicon)')
class LoaderTests(unittest.TestCase):
    def test_startup_dependency_interposes_but_late_dependency_does_not(self):
        """compat.dylib must be in the service's initial closure: a dependency added to a library loaded later
        (the compiler plugin's owner) does not interpose."""
        with tempfile.TemporaryDirectory(prefix='live-text-loader-') as tmp:
            out = Path(tmp)
            for name in ('plugin', 'interpose', 'owner'):
                clang(out/(name + '.dylib'), '-dynamiclib', '-Wl,-headerpad,0x400', FIXTURES/(name + '.c'))
            clang(out/'main', FIXTURES/'main.c')
            clang(out/'startup', FIXTURES/'main.c', '-Wl,-needed_library,' + str(out/'interpose.dylib'))
            changed, _ = compiler_service.editor.append_dylibs((out/'owner.dylib').read_bytes(),
                                                               [str(out/'interpose.dylib')], resign_filetype=6)
            (out/'late.dylib').write_bytes(changed)
            run('codesign', '-f', '-s', '-', out/'late.dylib')
            self.assertEqual(run(out/'main', out/'owner.dylib', out/'plugin.dylib'), 'result=41')
            self.assertEqual(run(out/'main', out/'late.dylib', out/'plugin.dylib'), 'result=41')
            self.assertEqual(run(out/'startup', out/'owner.dylib', out/'plugin.dylib'), 'result=42')
            clang(out/'bootstrap.dylib', '-dynamiclib', '-DHELPER="%s"' % (out/'interpose.dylib'), FIXTURES/'bootstrap.c')
            clang(out/'conditional', FIXTURES/'main.c', '-Wl,-needed_library,' + str(out/'bootstrap.dylib'))
            clang(out/'weak.dylib', '-dynamiclib', '-undefined', 'dynamic_lookup', FIXTURES/'weak_interpose.c')
            clang(out/'weak-present', FIXTURES/'call.c', '-Wl,-needed_library,' + str(out/'weak.dylib'),
                  '-Wl,-needed_library,' + str(out/'plugin.dylib'))
            clang(out/'weak-absent', FIXTURES/'main.c', '-Wl,-needed_library,' + str(out/'weak.dylib'))
            self.assertEqual(run(out/'conditional', out/'owner.dylib', out/'plugin.dylib'), 'result=41')
            self.assertEqual(run(out/'weak-present'), 'result=42')
            self.assertEqual(run(out/'weak-absent', out/'owner.dylib', out/'plugin.dylib'), 'result=41')


class ServiceTests(unittest.TestCase):
    def test_refuses_a_service_with_another_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            clang(out/'service', FIXTURES/'main.c')
            with self.assertRaises(ValueError):
                compiler_service.check_service((out/'service').read_bytes())


if __name__ == '__main__':
    unittest.main()
