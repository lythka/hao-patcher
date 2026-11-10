import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('copy_hooks', Path(__file__).resolve().parents[1]/'stages/copy-hooks.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CopyHooksTest(unittest.TestCase):
    def test_only_declared_input_stays_on_share(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root/'share', root/'local'
            hook = source/'02-test'
            (hook/'inputs/images').mkdir(parents=True)
            (hook/'inputs/native').mkdir()
            image = hook/'inputs/images/source.dmg'
            image.write_bytes(b'immutable image')
            stamp = image.stat().st_mtime_ns
            (hook/'inputs/native/kext').write_bytes(b'owned payload')
            (hook/'read-from-share.txt').write_text('# image input\nimages\n')
            (hook/'kc-hook.sh').write_text('recipe')
            module.copy_hooks(source, target)
            shared = target/'02-test/inputs/images'
            self.assertTrue(shared.is_symlink())
            self.assertEqual((shared/'source.dmg').read_bytes(), b'immutable image')
            self.assertEqual(image.stat().st_mtime_ns, stamp)
            self.assertFalse((target/'02-test/inputs/native').is_symlink())
            self.assertEqual((target/'02-test/inputs/native/kext').read_bytes(), b'owned payload')
            self.assertEqual((target/'02-test/kc-hook.sh').read_text(), 'recipe')
