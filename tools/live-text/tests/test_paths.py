"""Build records name their inputs as the repository does, never by an absolute (home-folder) path."""
from pathlib import Path
import tempfile
import unittest

import common  # noqa: F401  (puts lib/ on sys.path)
import detector
import paths


class RepoPath(unittest.TestCase):
    def test_inside_the_repository_and_its_store(self):
        self.assertEqual('tools/live-text/lib/paths.py', paths.repo_path(paths.REPO/'tools/live-text/lib/paths.py'))
        self.assertEqual('store/artifacts/x/y', paths.repo_path(paths.REPO/'store/artifacts/x/y'))

    def test_outside_by_name_only(self):
        with tempfile.TemporaryDirectory() as scratch:
            self.assertEqual('input.dylib', paths.repo_path(Path(scratch)/'input.dylib'))

    def test_detector_record(self):
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch)/'cr_td_model_v3_e5.mlmodelc.bundle'
            source.mkdir()
            (source/'model.mil').write_text('%s\n%s\n' % (detector.PIXEL_INPUT, detector.PIXEL_TO_TENSOR))
            record = detector.prepare(source, Path(scratch)/'out')
            self.assertEqual('cr_td_model_v3_e5.mlmodelc.bundle', record['path'])


if __name__ == '__main__':
    unittest.main()
