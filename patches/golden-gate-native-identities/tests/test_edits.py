"""patch.json "files" declares what native_identities.py install writes: the list, the record and, for the three
programs the README expects, the originals under usr/lib/hao/originals. The declared paths come from the script's own
constants. The files are generated or copied whole, so there is no edit spec and no byte comparison.
python3.14 -I -B -m unittest discover -s patches/golden-gate-native-identities/tests"""
import json
from pathlib import Path
import runpy
import unittest

PATCH = Path(__file__).resolve().parents[1]
SCRIPT = runpy.run_path(str(PATCH / 'src/native_identities.py'))
FILES = {f['path']: f['origin'] for f in json.loads((PATCH / 'patch.json').read_text())['files']}
SUPPORT = ('System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/'
           'Versions/A/Support/')
WINDOWSERVER = 'System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer'


class Declaration(unittest.TestCase):
    def test_list_and_record_are_the_scripts_paths(self):
        self.assertEqual(FILES[SCRIPT['LIST']]['kind'], 'hao')
        self.assertEqual(FILES[SCRIPT['RECORD']]['kind'], 'hao')

    def test_originals_sit_where_the_script_puts_them(self):
        o = SCRIPT['ORIGINALS'] + '/'
        self.assertEqual({p for p in FILES if p.startswith(o)},
                         {o + WINDOWSERVER, o + SUPPORT + 'corespotlightd', o + SUPPORT + 'managedcorespotlightd'})
        self.assertEqual(FILES[o + WINDOWSERVER]['kind'], 'tahoe')
        self.assertEqual(FILES[o + SUPPORT + 'corespotlightd']['kind'], 'apple')
        self.assertEqual(FILES[o + SUPPORT + 'managedcorespotlightd']['kind'], 'apple')
        self.assertEqual(len(FILES), 5)

    def test_made_after_every_overlay_is_installed(self):
        """The list is keyed on the CDHashes of the installed programs, so every file is generated on the target
        after the overlays are in place (no row: the materialiser defers them and orders them last)."""
        entries = json.loads((PATCH / 'patch.json').read_text())['files']
        self.assertEqual({tuple(f.get('after', [])) for f in entries}, {('overlays',)})
        self.assertFalse(any(f['origin'].get('row') for f in entries))
        self.assertIn('kc-hook.sh', [p.name for p in (PATCH / 'src').iterdir()])


if __name__ == '__main__':
    unittest.main()
