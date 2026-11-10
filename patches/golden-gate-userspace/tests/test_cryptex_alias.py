from pathlib import Path
import subprocess
import tempfile
import unittest

RECIPE = Path(__file__).resolve().parents[1]/'src/cryptex-cache-alias.sh'

class CacheAliasTest(unittest.TestCase):
    def test_same_cache_identity_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temporary:
            pre = Path(temporary)
            cache = pre/'Cryptexes/OS/System/Library/dyld/cache'
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b'cache')
            for _ in range(2):
                subprocess.run(['sh', str(RECIPE), str(pre)], check=True)
            alias = pre/'Cryptexes/Rosetta/System/Library/dyld/cache'
            self.assertTrue(alias.samefile(cache))

    def test_existing_rosetta_directory_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            pre = Path(temporary)
            real = pre/'Cryptexes/Rosetta/System/Library/dyld'
            real.mkdir(parents=True)
            result = subprocess.run(['sh', str(RECIPE), str(pre)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(real.is_dir())
            self.assertFalse(real.is_symlink())
