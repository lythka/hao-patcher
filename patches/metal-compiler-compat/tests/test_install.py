"""src/install.py's checks, on an authored System and artifact (no Apple file, nothing installed)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('compiler_compat_install', Path(__file__).resolve().parents[1]/'src/install.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sha = lambda data: hashlib.sha256(data).hexdigest()
CONTENTS = Path(m.SERVICE).parent.parent


def layout(root):
    system, artifact = root/'System', root/'artifact'
    originals = {m.SERVICE: b'service', str(CONTENTS/'Info.plist'): b'info',
                 str(CONTENTS/'_CodeSignature/CodeResources'): b'resources'}
    for name, data in originals.items():
        (system/name).parent.mkdir(parents=True, exist_ok=True)
        (system/name).write_bytes(data)
    files = {m.SERVICE: b'repaired service', m.LIBRARY: b'compat'}
    for name, data in files.items():
        (artifact/'replace'/name).parent.mkdir(parents=True, exist_ok=True)
        (artifact/'replace'/name).write_bytes(data)
    record = dict(unit='tools/live-text', operation='compiler-service', service=m.SERVICE,
                  source_service_sha256=sha(b'service'), source_info_sha256=sha(b'info'),
                  source_resources_sha256=sha(b'resources'), files={k: sha(v) for k, v in files.items()})
    (artifact/'build.json').write_text(json.dumps(record))
    return system, artifact


class InstallTests(unittest.TestCase):
    def test_accepts_the_matching_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            system, artifact = layout(Path(tmp))
            self.assertEqual(m.check(system, artifact)['service'], m.SERVICE)

    def test_refuses_another_service_or_changed_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            system, artifact = layout(Path(tmp))
            (system/CONTENTS/'Info.plist').write_bytes(b'other')
            with self.assertRaisesRegex(ValueError, 'MISSING/unsupported'):
                m.check(system, artifact)
        with tempfile.TemporaryDirectory() as tmp:
            system, artifact = layout(Path(tmp))
            (artifact/'replace'/m.LIBRARY).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'differs'):
                m.check(system, artifact)
        with self.assertRaises(ValueError):
            m.check('/', '/nonexistent')


if __name__ == '__main__':
    unittest.main()
