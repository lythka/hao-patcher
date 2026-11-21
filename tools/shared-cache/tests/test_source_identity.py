from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dyld_rebuild.source_identity import head, content_hash


class SourceIdentityTests(unittest.TestCase):
    def test_worktree_head_and_content_identity_need_no_git_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);repo=base/'repo';repo.mkdir()
            common=base/'admin';admin=common/'worktrees/task';admin.mkdir(parents=True)
            (repo/'.git').write_text('gitdir: '+str(admin)+'\n')
            (admin/'commondir').write_text('../..\n')
            (admin/'HEAD').write_text('ref: refs/heads/task\n')
            (common/'packed-refs').write_text('a'*40+' refs/heads/task\n')
            source=repo/'source';source.mkdir();(source/'make').write_text('first')
            self.assertEqual(head(source),'a'*40)
            before=content_hash(source)
            cache=source/'__pycache__';cache.mkdir();(cache/'module.pyc').write_bytes(b'ignored')
            self.assertEqual(content_hash(source),before)
            (source/'make').write_text('changed')
            self.assertNotEqual(content_hash(source),before)
            (admin/'HEAD').write_text('b'*40+'\n')
            self.assertEqual(head(source),'b'*40)
            self.assertIsNone(head(base/'not-a-checkout'))
