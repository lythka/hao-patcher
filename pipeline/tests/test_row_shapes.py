"""A recipe that reads a file inside one of its inputs ("$P/inputs/ROW/...") needs ROW to be a folder row ("- PATH"):
a file row stages the file itself as inputs/ROW and the recipe fails late, inside the builder (2026-11-11: a file row
for shader_caches_golden_gate stopped an image build after 70 minutes)."""
import os
import re
import unittest

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INPUTS = os.path.join(TOP, 'pipeline', 'inputs')


def rows_read_as_folders():
    """{row: [script, ...]} for every '$P/inputs/ROW/' or '$A/...' (A="$P/inputs/ROW") use in a recipe script."""
    out = {}
    patches = os.path.join(TOP, 'patches')
    for name in sorted(os.listdir(patches)):
        for d, _, files in os.walk(os.path.join(patches, name)):
            if os.sep + 'tests' in d or os.sep + 'build' in d:
                continue
            for f in files:
                if not f.endswith(('.sh', '.zsh')):
                    continue
                p = os.path.join(d, f)
                text = open(p, errors='replace').read()
                rows = set(re.findall(r'\$\{?P\}?/inputs/([A-Za-z0-9_]+)/', text))
                for var, row in re.findall(r'^\s*([A-Za-z_][A-Za-z0-9_]*)="?\$\{?P\}?/inputs/([A-Za-z0-9_]+)"?\s*$', text, re.M):
                    if re.search(r'\$\{?%s\}?/' % re.escape(var), text):
                        rows.add(row)
                for row in rows:
                    out.setdefault(row, []).append(os.path.relpath(p, TOP))
    return out


def row_files(row):
    for d, _, files in os.walk(INPUTS):
        if row in files:
            yield os.path.join(d, row)


class RowShapes(unittest.TestCase):
    def test_rows_read_as_folders_are_folder_rows(self):
        bad = []
        for row, scripts in rows_read_as_folders().items():
            for f in row_files(row):
                lines = [l for l in open(f).read().splitlines() if l.strip() and not l.lstrip().startswith('#')]
                if lines and not lines[0].startswith('- '):
                    bad.append('%s: %s (a file row) but %s read files inside it'
                               % (os.path.relpath(f, TOP), lines[0][:80], ', '.join(scripts)))
        self.assertEqual(bad, [])

    def test_scan_finds_the_seed_rows(self):
        rows = rows_read_as_folders()
        for row in ('shader_caches_golden_gate', 'icon_caches_golden_gate', 'font_registry_golden_gate'):
            self.assertIn(row, rows)


if __name__ == '__main__':
    unittest.main()
