"""patch.json "files" lists what apply.sh installs: every declared path is a destination in apply.sh, and every
file it copies from src/ or builds with make.sh is declared. These recipes edit no bytes, so there is no edit spec
and no byte-equality test; the text of apply.sh and make.sh is the only reference that can run on the host.
python3.14 -I -B -m unittest discover -s patches/rec-power/tests"""
import json
from pathlib import Path
import re
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'pipeline'))
import patches   # noqa: E402

FILES = json.loads((PATCH / 'patch.json').read_text())['files']
APPLY = (PATCH / 'apply.sh').read_text()


class Declaration(unittest.TestCase):
    def test_declaration_has_no_problems(self):
        self.assertEqual(patches.file_problems(str(PATCH), json.loads((PATCH / 'patch.json').read_text())), [])
        self.assertTrue(all('origin' in f for f in FILES))

    def test_every_declared_file_is_installed_by_apply_sh(self):
        for f in FILES:
            if f['path'].startswith('usr/local/ssdtools/bin/'):
                self.assertIn('$BS/usr/local/ssdtools/bin/', APPLY)
            elif f['path'].startswith('usr/local/ssdtools/'):
                self.assertIn('$BS/usr/local/ssdtools/', APPLY)
                self.assertIn(f['path'].rsplit('/', 1)[1], APPLY)
            elif f['path'].startswith('System/Library/LaunchDaemons/'):
                self.assertIn('$BS/' + f['path'], APPLY)
            elif f['path'].endswith('IO80211.framework'):
                self.assertIn('t=' + f['path'], APPLY)

    def test_every_copied_or_built_file_is_declared(self):
        declared = {f['path'].rsplit('/', 1)[1] for f in FILES}
        installed = set()
        for m in re.finditer(r'\$P/(?:src|build)/([^\s"]+)', APPLY):
            installed.add(m.group(1))
        for name in installed:
            self.assertIn(name, declared)
            self.assertTrue((PATCH / 'src' / name).is_file() or (PATCH / 'make.sh').is_file(), name)


if __name__ == '__main__':
    unittest.main()
