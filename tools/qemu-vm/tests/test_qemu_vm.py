import datetime, importlib.machinery, importlib.util, json, plistlib, subprocess, tempfile, unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader('qemu_vm', str(HERE.parent / 'qemu-vm'))
spec = importlib.util.spec_from_loader('qemu_vm', loader)
qv = importlib.util.module_from_spec(spec)
loader.exec_module(qv)


class Command(unittest.TestCase):
    def test_topology_and_paths(self):
        c = qv.command(Path('/r'), Path('/e/esp.cdr'), 'n', 22397, '16G', Path('/s'))
        j = ' '.join(c)
        self.assertEqual(c[0], qv.QEMU)
        for want in ('q35', 'tcg,thread=multi', 'IvyBridge,vendor=GenuineIntel', '4,sockets=1,cores=2,threads=2',
                     'file=/e/esp.cdr,snapshot=on', 'file=/r/guest.qcow2', 'hostfwd=tcp:127.0.0.1:22397-:22',
                     'file:/r/serial.log', 'unix:/s/qmp.sock', 'unix:/s/gdb.sock', 'model=vmxnet3', '-no-reboot'):
            self.assertIn(want, j)
        self.assertEqual(c[c.index('-m') + 1], '16G')


class Runs(unittest.TestCase):
    def test_run_folder_is_always_new(self):
        with tempfile.TemporaryDirectory() as t:
            now = datetime.datetime(2026, 11, 3, 14, 0, 0)
            r = qv.new_run(Path(t) / 'a', 'n', now)
            self.assertTrue(r.is_dir())
            with self.assertRaises(FileExistsError):
                qv.new_run(Path(t) / 'a', 'n', now)


class Faults(unittest.TestCase):
    def test_counts_per_process_and_early_boot_line(self):
        log = ['boot',
               'Data/Stack execution not permitted: launchd[pid 1] at 0x1',
               'Data/Stack execution not permitted: init_featureflag[pid 15] x',
               'Data/Stack execution not permitted: launchd[pid 1] y',
               'com.apple.xpc.launchd (boot) <Notice>: Early boot complete. Continuing system boot.',
               'Early boot complete. again']
        f = qv.faults(log)
        self.assertEqual(f['total'], 3)
        self.assertEqual(f['per_process'], {'launchd': 2, 'init_featureflag': 1})
        self.assertEqual(f['early_boot_line'], 5)
        self.assertEqual(f['lines'], 6)


class LaunchdLog(unittest.TestCase):
    def test_lines_from_binary_and_exit_counts(self):
        import io
        blob = (b'\x00\x01junk2026-11-03 09:19:37.530000 (system/com.apple.notifyd [127]) <Notice>: exited due to '
                b'exit(1), ran for 2493ms\n\xff\xfe' + b'\x00' * 100 +
                b'2026-11-03 09:19:38.540000 (system/com.apple.sandboxd [128]) <Notice>: exited due to SIGABRT | sent'
                b'\n2026-11-03 09:19:37.530000 (system/com.apple.notifyd [127]) <Notice>: exited due to exit(1), ran '
                b'for 2493ms\n2026-11-03 09:20:00.000001 (system/com.apple.notifyd [131]) <Notice>: exited due to '
                b'exit(1), ran for 530ms\n')
        lines = qv.log_lines(io.BytesIO(blob), chunk=64, overlap=200)
        self.assertEqual(len(lines), 3)                      # the duplicate line is kept once
        self.assertTrue(lines[0].startswith('2026-11-03 09:19:37.530000'))
        self.assertEqual(qv.exits(lines), {'com.apple.notifyd': {'exit(1)': 2}, 'com.apple.sandboxd': {'SIGABRT': 1}})


