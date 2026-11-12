"""edits/system-sb.json declares what src/disk_space_rule.py does: the engine writes the same bytes, and leaves a
system.sb that already has the rule unchanged. python3.14 -I -B -m unittest discover -s patches/darwin27-sandbox-disk-space/tests

SANDBOX_SYSTEM_SB_WITHOUT=PATH (a system.sb without the rule, e.g. Tahoe 25G332's) and SANDBOX_SYSTEM_SB_WITH=PATH
(one with it, e.g. 26B131's) add the real files."""
import os
from pathlib import Path
import runpy
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
import engine   # noqa: E402
import spec as S   # noqa: E402

RULE = runpy.run_path(str(PATCH / 'src/disk_space_rule.py'))
SPEC = S.load(PATCH / 'edits/system-sb.json')


class Declaration(unittest.TestCase):
    def test_spec_carries_the_scripts_own_strings(self):
        self.assertEqual(S.validate(SPEC), [])
        args = SPEC['edits'][0]['args']
        self.assertEqual((args['after'], args['text']), (RULE['ANCHOR'], RULE['RULE']))

    def same(self, text):
        out, record = engine.apply(SPEC, text.encode())
        self.assertEqual(out.decode(), RULE['add_rule'](text))
        return record

    def test_synthetic_without_and_with_the_rule(self):
        text = '(version 1)\n' + RULE['ANCHOR'] + '(deny default)\n'
        self.same(text)
        self.assertEqual(self.same(RULE['add_rule'](text))['edits'][0].get('skipped'), 'already present in this file')

    @unittest.skipUnless(os.environ.get('SANDBOX_SYSTEM_SB_WITHOUT'), 'set SANDBOX_SYSTEM_SB_WITHOUT')
    def test_real_file_without_the_rule(self):
        record = self.same(Path(os.environ['SANDBOX_SYSTEM_SB_WITHOUT']).read_text())
        self.assertEqual(record['expected'], 'match')

    @unittest.skipUnless(os.environ.get('SANDBOX_SYSTEM_SB_WITH'), 'set SANDBOX_SYSTEM_SB_WITH')
    def test_real_file_with_the_rule_is_unchanged(self):
        self.same(Path(os.environ['SANDBOX_SYSTEM_SB_WITH']).read_text())


if __name__ == '__main__':
    unittest.main()
