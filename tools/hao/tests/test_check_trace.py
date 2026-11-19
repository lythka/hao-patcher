"""The USB installer check's trace: one line per file before it is read, and a traceback there on a fatal signal."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / 'src'


class CheckTrace(unittest.TestCase):
    def test_a_crash_leaves_the_last_file_and_a_traceback(self):
        # A real fault would leave a crash report on the Mac running the tests. The child ignores SIGBUS before the
        # fault handler is installed: the handler writes its traceback, re-raises into SIG_IGN, and the child exits.
        with tempfile.TemporaryDirectory() as d:
            code = ('import sys; sys.path.insert(0, %r)\n'
                    'import builder, os, pathlib, signal\n'
                    'signal.signal(signal.SIGBUS, signal.SIG_IGN)\n'
                    'f = builder.check_trace(pathlib.Path(%r)); f.write("4 kit/x\\n"); f.flush()\n'
                    'os.kill(os.getpid(), signal.SIGBUS)\n'
                    'os._exit(3)\n') % (str(SRC), d)
            r = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True)
            self.assertEqual(r.returncode, 3, r.stderr)
            logs = list((Path(d) / 'logs').glob('manifest-check-*.txt'))
            self.assertEqual(len(logs), 1)
            text = logs[0].read_text()
            self.assertTrue(text.startswith('4 kit/x\n'), text)
            self.assertIn('Fatal Python error', text)

    def test_no_trace_where_logs_cannot_be_made(self):
        sys.path.insert(0, str(SRC))
        try:
            import builder
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'logs').write_text('a file, not a folder')
            self.assertIsNone(builder.check_trace(Path(d)))


if __name__ == '__main__':
    unittest.main()
