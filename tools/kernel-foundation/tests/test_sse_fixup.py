"""Production transaction and static SSE helpers, executed in a signed x86_64 host harness."""
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class Fixup(unittest.TestCase):
    def test_synchronous_transaction(self):
        gen = runpy.run_path(str(HERE.parent/'sse_codegen.py'))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'sse.S').write_text(gen['assembly']())
            subprocess.run(['xcrun', 'clang++', '-arch', 'x86_64', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(HERE/'sse_fixup_harness.cpp'), str(tmp/'sse.S'), '-o', str(tmp/'harness')], check=True)
            subprocess.run(['codesign', '-s', '-', str(tmp/'harness')], check=True, capture_output=True)
            result = subprocess.check_output([str(tmp/'harness')], text=True)
            self.assertIn('1024 contexts OK', result)
