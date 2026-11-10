import importlib.util
import importlib.machinery
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('load_shim', PATCH/'src/load_shim.py')
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

def run(*args):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True)

class FilterTest(unittest.TestCase):
    def test_deferred_software_backdrops(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp)/'backdrops'
            run('xcrun', 'clang', '-arch', 'x86_64', '-O2', '-Wall', '-Wextra',
                '-Wno-unused-function', '-Werror', PATCH/'tests/backdrop_test.c', '-o', exe)
            run('codesign', '-s', '-', exe)
            self.assertIn('PASS:', run(exe).stdout)

    def test_native_adapter_and_unadapted_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp)/'adapter'
            run('xcrun', 'clang', '-arch', 'x86_64', '-O2', '-Wall', '-Wextra', '-Werror',
                PATCH/'tests/adapter_test.c', '-o', exe)
            run('codesign', '-s', '-', exe)
            self.assertIn('PASS:', run(exe).stdout)
            failed = subprocess.run([str(exe), 'unadapted'], capture_output=True)
            self.assertIn(failed.returncode, (-signal.SIGSEGV, -signal.SIGBUS))

    def test_real_display_shim_preserves_code_and_existing_interposer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            path = PATCH.parents[1]/'tools/display-state-shim/display-state-shim'
            source = importlib.machinery.SourceFileLoader('owned_dss_producer', str(path))
            spec = importlib.util.spec_from_loader(source.name, source)
            producer = importlib.util.module_from_spec(spec)
            source.exec_module(producer)
            producer.build(tmp/'artifact')
            src = tmp/'artifact/dss_shim.dylib'
            original = src.read_bytes()
            changed = bytearray(original)
            self.assertTrue(loader.add(changed))
            self.assertFalse(loader.add(changed))
            out = tmp/'dss_shim.dylib'; out.write_bytes(changed)
            # Only header/load-command padding may differ before re-signing.
            before_text = run('otool', '-s', '__TEXT', '__text', src).stdout.split('\n', 1)[1]
            after_text = run('otool', '-s', '__TEXT', '__text', out).stdout.split('\n', 1)[1]
            self.assertEqual(before_text, after_text)
            run('codesign', '-f', '-s', '-', '--preserve-metadata=identifier,entitlements,flags,runtime', out)
            run('codesign', '--verify', '--strict', out)
            self.assertIn(loader.DYLIB.decode(), run('otool', '-L', out).stdout)
            self.assertIn('_CGXGetDisplaySystemState', run('nm', out).stdout)

    def test_uuid_and_process_gate(self):
        # Compile the real gate in this test process: no __SLSERVER means inert.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp/'gate.c'
            src.write_text('#include "'+str(PATCH/'src/filter_shim.c')+'"\n'
                '#include <assert.h>\nint main(void) {\n'
                'struct { struct mach_header_64 h; struct uuid_command u; } m = {0};\n'
                'uint8_t id[16] = {1}; m.h.magic=MH_MAGIC_64; m.h.cputype=CPU_TYPE_X86_64;\n'
                'm.h.ncmds=1; m.h.sizeofcmds=sizeof m.u; m.u.cmd=LC_UUID; m.u.cmdsize=sizeof m.u; m.u.uuid[0]=1;\n'
                'assert(uuid_is((void *)&m,id)); m.u.uuid[0]=2; assert(!uuid_is((void *)&m,id));\n'
                'm.u.uuid[0]=1; m.h.cputype=CPU_TYPE_ARM64; assert(!uuid_is((void *)&m,id));\n'
                'identify(); assert(!identity.api_table && !identity.display_callbacks); return 0; }\n')
            run('xcrun', 'clang', '-arch', 'x86_64', '-O2', '-framework', 'QuartzCore', src, '-o', tmp/'gate')
            run('codesign', '-s', '-', tmp/'gate')
            run(tmp/'gate')
