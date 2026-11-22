"""The disk plan (space.py) and the drive-first stick writing (stick.adopt_drive_links): 1440."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import space  # noqa: E402
import stick  # noqa: E402

GB = 10 ** 9
RELEASE = {'downloads': [
    {'row': 'kdk', 'size': int(1.2 * GB), 'sha256': 'a' * 64},
    {'row': 'ipsw_27_0', 'size': 25 * GB, 'sha256': 'b' * 64, 'stream': 'ipsw',
     'pins': {'images': {'filesystem': {'size': 14 * GB}, 'rosetta': {'size': int(6.5 * GB)},
                         'os': {'size': int(2.9 * GB)}}}},
    {'row': 'installer', 'size': 18 * GB, 'sha256': 'c' * 64, 'stream': 'installer'}],
    'assets': [{'row': 'x', 'size': int(0.5 * GB), 'sha256': 'd' * 64}]}


class Plan(unittest.TestCase):
    def test_target_rebuild_is_shown_without_adding_host_scratch(self):
        stock=space.plan(RELEASE); rebuilt=space.plan(dict(RELEASE,install_cache_rebuild=True))
        self.assertEqual(stock['mac_peak'],rebuilt['mac_peak'])
        self.assertEqual(stock['drive_need'],rebuilt['drive_need'])
        self.assertTrue(any('On the target' in line for line in space.lines(rebuilt)))
        self.assertFalse(any('On the target' in line for line in space.lines(stock)))

    def test_streamed_downloads_are_not_stored_and_the_images_go_to_the_drive(self):
        p = space.plan(RELEASE)
        self.assertAlmostEqual(p['download'], 1.7, places=1)          # the IPSW and the installer are not fetched
        self.assertAlmostEqual(p['stages'][2][2], 23.4, places=1)       # images on the drive, not on this Mac
        self.assertLess(p['mac_peak'], 25)
        self.assertEqual(space.drive_size_gb(p), 64)          # never below what create-installer accepts
        self.assertIn('(a 64 GB drive or larger)', space.lines(p)[-1])

    def test_refusals(self):
        p = space.plan(RELEASE)
        self.assertEqual(space.problems(p, 100 * GB, 64 * GB), [])
        self.assertIn('this Mac has', space.problems(p, 10 * GB)[0])
        self.assertIn('USB drive holds', space.problems(p, 100 * GB, 16 * GB)[0])

    def test_already_downloaded_files_are_not_fetched_again(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'sha256').mkdir()
            (Path(d) / 'sha256' / ('a' * 64)).write_bytes(b'')
            self.assertAlmostEqual(space.plan(RELEASE, d)['download'], 0.5, places=1)


class DriveFirst(unittest.TestCase):
    def test_payload_on_the_drive_is_moved_once_and_linked_for_the_second_hook(self):
        with tempfile.TemporaryDirectory() as d:
            mount = Path(d)
            made = mount / stick.BUILD_DIR / 'golden-gate-userspace' / 'payload'
            made.mkdir(parents=True)
            (made / 'filesystem.dmg').write_bytes(b'images')
            kit = mount / 'kit'
            for hook in ('02-golden-gate-userspace', '03-hao-runtime'):
                (kit / 'hooks' / hook / 'inputs').mkdir(parents=True)
                os.symlink(made, kit / 'hooks' / hook / 'inputs' / 'golden_gate_userspace')
            done = stick.adopt_drive_links(kit, mount)
            self.assertEqual(len(done), 2)
            a = kit / 'hooks/02-golden-gate-userspace/inputs/golden_gate_userspace/filesystem.dmg'
            b = kit / 'hooks/03-hao-runtime/inputs/golden_gate_userspace/filesystem.dmg'
            self.assertFalse(a.parent.is_symlink())
            self.assertEqual(a.read_bytes(), b'images')
            self.assertEqual(a.stat().st_ino, b.stat().st_ino)            # one copy of the bytes on the drive
            self.assertFalse(made.exists())

    def test_links_elsewhere_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            mount = Path(d)
            (mount / stick.BUILD_DIR).mkdir()
            (mount / 'kit').mkdir()
            os.symlink('/usr/bin', mount / 'kit' / 'bin')
            self.assertEqual(stick.adopt_drive_links(mount / 'kit', mount), [])
            self.assertTrue((mount / 'kit' / 'bin').is_symlink())


if __name__ == '__main__':
    unittest.main()
