"""hao-accept's USB read-back: raw-device reads in whole sectors, and a failed or short read reported as such.

A read of /dev/rdiskN that is not whole sectors fails with EINVAL and dd reads 0 bytes, so `dd ... 2> /dev/null |
sha256` hashed nothing and showed a false mismatch (laptop 1's hand-run with bs=1000000, 2026-11-18). These tests run
the shell commands hao-accept sends to the laptop on this Mac with /bin/sh: against a file always, and against a
RAM disk's raw device when HAO_ACCEPT_TEST_RAMDISK=1 (it attaches and detaches one with hdiutil; no root needed).

    python3.14 -I -B -m unittest discover -s tools/hao-accept/tests
"""
import hashlib
import os
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = SourceFileLoader('hao_accept', os.path.join(TOOLS, 'hao-accept'))
ha = module_from_spec(spec_from_loader('hao_accept', _loader))
_loader.exec_module(ha)


def sh(cmd):
    r = subprocess.run(['/bin/sh', '-c', cmd], capture_output=True, timeout=60)
    return r.returncode, (r.stdout + r.stderr).decode()


class RawSha256(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='hao-accept-raw-')
        self._lt, ha.LT = ha.LT, os.path.join(self.tmp, 'lt')

    def tearDown(self):
        ha.LT = self._lt
        subprocess.run(['rm', '-rf', self.tmp])

    def test_refuses_sizes_that_are_not_whole_sectors(self):
        for off, length, bs in ((0, 2_000_000, 1_000_000),       # the hand-run's bs
                                (1 << 20, 1000, 1000), (100, 512, 512), (0, 1 << 20, 3 << 19), (0, 0, 512)):
            with self.assertRaises(ValueError, msg=(off, length, bs)):
                ha.raw_sha256_cmd('disk9', off, length, bs, 't')

    def test_result_complete_failed_and_short(self):
        h = hashlib.sha256(b'x').hexdigest()
        self.assertEqual(ha.raw_sha256_result('%s\nDD-RC 0\n1048576 bytes transferred in 0.1 secs\n' % h, 1 << 20),
                         (h, None))
        empty = hashlib.sha256(b'').hexdigest()                    # what the old pipeline compared
        sha, why = ha.raw_sha256_result('%s\nDD-RC 1\n0 bytes transferred in 0.0 secs\n' % empty, 1 << 20)
        self.assertEqual(sha, '')
        self.assertIn('read FAILED', why)
        sha, why = ha.raw_sha256_result('%s\nDD-RC 0\n512 bytes transferred in 0.0 secs\n' % h, 1 << 20)
        self.assertEqual((sha, why), ('', 'read SHORT: 512 of 1048576 bytes'))

    def run_on(self, dev_name, off, length, bs):
        rc, out = sh(ha.raw_sha256_cmd(dev_name, off, length, bs, 't'))
        return ha.raw_sha256_result(out, length)

    def test_reads_a_file_through_the_shell(self):
        data = os.urandom(3 << 20)
        # the same command with /dev/rNAME replaced by a file
        path = os.path.join(self.tmp, 'img')
        with open(path, 'wb') as f:
            f.write(data)
        cmd = ha.raw_sha256_cmd('XDEV', 1 << 20, 2 << 20, 1 << 20, 't').replace('/dev/rXDEV', path)
        rc, out = sh(cmd)
        self.assertEqual(ha.raw_sha256_result(out, 2 << 20), (hashlib.sha256(data[1 << 20:]).hexdigest(), None))
        cmd = ha.raw_sha256_cmd('XDEV', 1 << 20, 4 << 20, 1 << 20, 't').replace('/dev/rXDEV', path)   # past the end
        rc, out = sh(cmd)
        sha, why = ha.raw_sha256_result(out, 4 << 20)
        self.assertEqual(sha, '')
        self.assertIn('read SHORT', why)

    @unittest.skipUnless(os.environ.get('HAO_ACCEPT_TEST_RAMDISK') == '1', 'HAO_ACCEPT_TEST_RAMDISK=1 for a RAM disk')
    def test_raw_device(self):
        r = subprocess.run(['hdiutil', 'attach', '-nomount', 'ram://8192'], capture_output=True, text=True, check=True)
        dev = r.stdout.split()[0]                                   # /dev/diskN, 4 MiB
        try:
            name = dev[len('/dev/'):]
            data = os.urandom(4 << 20)
            with open('/dev/r' + name, 'wb', buffering=0) as f:
                f.write(data)
            self.assertEqual(self.run_on(name, 1 << 20, 3 << 20, 1 << 20),
                             (hashlib.sha256(data[1 << 20:]).hexdigest(), None))
            # what the hand-run did: dd bs=1000000 on the raw device reads nothing and fails
            rc, out = sh('dd if=/dev/r%s bs=1000000 count=2 2>&1' % name)
            self.assertIn('0 bytes transferred', out)
        finally:
            subprocess.run(['hdiutil', 'detach', dev], capture_output=True)


if __name__ == '__main__':
    unittest.main()
