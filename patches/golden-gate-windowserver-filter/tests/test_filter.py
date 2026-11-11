import importlib.util
import importlib.machinery
import os
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
            for slot in ('124', '126'):
                failed = subprocess.run([str(exe), slot], capture_output=True)
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

    def test_uuid_profiles_and_process_gate(self):
        # Compile the real gate in this test process: no __SLSERVER means inert.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp/'gate.c'
            src.write_text('#include "'+str(PATCH/'src/filter_shim.c')+'"\n'
                '#include <assert.h>\nint main(void) {\n'
                'struct { struct mach_header_64 h; struct uuid_command u; } m = {0};\n'
                'uint8_t id[16]; m.h.magic=MH_MAGIC_64; m.h.cputype=CPU_TYPE_X86_64;\n'
                'm.h.ncmds=1; m.h.sizeofcmds=sizeof m.u; m.u.cmd=LC_UUID; m.u.cmdsize=sizeof m.u; m.u.uuid[0]=1;\n'
                'assert(image_uuid((void *)&m,id) && id[0]==1);\n'
                'm.h.cputype=CPU_TYPE_ARM64; assert(!image_uuid((void *)&m,id));\n'
                # 26A428 keeps the values it was validated with; 26B131 has its own.
                'const uint8_t s0[16]={0x02,0xa9,0xa2,0x02,0x2f,0x39,0x32,0x04,0x86,0xde,0xa0,0x9e,0xa6,0xae,0x8d,0xee};\n'
                'const uint8_t q0[16]={0x0a,0xd3,0x3c,0xc9,0x75,0xa1,0x35,0x9d,0xbc,0x41,0x4e,0xe2,0x5b,0xe4,0xf5,0x99};\n'
                'const uint8_t s1[16]={0x21,0xac,0xab,0xfa,0xb6,0xcc,0x3e,0xc9,0xa3,0xcf,0x6d,0x4d,0x6b,0x63,0x7f,0x5a};\n'
                'const uint8_t q1[16]={0x18,0x5c,0xe9,0x5a,0xbf,0x48,0x3f,0x58,0xa0,0x8a,0x8d,0xc4,0xe5,0x25,0x8b,0x94};\n'
                'const struct skylight_profile *s=skylight_for(s0); assert(s && !strcmp(s->build,"26A428"));\n'
                'assert(s->api_table==0x398e4a40 && s->api_count==14 && s->display_callbacks==0x398d85b0 && s->software_update_return==0x361271);\n'
                'const struct quartzcore_profile *q=quartzcore_for(q0); assert(q && q->inverse_slot==124 && q->update_flags==0x543);\n'
                's=skylight_for(s1); assert(s && !strcmp(s->build,"26B131") && s->api_count==14);\n'
                'q=quartzcore_for(q1); assert(q && !strcmp(q->build,"26B131") && q->inverse_slot==126 && q->update_flags==0x553);\n'
                'assert(!skylight_for(q0) && !quartzcore_for(s0));\n'
                'char t[37]; uuid_text(s1,t); assert(!strcmp(t,"21ACABFA-B6CC-3EC9-A3CF-6D4D6B637F5A"));\n'
                'identify(); assert(!identity.api_table && !identity.display_callbacks && !update_flags); return 0; }\n')
            run('xcrun', 'clang', '-arch', 'x86_64', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                '-framework', 'QuartzCore', src, '-o', tmp/'gate')
            run('codesign', '-s', '-', tmp/'gate')
            run(tmp/'gate')

    def test_profiles_match_derivation(self):
        # WINDOWSERVER_FILTER_DSC="BUILD=/path/dyld_shared_cache_x86_64 ..." re-derives each build's lines
        # from its own SkyLight and QuartzCore (needs ipsw); every line must already be in profiles.h.
        caches = os.environ.get('WINDOWSERVER_FILTER_DSC', '').split()
        if not caches:
            self.skipTest('WINDOWSERVER_FILTER_DSC not set')
        spec = importlib.util.spec_from_file_location('derive', PATCH/'src/derive.py')
        derive = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(derive)
        table = (PATCH/'src/profiles.h').read_text().splitlines()
        for item in caches:
            build, dsc = item.split('=', 1)
            for line in derive.derive(build, dsc=dsc):
                self.assertIn(line, table)
