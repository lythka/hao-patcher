"""The repository is public: no tracked file may point into the maintainers' private repositories or home folders."""
from pathlib import Path
import re
import subprocess
import unittest

TOP = Path(__file__).resolve().parents[2]

# Built from parts so this file does not match itself.
PATTERNS = [
    ('private research repository', re.compile(re.escape('hao-' 'research'))),
    ('private evidence folder', re.compile(re.escape('store/' 'evidence'))),
    ('archived private tooling', re.compile(re.escape('ssd-tools-' 'archive'))),
    # A literal home folder. /Users/Shared and computed paths (/Users/$u, /Users/{name}, "/Users/") are macOS's own.
    ('home folder', re.compile('/' r'Users/(?!Shared\b)[A-Za-z]')),
]

# path -> (pattern names allowed there, why). Add a file only with the reason it is not private.
ALLOWED = {
    'tools/qemu-vm/qemu-vm': ({'private evidence folder'},
                              "the repository's own git-ignored store/ folder, where QEMU runs keep their evidence"),
    'cache/seeds/tests/test_seeds.py': ({'home folder'}, 'fixture paths of the harvest account benchtest'),
    'patches/golden-gate-font-registry/README.md': ({'home folder'}, 'names the harvest account benchtest'),
}


class PrivateReferences(unittest.TestCase):
    def test_no_tracked_file_names_private_locations(self):
        files = subprocess.run(['git', '-C', str(TOP), 'ls-files', '-z'], capture_output=True, check=True).stdout
        found = []
        for name in filter(None, files.decode().split('\0')):
            path = TOP/name
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if b'\0' in data[:8192]:
                continue   # binary
            text = data.decode('utf-8', 'replace')
            allowed = ALLOWED.get(name, (set(), ''))[0]
            for label, pattern in PATTERNS:
                if label in allowed:
                    continue
                for match in pattern.finditer(text):
                    found.append('%s:%d: %s' % (name, text.count('\n', 0, match.start())+1, label))
        self.assertEqual([], found, 'private references in tracked files (fix them, or allow-list with a reason)')

    def test_allow_list_names_tracked_files(self):
        files = set(subprocess.run(['git', '-C', str(TOP), 'ls-files'], capture_output=True, text=True,
                                   check=True).stdout.split('\n'))
        self.assertEqual([], [name for name in ALLOWED if name not in files])
        self.assertTrue(all(why for _, why in ALLOWED.values()))


if __name__ == '__main__':
    unittest.main()
