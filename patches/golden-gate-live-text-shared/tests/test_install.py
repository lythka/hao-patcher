"""src/install.py's checks, on an authored System and framework artifact (no Apple file, nothing installed)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('live_text_install', Path(__file__).resolve().parents[1]/'src/install.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sha = lambda data: hashlib.sha256(data).hexdigest()
METALLIB = ('System/Library/Frameworks/MetalPerformanceShaders.framework/Versions/A/Frameworks/'
            'MPSNDArray.framework/Versions/A/Resources/default.metallib')


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def layout(root):
    system, artifact = root/'System', root/'artifact'
    write(system/m.LIBRARY, b'helloblur')
    write(system/METALLIB, b'apple mpsndarray')
    write(system/m.COMPILER_COMPAT, b'compat')
    runtime = {'LiveText.dylib': b'umbrella', 'detector/model.mil': b'mil', 'MPSNDArray.metallib': b'repaired'}
    for name, data in runtime.items():
        write(artifact/'runtime'/name, data)
    record = dict(compiler_mode='shared', install_root=m.ROOT, load_paths=[m.ROOT + '/LiveText.dylib'],
                  files={k: sha(v) for k, v in runtime.items()},
                  inputs={'MPSNDArray.metallib': dict(source=METALLIB, source_sha256=sha(b'apple mpsndarray'))})
    (artifact/'runtime/build.json').write_text(json.dumps(record))
    write(artifact/'original/libhelloblur.dylib', b'helloblur')
    write(artifact/'replace'/m.LIBRARY, b'helloblur + umbrella')
    framework = dict(unit='tools/live-text', operation='framework', library_path=m.LIBRARY, runtime_root=m.ROOT,
                     umbrella=m.ROOT + '/LiveText.dylib', photos_modified=False, metal_modified=False,
                     source_library_sha256=sha(b'helloblur'), library_sha256=sha(b'helloblur + umbrella'),
                     runtime_manifest_sha256=sha((artifact/'runtime/build.json').read_bytes()))
    (artifact/'build.json').write_text(json.dumps(framework))
    return system, artifact


class InstallTests(unittest.TestCase):
    def check_refuses(self, change, pattern):
        with tempfile.TemporaryDirectory() as tmp:
            system, artifact = layout(Path(tmp))
            change(system, artifact)
            with self.assertRaisesRegex(ValueError, pattern):
                m.check(system, artifact)

    def test_accepts_a_matching_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            system, artifact = layout(Path(tmp))
            record, destination = m.check(system, artifact)
            self.assertEqual(destination, system.resolve()/'usr/lib/hao-live-text')

    def test_refusals(self):
        self.check_refuses(lambda s, a: write(s/m.LIBRARY, b'other'), 'golden-gate-hello')
        self.check_refuses(lambda s, a: write(s/METALLIB, b'26A428 library'), 'MISSING/unsupported')
        self.check_refuses(lambda s, a: (s/m.COMPILER_COMPAT).unlink(), 'metal-compiler-compat')
        self.check_refuses(lambda s, a: write(a/'runtime/detector/model.mil', b'changed'), 'file changed')
        self.check_refuses(lambda s, a: write(a/'runtime/extra', b''), 'file set')
        self.check_refuses(lambda s, a: (s/'usr/lib/hao-live-text').mkdir(), 'exists')
        self.check_refuses(lambda s, a: write(a/'replace'/m.LIBRARY, b'changed'), 'differs')


if __name__ == '__main__':
    unittest.main()