class EspFolder(unittest.TestCase):
    def test_folder_uses_read_only_fat_driver(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(qv.esp_drive(Path(t)), 'id=esp,if=none,format=raw,file=fat:%s,snapshot=on' % t)
        self.assertIn('snapshot=on', qv.esp_drive(Path('/nonexistent/esp.cdr')))

    def test_boot_args_appended(self):
        import plistlib
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / 'config.plist'
            c.write_bytes(plistlib.dumps({'NVRAM': {'Add': {qv.NVRAM_GUID: {'boot-args': 'keepsyms=1 -v'}}}}))
            self.assertEqual(qv.add_boot_args(c, ['hao_nx_stats=1']), 'keepsyms=1 -v hao_nx_stats=1')
            self.assertEqual(plistlib.loads(c.read_bytes())['NVRAM']['Add'][qv.NVRAM_GUID]['boot-args'],
                             'keepsyms=1 -v hao_nx_stats=1')

    def test_csr_set(self):
        import plistlib
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / 'config.plist'
            c.write_bytes(plistlib.dumps({'NVRAM': {'Add': {qv.NVRAM_GUID: {'csr-active-config': b'\x03\x08\x00\x00'}}}}))
            self.assertEqual(qv.set_csr(c, 0x813), '0x813')
            self.assertEqual(plistlib.loads(c.read_bytes())['NVRAM']['Add'][qv.NVRAM_GUID]['csr-active-config'],
                             b'\x13\x08\x00\x00')


class Retire(unittest.TestCase):
    def test_extracts_logs_and_moves_the_run(self):
        import json
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            build = t / 'tmp' / 'mbp92-25G332-dev-x-20261103-120000'
            rec = t / 'runs' / build.name
            rec.mkdir(parents=True)
            (rec / 'MANIFEST.txt').write_text('oclp abc (abc)\nkdk K\nset dev-x: a r1 b r2\nfile x 1\n')
            (rec / 'build.log').write_text('run mbp92 25G332 fd8 dev-x (1234567) -> somewhere\n== done\n')
            build.mkdir(parents=True)
            (build / 'target.img').write_bytes(b'')
            run = t / 'qemu' / 'r-20261103-1'
            run.mkdir(parents=True)
            (run / 'serial.log').write_text('Data/Stack execution not permitted: launchd[pid 1] at x\n')
            (run / 'started.json').write_text(json.dumps({'image': str(build / 'target.img'), 'esp': '/e', 'boot_args': 'a=1'}))
            (run / 'guest.qcow2').write_bytes(b'\x00' * 10 + b'2026-11-03 12:00:00.000001 (system/com.apple.notifyd [9]) <Notice>: exited due to exit(1), ran\n')
            log = t / 'IMAGE-LOG.md'
            log.write_text('# Image log\n\nheader text\n\n## 2026-11-01 older entry\n- x\n')
            earlier = t / 'evidence' / ('qemu-' + run.name)
            earlier.mkdir(parents=True)
            (earlier / 'faults.json').write_text('earlier')              # an earlier retire's file
            calls = []
            def fake_logs(r, out, tmp, evr, predicate=None, now=None):
                self.assertTrue(r.exists())                      # logs runs before the run folder moves
                calls.append((r, out, tmp))
                out.mkdir(parents=True, exist_ok=True)
                (out / qv.LOGARCHIVE).mkdir()
            with mock.patch.object(qv, 'logs', fake_logs):
                ev, moved = qv.retire(run, 'notifyd looped', t / 'evidence', t / 'gc', log, t / 'runs', t / 'work')
            self.assertEqual(calls, [(run.resolve(), ev, t / 'work')])  # same evidence folder
            self.assertTrue((ev / qv.LOGARCHIVE).is_dir())
            self.assertFalse(run.exists())
            self.assertTrue((moved / 'guest.qcow2').is_file())
            self.assertEqual(json.loads((ev / 'launchd-exits.json').read_text()), {'com.apple.notifyd': {'exit(1)': 1}})
            self.assertEqual(json.loads((ev / 'faults.json').read_text())['total'], 1)
            self.assertEqual([p.read_text() for p in ev.glob('replaced-*/faults.json')], ['earlier'])
            self.assertIn('set dev-x: a r1 b r2', (ev / 'recipe.txt').read_text())
            text = log.read_text()
            self.assertLess(text.index('r-20261103-1'), text.index('older entry'))   # newest first, after the header
            self.assertIn('commit 1234567', text)
            self.assertIn('- showed: notifyd looped', text)

    def test_pruned_image_keeps_the_text_evidence(self):
        import json
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            run = t / 'qemu' / 'r-20261103-2'
            run.mkdir(parents=True)
            (run / 'serial.log').write_text('Data/Stack execution not permitted: launchd[pid 1] at x\n')
            (run / 'started.json').write_text(json.dumps({'image': str(t / 'tmp' / 'gone' / 'target.img')}))
            log = t / 'IMAGE-LOG.md'
            log.write_text('# Image log\n')
            with mock.patch.object(qv, 'logs', side_effect=AssertionError('logs needs the image')):
                ev, moved = qv.retire(run, 'probe', t / 'evidence', t / 'gc', log, t / 'runs', t / 'work')
            self.assertIn('was pruned', (ev / 'logs-skipped.txt').read_text())
            self.assertEqual(json.loads((ev / 'faults.json').read_text())['total'], 1)
            self.assertTrue((moved / 'serial.log').is_file())
            self.assertIn('- showed: probe; unified log lost (backing image pruned)', log.read_text())

    def test_set_aside_never_deletes(self):
        now = datetime.datetime(2026, 11, 3, 23, 0, 0, tzinfo=datetime.timezone.utc)
        with tempfile.TemporaryDirectory() as t:
            ev = Path(t)
            self.assertIsNone(qv.set_aside(ev, 'faults.json', now))       # nothing there: nothing made
            self.assertEqual([p.name for p in ev.iterdir()], [])
            (ev / 'faults.json').write_text('1')
            a = qv.set_aside(ev, 'faults.json', now)
            (ev / 'faults.json').write_text('2')
            b = qv.set_aside(ev, 'faults.json', now)                      # same second: a suffix, not a replace
            self.assertEqual((a.read_text(), b.read_text()), ('1', '2'))
            self.assertEqual(a.parent, b.parent)
            self.assertNotEqual(a, b)
            self.assertFalse((ev / 'faults.json').exists())

    def test_failed_logs_moves_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            run = t / 'qemu' / 'r'
            run.mkdir(parents=True)
            with mock.patch.object(qv, 'logs', side_effect=RuntimeError('attach failed')):
                with self.assertRaises(RuntimeError):
                    qv.retire(run, 'x', t / 'evidence', t / 'gc', t / 'IMAGE-LOG.md', t / 'runs', t / 'work')
            self.assertTrue(run.is_dir())
            self.assertFalse((t / 'IMAGE-LOG.md').exists())


class Logs(unittest.TestCase):
    def test_running_and_refusal(self):
        import os
        with tempfile.TemporaryDirectory() as t:
            run = Path(t) / 'r'
            run.mkdir()
            self.assertFalse(qv.running(run))                       # no qemu.pid
            (run / 'qemu.pid').write_text(str(os.getpid()))
            self.assertTrue(qv.running(run))
            with self.assertRaises(SystemExit):
                qv.logs(run, None, Path(t) / 'work', Path(t) / 'ev')
            self.assertFalse((Path(t) / 'work').exists())           # refused before any copy
            (run / 'qemu.pid').write_text('999999999')
            self.assertFalse(qv.running(run))

    def test_commands(self):
        c = qv.merge_commands(Path('/s/target.img'), (Path('/r/guest.qcow2'), Path('/w/guest.qcow2')), Path('/w/target.img'))
        self.assertEqual(c[0], ['cp', '-c', '/s/target.img', '/w/target.img'])
        self.assertEqual(c[1], ['cp', '-c', '/r/guest.qcow2', '/w/guest.qcow2'])
        self.assertEqual(c[2], [qv.QEMU_IMG, 'rebase', '-u', '-F', 'raw', '-b', '/w/target.img', '-f', 'qcow2', '/w/guest.qcow2'])
        self.assertEqual(c[3], [qv.QEMU_IMG, 'commit', '-q', '-f', 'qcow2', '/w/guest.qcow2'])
        for cmd in c:                       # the run's overlay and the stored image are only ever cp sources
            for own in ('/r/guest.qcow2', '/s/target.img'):
                self.assertNotIn(own, cmd[3:] if cmd[0] == 'cp' else cmd)
        self.assertEqual(qv.show_command(Path('/e/boot.logarchive'), 'process == "x"'),
                         ['/usr/bin/log', 'show', '--archive', '/e/boot.logarchive', '--predicate', 'process == "x"',
                          '--style', 'compact'])

    def test_work_dir_is_new(self):
        with tempfile.TemporaryDirectory() as t:
            now = datetime.datetime(2026, 11, 3, 17, 0, 0, tzinfo=datetime.timezone.utc)
            w = qv.work_dir(Path(t) / 'qemu-logs', 'r13', now)
            self.assertTrue(w.is_dir() and w.name.startswith('r13-2026110'))
            with self.assertRaises(FileExistsError):
                qv.work_dir(Path(t) / 'qemu-logs', 'r13', now)

    def test_timesync_and_info(self):
        import struct, uuid
        u = uuid.UUID('31e8ea15-5349-4ba2-8a81-50bd4c22b864')
        boot = struct.pack('<HHI', 0xbbb0, 48, 0) + u.bytes + struct.pack('<IIQii', 1, 1, 1000 * 10**9, 0, 0)
        ts = struct.pack('<HHI', 0x7354, 32, 0) + struct.pack('<QQii', 5 * 10**9, 1005 * 10**9, 0, 0)
        b = qv.timesync(boot + ts)
        self.assertEqual(b, [(str(u).upper(), 1, 1, 1000 * 10**9, [(5 * 10**9, 1005 * 10**9)])])
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / 'timesync').mkdir()
            (Path(t) / 'timesync' / '0000000000000002.timesync').write_bytes(boot + ts)
            i = qv.archive_info(t, end_wall_ns=1100 * 10**9)
            # anchored on the last 'Ts' record (5 s continuous at 1005 s wall), carried to the end wall time
            self.assertEqual(i['EndTimeRef'], {'ContinuousTime': 100 * 10**9, 'UUID': str(u).upper(), 'WallTime': 1100 * 10**9})
            ts2 = struct.pack('<HHI', 0x7354, 32, 0) + struct.pack('<QQii', 7 * 10**9, 1010 * 10**9, 0, 0)
            (Path(t) / 'timesync' / '0000000000000002.timesync').write_bytes(boot + ts + ts2)
            i = qv.archive_info(t, end_wall_ns=1100 * 10**9)
            self.assertEqual(i['EndTimeRef']['ContinuousTime'], 97 * 10**9)      # 7 s + 90 s after the last record
            i = qv.archive_info(t, end_wall_ns=900 * 10**9)                      # never before the last record
            self.assertEqual(i['EndTimeRef'], {'ContinuousTime': 7 * 10**9, 'UUID': str(u).upper(), 'WallTime': 1010 * 10**9})
            self.assertEqual(i['PersistMetadata']['OldestTimeRef']['WallTime'], 1000 * 10**9)
            self.assertEqual(i['OSArchiveVersion'], 5)

    def test_extract_layout(self):
        import plistlib
        with tempfile.TemporaryDirectory() as t:
            data, ev = Path(t) / 'Data', Path(t) / 'ev'
            db = data / 'private' / 'var' / 'db'
            (db / 'diagnostics' / 'Persist').mkdir(parents=True)
            (db / 'diagnostics' / 'Persist' / '1.tracev3').write_bytes(b'x')
            (db / 'uuidtext' / 'AB').mkdir(parents=True)
            (db / 'uuidtext' / 'AB' / 'CD').write_bytes(b'y')
            (data / 'private' / 'var' / 'log' / 'com.apple.xpc.launchd').mkdir(parents=True)
            (data / 'private' / 'var' / 'log' / 'com.apple.xpc.launchd' / 'launchd.log').write_text('l\n')
            (data / 'private' / 'var' / 'tmp' / 'heartbeat').mkdir(parents=True)
            (data / 'private' / 'var' / 'tmp' / 'heartbeat' / 'beat').write_text('1')
            (data / 'Library' / 'Logs' / 'DiagnosticReports').mkdir(parents=True)
            (data / 'Library' / 'Logs' / 'DiagnosticReports' / 'x.ips').write_text('{}')
            self.assertIsNone(qv.data_root([str(Path(t) / 'none')]))
            self.assertEqual(qv.data_root([str(Path(t) / 'none'), str(data)]), data)
            ev.mkdir()
            (ev / 'serial.log').write_text('kept')                  # anything already there is kept
            old = ev / qv.LOGARCHIVE / 'Persist'
            old.mkdir(parents=True)
            (old / '0.tracev3').write_bytes(b'old')                 # an earlier extraction's rotated file
            (ev / qv.ERRORS).write_text('stale\n')
            got = qv.extract(data, Path(t) / 'stage')
            now = datetime.datetime(2026, 11, 3, 23, 0, 0, tzinfo=datetime.timezone.utc)
            qv.publish(Path(t) / 'stage', ev, now)
            arc = ev / qv.LOGARCHIVE
            self.assertFalse((arc / 'Persist' / '0.tracev3').exists())   # replaced, not mixed
            self.assertFalse((ev / qv.ERRORS).exists())
            aside = ev / ('replaced-' + now.astimezone().strftime('%Y%m%d-%H%M%S'))
            self.assertEqual((aside / qv.LOGARCHIVE / 'Persist' / '0.tracev3').read_bytes(), b'old')  # kept aside
            self.assertEqual((aside / qv.ERRORS).read_text(), 'stale\n')
            self.assertEqual((arc / 'Persist' / '1.tracev3').read_bytes(), b'x')
            self.assertEqual((arc / 'AB' / 'CD').read_bytes(), b'y')
            self.assertEqual(plistlib.loads((arc / 'Info.plist').read_bytes())['OSArchiveVersion'], 5)
            self.assertEqual((ev / 'launchd.log').read_text(), 'l\n')
            self.assertTrue((ev / 'heartbeat' / 'beat').is_file())
            self.assertTrue((ev / 'DiagnosticReports' / 'x.ips').is_file())
            self.assertEqual((ev / 'serial.log').read_text(), 'kept')
            self.assertEqual(got['errors'], 0)


