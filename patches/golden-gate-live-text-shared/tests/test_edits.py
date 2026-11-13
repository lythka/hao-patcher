"""edits/libhelloblur.json declares what src/install.py installs on the System: the runtime from the row, and
libhelloblur with one more load command (tools/live-text/lib/framework.py attach(), built by tools/live-text).
python3.14 -I -B -m unittest discover -s patches/golden-gate-live-text-shared/tests

The existing code edits the load commands in dylib mode (append_dylibs(resign_filetype=6)): it writes
LC_LOAD_DYLIB (not weak) with timestamp 0 and versions 0, leaves the old signature invalid and lets the caller sign.
framework.build() then runs `codesign -f -s - --preserve-metadata=identifier,entitlements,flags,runtime`. The spec
is the same load command and `sign` adhoc with the library's own identifier (local.libhelloblur).
Always runs on a synthetic dylib. Real input, set one variable each:
  LIVE_TEXT_SHARED_INPUT=PATH      libhelloblur.dylib (row libhelloblur, sha256 d471519b...)
  LIVE_TEXT_SHARED_FRAMEWORK=PATH  the framework/ folder of the tools/live-text build (row live_text_framework);
                                   its replace/usr/lib/libhelloblur.dylib is the signed reference output
The signed comparison also needs macOS codesign (skipped without it)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
sys.path.insert(0, str(TOP / 'tools/edit-engine/tests'))
sys.path.insert(0, str(TOP / 'tools/live-text/lib'))
import engine   # noqa: E402
import spec as S   # noqa: E402
import fixture   # noqa: E402
import macho_edit   # noqa: E402

SPEC = S.load(PATCH / 'edits/libhelloblur.json')
UMBRELLA = '/usr/lib/hao-live-text/LiveText.dylib'
FILES = json.loads((PATCH / 'patch.json').read_text())['files']


def script_output(data):
    """What framework.attach() does: the load command appended, the signature left for codesign."""
    return macho_edit.append_dylibs(data, [UMBRELLA], resign_filetype=6)[0]


class Declaration(unittest.TestCase):
    def test_spec_follows_the_format(self):
        self.assertEqual(S.validate(SPEC), [])
        self.assertEqual(SPEC['path'], 'usr/lib/libhelloblur.dylib')
        entry = next(f for f in FILES if f['path'] == SPEC['path'])
        self.assertEqual(entry['edits'], 'edits/libhelloblur.json')
        self.assertEqual(entry['install'], {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']})   # as the hook installs it
        for f in FILES:
            if 'origin' in f:
                self.assertEqual(f['origin']['row'], 'live_text_framework')
                self.assertTrue(f['path'].startswith('usr/lib/hao-live-text/'))

    def test_same_bytes_as_attach_on_a_synthetic_libhelloblur(self):
        library = fixture.dylib(size=0x3000, text_at=0x400, text_size=0x1000, ident=b'/usr/lib/libhelloblur.dylib')
        out, record = engine.apply(SPEC, library, repo=str(TOP))
        self.assertEqual(out, script_output(library))
        self.assertEqual(record['edits'][0]['summary'].split()[0], 'macho.add-dylib')

    @unittest.skipUnless(os.environ.get('LIVE_TEXT_SHARED_INPUT'), 'set LIVE_TEXT_SHARED_INPUT to libhelloblur.dylib')
    def test_same_bytes_as_attach_on_the_real_libhelloblur(self):
        data = Path(os.environ['LIVE_TEXT_SHARED_INPUT']).read_bytes()
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, script_output(data))
        self.assertEqual(record['expected'], 'match')

    @unittest.skipUnless(os.environ.get('LIVE_TEXT_SHARED_FRAMEWORK') and shutil.which('codesign'),
                         'set LIVE_TEXT_SHARED_FRAMEWORK to the framework/ folder (and run on macOS)')
    def test_signed_bytes_equal_the_built_artifacts_libhelloblur(self):
        framework = Path(os.environ['LIVE_TEXT_SHARED_FRAMEWORK'])
        out, _ = engine.apply(SPEC, (framework / 'original/libhelloblur.dylib').read_bytes(), repo=str(TOP))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'libhelloblur.dylib'
            path.write_bytes(out)
            subprocess.run(['codesign', '-f', '-s', '-', '-i', SPEC['sign']['identifier'], str(path)],
                           check=True, capture_output=True)
            self.assertEqual(path.read_bytes(), (framework / 'replace/usr/lib/libhelloblur.dylib').read_bytes())

    @unittest.skipUnless(os.environ.get('LIVE_TEXT_SHARED_FRAMEWORK'), 'set LIVE_TEXT_SHARED_FRAMEWORK')
    def test_declared_runtime_files_are_exactly_the_runtime_folder(self):
        folder = Path(os.environ['LIVE_TEXT_SHARED_FRAMEWORK']) / 'runtime'
        installed = {'usr/lib/hao-live-text/' + str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file()}
        declared = {f['path'] for f in FILES if 'origin' in f}
        self.assertEqual(declared, installed)
        manifest = json.loads((folder / 'build.json').read_text())
        self.assertEqual(installed, {'usr/lib/hao-live-text/' + n for n in manifest['files']} |
                         {'usr/lib/hao-live-text/build.json'})


if __name__ == '__main__':
    unittest.main()
