"""Declaration check for rec-audio: patch.json `files` lists what apply.sh and make.sh put on the Recovery's BaseSystem
(paths relative to its root), and edits/ specs follow the edit-engine format. rec-audio's recipe edits no bytes of a file
(whole files and bundles are copied or built), so there is no byte-equality test and no real-input variable: the
declaration is checked against the recipe's own text instead.
python3.14 -I -B -m unittest discover -s patches/rec-audio/tests"""
import json
from pathlib import Path
import re
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
import spec as S   # noqa: E402

FILES = json.loads((PATCH / 'patch.json').read_text())['files']
PATHS = [f['path'] for f in FILES]


class Declaration(unittest.TestCase):
    def test_every_spec_follows_the_format(self):
        for f in FILES:
            if 'edits' in f:
                spec = S.load(PATCH / f['edits'])
                self.assertEqual(S.validate(spec), [])
                self.assertEqual(spec['path'], f['path'])
        declared = {f['edits'] for f in FILES if 'edits' in f}
        self.assertEqual({'edits/' + p.name for p in (PATCH / 'edits').glob('*.json')} if (PATCH / 'edits').is_dir() else set(), declared)

    def test_kernel_collection_spec_lists_kexts_txt(self):
        spec = S.load(PATCH / 'edits/kc.json')
        ids = (PATCH / 'kexts.txt').read_text().split()
        self.assertEqual(spec['edits'][0]['op'], 'kc.link')
        self.assertEqual(spec['edits'][0]['args']['bundles'], ids)
        self.assertEqual(spec['path'], 'System/Library/KernelCollections/BaseSystemKernelExtensions.kc')

    def test_every_copied_kext_is_declared(self):
        script = (PATCH / 'apply.sh').read_text()
        copied = set(re.findall(r'\$E/(\w+\.kext)', script))
        self.assertTrue(copied)
        self.assertEqual({p for p in PATHS if p.endswith('.kext')}, {'System/Library/Extensions/' + k for k in copied})

if __name__ == '__main__':
    unittest.main()
