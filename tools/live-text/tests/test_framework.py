"""lib/framework.py: the libhelloblur edit and the runtime manifest checks, on authored fixtures."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import common  # noqa: F401  (puts lib/ on sys.path)
import framework
import runtime
from test_macho_edit import fixture

ROOT = '/usr/lib/hao-live-text'


def helloblur():
    source = bytearray(fixture())
    struct.pack_into('<I', source, 12, 6)
    name = b'/usr/lib/libhelloblur.dylib\0'
    size = (24 + len(name) + 7) & ~7
    struct.pack_into('<II', source, 16, 4, 200 + size)
    struct.pack_into('<IIIIII', source, 232, 0xd, size, 24, 0, 0, 0)
    source[256:256 + len(name)] = name
    return source, size


class FrameworkTests(unittest.TestCase):
    def test_appends_only_the_umbrella_and_keeps_existing_commands(self):
        source, size = helloblur()
        changed, report = framework.attach(bytes(source), ROOT + '/' + runtime.UMBRELLA)
        self.assertEqual(source[32:232 + size], changed[32:232 + size])
        self.assertEqual(source[4096:], changed[4096:])
        self.assertEqual(report['added_dependencies'], [ROOT + '/' + runtime.UMBRELLA])
        source[256] ^= 1
        with self.assertRaisesRegex(ValueError, 'install name'):
            framework.attach(bytes(source), ROOT + '/' + runtime.UMBRELLA)

    def test_umbrella_fits_libhelloblur_header_padding(self):
        """libhelloblur d471519b (row libhelloblur) has 64 zero bytes between its load commands and __text."""
        name = (ROOT + '/' + runtime.UMBRELLA).encode() + b'\0'
        self.assertLessEqual((24 + len(name) + 7) & ~7, 64)
        source, size = helloblur()
        end = 32 + 200 + size
        source[end + 64:end + 72] = b'\xff' * 8
        framework.attach(bytes(source), ROOT + '/' + runtime.UMBRELLA)
        with self.assertRaisesRegex(ValueError, 'header padding'):
            framework.attach(bytes(source), ROOT + '/libLiveText.dylib')

    def test_runtime_manifest_roots_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            library = folder/runtime.UMBRELLA
            library.write_bytes(b'authored')
            record = dict(unit='tools/live-text', operation='runtime', compiler_mode='shared', install_root=ROOT,
                          load_paths=[ROOT + '/' + runtime.UMBRELLA], files={runtime.UMBRELLA: common.sha(library)})
            write = lambda: (folder/'build.json').write_text(json.dumps(record))
            write()
            self.assertEqual(runtime.verify(folder)['install_root'], ROOT)
            for bad in ['usr/lib/x', '/usr/lib/../x', '/usr/lib//x', '/usr/lib/x/', '/usr/lib/x:y']:
                record.update(install_root=bad, load_paths=[bad + '/' + runtime.UMBRELLA])
                write()
                with self.assertRaises(ValueError, msg=bad):
                    runtime.verify(folder)
            record.update(install_root=ROOT, load_paths=[ROOT + '/' + runtime.UMBRELLA])
            write()
            (folder/'extra').write_bytes(b'')
            with self.assertRaisesRegex(ValueError, 'file set'):
                runtime.verify(folder)
            (folder/'extra').unlink()
            library.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'file changed'):
                runtime.verify(folder)


if __name__ == '__main__':
    unittest.main()
