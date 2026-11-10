"""pipeline/stages/chunklist.py: the unsigned Recovery chunklist. python3 -m unittest discover -s pipeline/tests"""
import os, subprocess, sys, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
CL = TOP / 'pipeline/stages/chunklist.py'


def run(*a):
    return subprocess.run([sys.executable, '-I', str(CL), *map(str, a)], capture_output=True, text=True)


class Chunklist(unittest.TestCase):
    def test_create_verify_and_mismatch(self):
        d = Path(tempfile.mkdtemp())
        (d / 'a.dmg').write_bytes(os.urandom((10 << 20) * 2 + 12345))
        self.assertEqual(run('create', d / 'a.dmg', d / 'a.cl').returncode, 0)
        b = (d / 'a.cl').read_bytes()
        self.assertEqual(b[:4], b'CNKL')
        self.assertEqual(len(b), 0x24 + 3 * 36)            # 3 chunks, no signature
        self.assertEqual(run('verify', d / 'a.dmg', d / 'a.cl').returncode, 0)
        (d / 'b.dmg').write_bytes(os.urandom(100))
        self.assertEqual(run('verify', d / 'b.dmg', d / 'a.cl').returncode, 1)

    def test_same_chunk_table_as_apple(self):
        # Apple's BaseSystem.dmg and its signed chunklist from a build's unpack output (skipped without the store)
        lock = TOP / 'pipeline/inputs/25G332'
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/lock.py'), 'path', str(lock), 'unpack'],
                           capture_output=True, text=True)
        u = Path(r.stdout.strip())
        dmg, apple = u / 'out/BaseSystem-x86_64.dmg', u / 'zip/AssetData/Restore/BaseSystem.chunklist'
        if r.returncode or not dmg.is_file() or not apple.is_file():
            self.skipTest('no 25G332 unpack output in the store')
        out = Path(tempfile.mkdtemp()) / 'ours.cl'
        self.assertEqual(run('create', dmg, out).returncode, 0)
        ours, theirs = out.read_bytes(), apple.read_bytes()
        n = int.from_bytes(ours[0x0C:0x14], 'little')
        self.assertEqual(ours[0x24:0x24 + 36 * n], theirs[0x24:0x24 + 36 * n])
        self.assertEqual(n, int.from_bytes(theirs[0x0C:0x14], 'little'))


if __name__ == '__main__':
    unittest.main()
