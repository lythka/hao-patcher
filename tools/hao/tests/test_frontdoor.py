"""src/frontdoor.py: where hao runs, what `hao` alone lists, a command in the wrong place, and exit codes."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import console as CON      # noqa: E402
import frontdoor as FD     # noqa: E402


class Pipe(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return False


class Where(unittest.TestCase):
    @patch.object(FD, "translated", return_value=False)
    def test_places(self, _translated):
        none = Path('/nonexistent/record.json')
        self.assertEqual(FD.where({'HAO_STICK': '/Volumes/HAO'}, top='/x', record=none), 'installer')
        self.assertEqual(FD.where({}, top='/x', record=none, mac='27.2', machine='x86_64'), 'installed')
        self.assertEqual(FD.where({}, top='/x', record=none, mac='27.2', machine='arm64'), 'build')
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(FD.where({}, top='/x', record=Path(d), mac='26.7', machine='arm64'), 'build')

    @patch.object(FD, 'translated', return_value=True)
    def test_rosetta_is_build_host(self, _translated):
        self.assertEqual(FD.where({}, top='/x', record=Path('/nonexistent'),
                                  mac='27.2', machine='x86_64'), 'build')
        with tempfile.NamedTemporaryFile() as record:
            self.assertEqual(FD.where({}, top='/x', record=Path(record.name),
                                      mac='27.2', machine='x86_64'), 'installed')

    def test_wrong_place(self):
        self.assertIsNone(FD.wrong_place('create-installer', 'build'))
        self.assertIsNone(FD.wrong_place('fetch', 'installer'))                  # developer commands run anywhere
        self.assertEqual(FD.wrong_place('install', 'build'),
                         'hao install runs from the USB installer; on this Mac (the build Mac), run hao create-installer')
        self.assertIn('runs from any Mac on its own macOS (the build Mac)', FD.wrong_place('create-installer', 'installer'))


class Listing(unittest.TestCase):
    def test_lists_the_commands_for_the_place(self):
        out = Pipe()
        FD.listing(CON.Console('steps', out=out, env={}), 'installer', versions={'hao': 'v0.0.1', 'macos': '26.7'})
        text = out.getvalue()
        self.assertIn('hao v0.0.1  macOS 26.7 · the USB installer', text)
        self.assertIn('  install ', text)
        self.assertIn('  report ', text)
        self.assertNotIn('create-installer', text)
        self.assertNotIn('Developer commands', text)

    def test_all_adds_developer_commands(self):
        out = Pipe()
        FD.listing(CON.Console('steps', out=out, env={}), 'build', all_=True)
        self.assertIn('Developer commands', out.getvalue())
        self.assertIn('  build-image ', out.getvalue())


class Codes(unittest.TestCase):
    def test_codes(self):
        S = CON.Stopped
        self.assertEqual(FD.code_of(S('x', code=4)), 4)
        self.assertEqual(FD.code_of(S('not confirmed')), 1)
        self.assertEqual(FD.code_of(S('not enough disk space to assemble the image: 20 GB free')), 3)
        self.assertEqual(FD.code_of(S('/dev/disk4 may not be erased: it is internal')), 3)
        self.assertEqual(FD.code_of(S('an input is missing or has the wrong sha256')), 4)
        self.assertEqual(FD.code_of(S('hao create-installer needs administrator rights to erase the USB stick')), 5)
        self.assertEqual(FD.code_of(S('this Mac cannot build onto a disk: not running as root')), 5)
        self.assertEqual(FD.code_of(S('writing the USB stick failed')), 1)


if __name__ == '__main__':
    unittest.main()