def plist_attach(mount):
    """hdiutil attach -plist output of an APFS whole-disk image (shape of a real attach)."""
    return plistlib.dumps({'system-entities': [
        {'content-hint': 'GUID_partition_scheme', 'dev-entry': '/dev/disk7', 'potentially-mountable': False},
        {'content-hint': 'EFI', 'dev-entry': '/dev/disk7s1'},
        {'content-hint': 'Apple_APFS', 'dev-entry': '/dev/disk7s2'},
        {'content-hint': qv.APFS_CONTAINER, 'dev-entry': '/dev/disk8'},
        {'content-hint': '41504653-0000-11AA-AA11-00306543ECAC', 'dev-entry': '/dev/disk8s1', 'mount-point': '/Volumes/S'},
        {'content-hint': '41504653-0000-11AA-AA11-00306543ECAC', 'dev-entry': '/dev/disk8s2', 'mount-point': str(mount)}]})


class Cleanup(unittest.TestCase):
    """logs() with subprocess mocked: the clone is detached and the work folder handled in every case."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, self.t)
        self.run_dir = self.t / 'run'
        self.run_dir.mkdir()
        (self.run_dir / 'started.json').write_text(json.dumps({'image': str(self.t / 'img' / 'target.img')}))
        self.data = self.t / 'Data'
        (self.data / 'private' / 'var' / 'db' / 'diagnostics' / 'Persist').mkdir(parents=True)
        (self.data / 'private' / 'var' / 'db' / 'diagnostics' / 'Persist' / '1.tracev3').write_bytes(b'x')
        self.calls, self.attach_rc, self.detach_rc = [], 0, 0

    def fake_run(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ['hdiutil', 'attach']:
            if self.attach_rc:
                raise subprocess.CalledProcessError(self.attach_rc, cmd)
            return subprocess.CompletedProcess(cmd, 0, plist_attach(self.data), b'')
        if cmd[:2] == ['hdiutil', 'detach']:
            return subprocess.CompletedProcess(cmd, self.detach_rc, b'', b'')
        if cmd[0] == '/sbin/mount':        # another session's attach: never a candidate
            return subprocess.CompletedProcess(cmd, 0, '/dev/disk9s1 on %s (apfs, local, read-only)\n' % self.data, '')
        return subprocess.CompletedProcess(cmd, 0, '', '')

    def logs(self):
        with mock.patch.object(qv.subprocess, 'run', self.fake_run), mock.patch.object(qv.time, 'sleep'):
            return qv.logs(self.run_dir, self.t / 'ev', self.t / 'work', self.t / 'evr')

    def detaches(self):
        return [c for c in self.calls if c[:2] == ['hdiutil', 'detach']]

    def test_success_detaches_whole_disk_and_removes_work(self):
        ev, got = self.logs()
        self.assertEqual(self.detaches(), [['hdiutil', 'detach', '/dev/disk7']])   # whole disk, not a slice
        self.assertEqual(list((self.t / 'work').iterdir()), [])
        rec = json.loads((ev / qv.RECORD).read_text())
        self.assertEqual(rec['data_volume'], str(self.data))
        self.assertIn('/dev/disk8s2', rec['devices'])

    def test_exception_during_extraction_still_detaches(self):
        with mock.patch.object(qv, 'extract', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):
                self.logs()
        self.assertEqual(self.detaches(), [['hdiutil', 'detach', '/dev/disk7']])
        self.assertEqual(list((self.t / 'work').iterdir()), [])
        self.assertFalse((self.t / 'ev').exists())

    def test_failed_detach_keeps_and_names_work(self):
        self.detach_rc = 1
        with mock.patch('sys.stderr') as err:
            with self.assertRaises(SystemExit) as e:
                self.logs()
        work = list((self.t / 'work').iterdir())
        self.assertEqual(len(work), 1)                                 # kept
        self.assertIn(str(work[0]), str(e.exception))                  # and named
        self.assertIn(str(work[0]), ''.join(str(c) for c in err.write.call_args_list))
        self.assertEqual(self.detaches()[-1], ['hdiutil', 'detach', '-force', '/dev/disk7'])
        self.assertTrue((self.t / 'ev' / qv.LOGARCHIVE).is_dir())       # the evidence was still written

    def test_failed_attach_removes_work(self):
        self.attach_rc = 1
        with self.assertRaises(subprocess.CalledProcessError):
            self.logs()
        self.assertEqual(self.detaches(), [])
        self.assertEqual(list((self.t / 'work').iterdir()), [])

    def test_no_data_volume_in_this_attach(self):
        self.data = self.t / 'elsewhere'                  # this attach mounts no diagnostics; disk9 does
        with self.assertRaises(SystemExit):
            self.logs()
        self.assertEqual(self.detaches(), [['hdiutil', 'detach', '/dev/disk7']])
        self.assertEqual(list((self.t / 'work').iterdir()), [])


class Devices(unittest.TestCase):
    def test_whole_disk_and_own_mounts(self):
        ents = plistlib.loads(plist_attach('/Volumes/D'))['system-entities']
        self.assertEqual(qv.whole_disk(ents), '/dev/disk7')
        self.assertEqual(qv.whole_disk([{'dev-entry': '/dev/disk8', 'content-hint': qv.APFS_CONTAINER},
                                        {'dev-entry': '/dev/disk8s1'}, {'dev-entry': '/dev/disk11'}]), '/dev/disk11')
        table = [('/dev/disk8s3', '/Volumes/late'), ('/dev/disk8s1s1', '/Volumes/snap'),
                 ('/dev/disk9s1', '/Volumes/other'), ('/dev/disk80s1', '/Volumes/other2'), ('/dev/disk3s1', '/')]
        self.assertEqual(qv.own_mounts(ents, table), ['/Volumes/S', '/Volumes/D', '/Volumes/late', '/Volumes/snap'])


if __name__ == '__main__':
    unittest.main()
