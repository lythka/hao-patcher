"""Every relative link in a tracked Markdown file points at a file that exists, and at a heading that exists.

A reader follows these links from the GitHub page and from the checkout; a link that survives only in the author's
head costs them the same either way. This test is the reader's check, run by the push gate with the rest of
`pipeline/tests`.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from urllib.parse import unquote

TOP = Path(__file__).resolve().parents[2]

# [text](target), and ![alt](target), whose target is the part in the parentheses. A target may be <wrapped> and may
# carry a title ("...").
LINK = re.compile(r'\[(?:[^\]\\]|\\.)*\]\(\s*(<[^<>]*>|[^)\s]+)(?:\s+"[^"]*")?\s*\)')
FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})')
HEADING = re.compile(r'^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$')
CODE = re.compile(r'`+[^`]*`+')
ABSOLUTE = ('http://', 'https://', 'mailto:', 'tel:', 'file:')


def slug(text):
    """GitHub's anchor for a heading: lowercase, drop punctuation, spaces become hyphens."""
    text = re.sub(r'`+([^`]*)`+', r'\1', text)               # inline code keeps its text
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)      # a link keeps its text
    text = re.sub(r'[*_~]', '', text)
    return ''.join('-' if c == ' ' else c for c in text.strip().lower()
                   if c.isalnum() or c in '-_ ')


def anchors(path):
    """The anchors GitHub gives PATH's headings; a repeated heading gets -1, -2, ..."""
    seen, out, fence = {}, set(), None
    for line in path.read_text(errors='replace').splitlines():
        marker = FENCE.match(line)
        if marker:
            fence = None if fence else marker.group(1)[0]
            continue
        if fence:
            continue
        m = HEADING.match(line)
        if not m:
            continue
        base = slug(m.group(1))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if not n else '%s-%d' % (base, n))
    return out


def tracked_markdown():
    names = subprocess.run(['git', '-C', str(TOP), 'ls-files', '-z'],
                           capture_output=True, check=True).stdout.decode()
    return [n for n in names.split('\0') if n.endswith('.md')]


def links(path):
    """(line number, target) for each link outside a fenced code block and outside inline code."""
    fence = None
    for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
        marker = FENCE.match(line)
        if marker:
            fence = None if fence else marker.group(1)[0]
            continue
        if fence:
            continue
        for m in LINK.finditer(CODE.sub(' ', line)):
            target = m.group(1)
            if target.startswith('<') and target.endswith('>'):
                target = target[1:-1]
            if target and not target.startswith(ABSOLUTE):
                yield number, target


class DocLinks(unittest.TestCase):
    def test_relative_links_resolve(self):
        bad, known = [], {}
        for name in tracked_markdown():
            path = TOP / name
            if path.is_symlink() or not path.is_file():
                continue
            for number, target in links(path):
                filepart, _, anchor = target.partition('#')
                if filepart:
                    dest = (path.parent / unquote(filepart)).resolve()
                    if not dest.exists():
                        bad.append('%s:%d: %s (no such file)' % (name, number, target))
                        continue
                    if dest.suffix != '.md':
                        continue
                else:
                    dest = path
                if dest not in known:
                    known[dest] = anchors(dest)
                if anchor and unquote(anchor).lower() not in known[dest]:
                    bad.append('%s:%d: %s (no such heading)' % (name, number, target))
        self.assertEqual([], bad, 'broken links in tracked Markdown (fix them)')


class Slugs(unittest.TestCase):
    def test_slug_follows_github(self):
        self.assertEqual('hao-verify-release', slug('`hao verify-release`'))
        self.assertEqual('the-builder-path', slug('The builder path'))
        self.assertEqual('release-v001', slug('Release v0.0.1'))
        self.assertEqual('what-is-ab-c', slug('What is a/b (c)?'))
        self.assertEqual('café', slug('Café'))
        self.assertEqual('a-link-and-code', slug('A [link](../x.md) and `code`'))

    def test_repeated_headings_are_numbered(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'x.md'
            path.write_text('# Same\n\n## Same\n\n```\n# Not a heading\n```\n\n### Same\n')
            self.assertEqual({'same', 'same-1', 'same-2'}, anchors(path))


if __name__ == '__main__':
    unittest.main()
