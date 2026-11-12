"""patch.json "files" declares what apply.sh installs: every declared path is installed by apply.sh from the row or
src file the declaration names, and apply.sh installs nothing else.
python3.14 -I -B -m unittest discover -s patches/dev-access/tests"""
import json
from pathlib import Path
import re
import unittest

PATCH = Path(__file__).resolve().parents[1]
PJ = json.loads((PATCH / 'patch.json').read_text())
APPLY = (PATCH / 'apply.sh').read_text()
SOURCES = {'private/var/root/.testssh/sshd_config': '$P/src/sshd_config',
           'Library/LaunchDaemons/local.testssh.plist': '$P/src/local.testssh.plist'}


class Declaration(unittest.TestCase):
    def test_every_installed_file_is_declared_from_its_source(self):
        installs = {}
        for m in re.finditer(r'^install -o root -g wheel -m \d+ (\S+) (\S+)$', APPLY, re.M):
            dest = m.group(2).replace('$T', '$DATA/private/var/root/.testssh')
            self.assertTrue(dest.startswith('$DATA/'), dest)
            installs[dest[len('$DATA/'):]] = m.group(1)
        declared = {f['path']: f['origin'] for f in PJ['files']}
        self.assertEqual(set(installs), set(declared))
        for path, source in installs.items():
            origin = declared[path]
            self.assertEqual(origin['kind'], 'hao')
            if 'row' in origin:
                self.assertEqual(source, '$P/inputs/' + origin['row'])
                self.assertIn(origin['row'], PJ['inputs'])
            else:
                self.assertEqual(source, SOURCES[path])
                self.assertTrue((PATCH / source.replace('$P/', '')).is_file())
        self.assertEqual({o['row'] for o in declared.values() if 'row' in o}, set(PJ['inputs']))


if __name__ == '__main__':
    unittest.main()
