import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dyld_rebuild import artifacts
from dyld_rebuild.exports import Refusal


class ArtifactTests(unittest.TestCase):
    def test_absence_requires_explicit_developer_flag_and_corruption_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            root = Path(temp)
            with patch.object(artifacts, 'REPO', root):
                with self.assertRaisesRegex(Refusal, 'missing pinned helper'):
                    artifacts.helper('probe')
                with patch.dict(os.environ, {'SHARED_CACHE_DEVELOPER_BUILD': '1'}):
                    self.assertIsNone(artifacts.helper('probe'))
                    rows = root/'pipeline/inputs/common';rows.mkdir(parents=True)
                    binary = root/'probe';binary.write_bytes(b'expected');binary.chmod(0o755)
                    (rows/'shared_cache_probe').write_text(hashlib.sha256(b'expected').hexdigest()+' probe\n')
                    copy = artifacts.helper('probe', root/'copy/probe')
                    self.assertEqual(copy.read_bytes(), b'expected')
                    self.assertTrue(os.access(copy, os.X_OK))
                    binary.unlink()
                    with self.assertRaisesRegex(Refusal, 'missing artifact'):
                        artifacts.helper('probe')
                    binary.write_bytes(b'corrupt')
                    with self.assertRaisesRegex(Refusal, 'SHA256 mismatch'):
                        artifacts.helper('probe')
                    (rows/'shared_cache_probe').write_text('- probe\n')
                    with self.assertRaisesRegex(Refusal, 'unpinned'):
                        artifacts.helper('probe')
