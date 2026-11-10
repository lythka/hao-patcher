"""Run: /opt/homebrew/bin/python3.14 -I -m unittest discover -s patches/darwin27-sandbox-disk-space/tests"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('disk_space_rule', PATCH/'src/disk_space_rule.py')
rule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rule)
TAHOE = PATCH.parents[1]/'store/inputs/25G332/unpack/tree/System/Library/Sandbox/Profiles/system.sb'
HOST = Path('/System/Library/Sandbox/Profiles/system.sb')

HEAD = '(version 1)\n\n(allow sysctl-read)\n\n'
TAIL = '\n;;; (system-graphics) - Allow access to graphics hardware.\n(define (system-graphics))\n'


class Rule(unittest.TestCase):
    def test_inserts_after_libmalloc_block(self):
        out = rule.add_rule(HEAD+rule.ANCHOR+TAIL)
        self.assertEqual(out, HEAD+rule.ANCHOR+rule.RULE+TAIL)
        self.assertIn('vm_reallocate))\n\n;;; Disk space reporting', out)
        self.assertIn('(info-type "vfs.disk-space"))\n\n;;; (system-graphics)', out)

    def test_present_rule_is_unchanged(self):
        text = HEAD+rule.ANCHOR+rule.RULE+TAIL
        self.assertEqual(rule.add_rule(text), text)

    def test_missing_or_repeated_anchor_stops(self):
        for text in (HEAD+TAIL, HEAD+rule.ANCHOR+rule.ANCHOR+TAIL):
            with self.assertRaises(ValueError):
                rule.add_rule(text)

    def test_command_writes_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/'system.sb', Path(tmp)/'out.sb'
            src.write_text(HEAD+rule.ANCHOR+TAIL)
            subprocess.run([sys.executable, '-I', str(PATCH/'src/disk_space_rule.py'), str(src), str(out)], check=True,
                           capture_output=True)
            self.assertEqual(out.read_text(), HEAD+rule.ANCHOR+rule.RULE+TAIL)

    def test_tahoe_system_sb_gains_only_the_rule(self):
        if not TAHOE.exists():
            self.skipTest('25G332 unpack tree absent')
        text = TAHOE.read_text()
        out = rule.add_rule(text)
        self.assertEqual(out.replace(rule.RULE, '', 1), text)
        self.assertEqual(out.count('vfs.disk-space'), 1)

    def test_host_27_2_system_sb_already_has_the_rule(self):
        """26B123's system.sb is 26A428's plus exactly RULE; on a 27.2+ host it passes unchanged."""
        if not HOST.exists() or '(info-type "vfs.disk-space")' not in HOST.read_text():
            self.skipTest('host system.sb without the 27.2 rule')
        text = HOST.read_text()
        self.assertIn(rule.ANCHOR+rule.RULE, text)
        self.assertEqual(rule.add_rule(text), text)


if __name__ == '__main__':
    unittest.main()
