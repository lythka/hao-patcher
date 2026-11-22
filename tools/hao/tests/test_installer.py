"""hao create-installer (the user's one command) and hao report, with fakes only: no real disk, no network.

The fake Mac answers diskutil from a table of disks (an internal SSD, the running system's container, a 64 GB stick,
a 16 GB stick, an external SSD); a fake acquire records what would be downloaded. Answers to the questions come from
a list, as a user would type them.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
sys.path.insert(0, str(TOOL / 'src'))
sys.path.insert(0, str(HERE))
import console as CON      # noqa: E402
import detect as DETECT    # noqa: E402
import installer as INST   # noqa: E402
import report as REP       # noqa: E402
from test_cli import FakeMac as LaptopMac, GenericTree   # noqa: E402

GB = 10 ** 9
DISKS = {
    'disk0': dict(WholeDisk=True, DeviceTreePath='IODeviceTree:/disk0', Internal=True, VirtualOrPhysical='Physical', BusProtocol='SATA', TotalSize=500 * GB,
                  MediaName='APPLE SSD SM512E'),
    'disk1': dict(WholeDisk=True, Internal=True, VirtualOrPhysical='Virtual', TotalSize=499 * GB),
    'disk3': dict(WholeDisk=True, Internal=False, VirtualOrPhysical='Virtual', TotalSize=100 * GB,
                  MediaName='Disk Image'),
    'disk4': dict(WholeDisk=True, DeviceTreePath='IODeviceTree:/disk4', Internal=False, VirtualOrPhysical='Physical', BusProtocol='USB', Removable=True,
                  TotalSize=62 * GB, MediaName='SanDisk Ultra'),
    'disk6': dict(WholeDisk=True, DeviceTreePath='IODeviceTree:/disk6', Internal=False, VirtualOrPhysical='Physical', BusProtocol='USB', Removable=True,
                  TotalSize=16 * GB, MediaName='Small Stick'),
}
SERIAL, UUID = 'C02XK1ABDV33', '1A2B3C4D-1111-2222-3333-444455556666'
NAME = 'alice'
HOME = os.path.join('/', 'Users', NAME)          # built, so this file names no home folder


class FakeMac(LaptopMac):
    def __init__(self, listed=('disk0', 'disk1', 'disk3', 'disk4', 'disk6'), disks=None, **kw):
        kw.setdefault('root_device', 'disk1s5s1')
        super().__init__(**kw)
        self.listed, self.disks = list(listed), dict(DISKS if disks is None else disks)

    def answer(self, argv):
        if argv == ['diskutil', 'list', '-plist', 'external', 'physical']:
            return plistlib.dumps({'WholeDisks': self.listed, 'AllDisks': self.listed})
        if argv[:3] == ['diskutil', 'info', '-plist'] and argv[3] != '/':
            info = self.disks.get(argv[3])
            return None if info is None else plistlib.dumps(dict(info, DeviceIdentifier=argv[3]))
        if argv[0] == 'ioreg' and 'IODeviceTree' in argv and 'efi' in argv:
            vendor = getattr(self, 'firmware', 'Apple')
            return plistlib.dumps([{'firmware-vendor': (vendor + '\0').encode('utf-16-le')}] if vendor else [])
        if argv[0] == 'ioreg' and 'IOPlatformExpertDevice' in argv:
            return plistlib.dumps([{'board-id': self.board, 'model': self.model.encode() + b'\0',
                                    'IOPlatformSerialNumber': SERIAL, 'IOPlatformUUID': UUID}])
        if argv == ['sw_vers']:
            return b'ProductName:\t\tmacOS\nProductVersion:\t\t27.2.1\nBuildVersion:\t\t26B131\n'
        if argv == ['scutil', '--get', 'ComputerName']:
            return b"Alice's MacBook Pro"
        if argv == ['scutil', '--get', 'LocalHostName']:
            return b'Alices-MacBook-Pro'
        if argv == ['hostname']:
            return b'Alices-MacBook-Pro.local'
        if argv == ['id', '-F', NAME]:
            return b'Alice Example'
        if argv == ['uname', '-v']:
            return b'Darwin Kernel Version 27.2.0'
        if argv[:2] == ['hdiutil', 'detach'] or argv[:2] == ['diskutil', 'eject']:
            return b''
        if argv == ['diskutil', 'list', '-plist', 'internal', 'physical']:
            internal = [d for d in self.disks if self.disks[d].get('Internal') and
                        self.disks[d].get('VirtualOrPhysical') == 'Physical']
            return plistlib.dumps({'WholeDisks': internal})
        return super().answer(argv)


def ns(**kw):
    base = dict(model=None, profile=None, assembly=None, dry_run=True, disk=None, confirm=None, quiet=False,
                show=False, show_edits=False, log=None, minimum_bytes=None, release=None, cache=None, kit=None,
                work=None, basesystem=None, image=None, image_size=None, opencore=None)
    base.update(kw)
    return argparse.Namespace(**base)


def console(answers, level='steps'):
    """A console whose questions get ANSWERS in turn; running out of answers is EOF (no keyboard)."""
    out = io.StringIO()
    answers = list(answers)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        if not answers:
            raise EOFError
        return answers.pop(0)
    c = CON.Console(level, out=out, ask=ask)
    return c, out, asked


class FakeAcquire:
    """tools/acquire's load/final_path/fetch over a release.json; fetch records the rows instead of downloading."""

    def __init__(self, fail=None):
        self.fetched, self.fail = [], fail
        real = __import__('runpy').run_path(str(INST.ACQUIRE))
        self.api = {'load': real['load'], 'final_path': real['final_path'], 'intact': real['intact'],
                    'AcquireError': real['AcquireError'], 'fetch': self.fetch}
        self.Error = real['AcquireError']

    def fetch(self, rows, cache, on_event=None):
        if self.fail:
            raise self.Error(self.fail)
        for r in rows:
            self.fetched.append(r['name'])
            on_event({'ev': 'progress', 'name': r['name'], 'done': r['size'], 'size': r['size']})
            on_event({'ev': 'ok', 'name': r['name'], 'sha256': r['sha256']})


class FakeWriter:
    """Stands in for the kit staging, the input lookup and stick.write: records the call, writes nothing."""

    def __init__(self, fail=None):
        self.calls, self.attached, self.fail = [], [], fail

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.saved = (INST.prepare_kit, INST.stick_inputs, INST.STICK.write, INST.STICK.attach_image,
                      INST.STICK.prepare, INST.opencore_input)
        INST.prepare_kit = lambda a, c, path: d / 'kit'
        INST.stick_inputs = lambda a, asm: (d / 'BaseSystem.dmg', d / 'esp.tar')
        INST.opencore_input = lambda a, asm: d / 'esp.tar'
        self.prepared = []
        INST.STICK.prepare = lambda host, disk, *a, **k: self.prepared.append(disk) or {
            INST.STICK.KIT_NAME: ('%ss3' % disk, str(d / 'HAO'))}
        INST.STICK.attach_image = lambda host, image, size: self.attached.append((image, size)) or 'disk9'

        def write(host, disk, *args, **kw):
            self.calls.append(((disk,) + args, kw))
            if self.fail:
                raise INST.STICK.StickError(self.fail)
            return dict(basesystem_sha256='ab' * 32, kit_bytes=10 * GB)
        INST.STICK.write = write
        return self

    def __exit__(self, *exc):
        (INST.prepare_kit, INST.stick_inputs, INST.STICK.write, INST.STICK.attach_image, INST.STICK.prepare,
         INST.opencore_input) = self.saved
        self.tmp.cleanup()


class Release:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.path, self.cache = d / 'release.json', d / 'cache'
        rows = []
        for name, data in (('UniversalMac_27.2.1_26B131_Restore.ipsw', b'apple'), ('shims.tar', b'ours')):
            rows.append({'name': name, 'url': 'http://127.0.0.1:9/' + name, 'size': len(data),
                         'sha256': hashlib.sha256(data).hexdigest()})
        self.path.write_text(json.dumps({'release': '27.2.1-26B131.0', 'hao_patcher': 'abc123',
                                         'assembly': 'mbp92-26B131', 'proven_models': ['MacBookPro9,2'],
                                         'downloads': rows}))
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()


class TwoProfiles(Release):
    """A release cut for two image profiles: the 9,2 image (first, the top level) and the generic one; one shared
    download, one asset each of their own."""

    def __enter__(self):
        super().__enter__()
        rel = json.loads(self.path.read_text())
        def entry(row, name, data):
            return {'row': row, 'name': name, 'url': 'http://127.0.0.1:9/' + name, 'size': len(data),
                    'sha256': hashlib.sha256(data).hexdigest()}
        rel['downloads'] = [entry('ipsw_27_2_1', 'UniversalMac_27.2.1_26B131_Restore.ipsw', b'apple')]
        rel['assets'] = [dict(entry('esp_mbp92', 'esp_mbp92.tar', b'nine'), file='esp_mbp92.tar', format='tar'),
                         dict(entry('esp_generic', 'esp_generic.tar', b'generic!'), file='esp_generic.tar',
                              format='tar')]
        for x in rel['assets']:
            del x['name']
        rel['harvested'] = []
        rel['target'], rel['patch_set'] = 'mbp92', 'golden-gate-26B131'
        rel['profiles'] = {
            'golden-gate-26B131': {'assembly': 'mbp92-26B131', 'target': 'mbp92', 'patch_set': 'golden-gate-26B131',
                                   'proven_models': ['MacBookPro9,2'], 'rows': ['esp_mbp92', 'ipsw_27_2_1'],
                                   'harvested': []},
            'generic': {'assembly': 'generic-26B131', 'target': 'generic', 'patch_set': 'generic',
                        'proven_models': [], 'rows': ['esp_generic', 'ipsw_27_2_1'], 'harvested': ['caches']}}
        self.path.write_text(json.dumps(rel))
        self.tree = GenericTree().__enter__()
        return self

    def __exit__(self, *exc):
        self.tree.__exit__(*exc)
        super().__exit__(*exc)


class GenericImageTest(unittest.TestCase):
    """create-installer and the generic image: offered when the release carries it, made only when chosen."""

    def test_offered_last_and_chosen(self):
        host = FakeMac()
        with TwoProfiles() as r:
            c, out, asked = console(['4', '1'])          # the generic image, the first stick
            a = ns(release=str(r.path), cache=str(r.cache))
            self.assertEqual(INST.create_installer(a, c, host), 0)
        text = out.getvalue()
        self.assertIn('    1  MacBookPro9,2', text)
        self.assertIn('4  A virtual machine or a non-Apple PC (not a Mac): the generic image: software graphics', text)
        self.assertEqual((a.image_profile, a.assembly), ('generic', 'generic-26B131'))
        self.assertEqual(a.release_view['profile'], 'generic')
        self.assertIn('2 files, 13 B', text)             # the shared download and the generic asset, not the 9,2's
        self.assertTrue(a.unproven_reasons[0].startswith('the generic image generic'))
        self.assertEqual(writes(host), [])

    def test_selected_profile_owns_its_cache_rebuild_plan(self):
        with TwoProfiles() as r:
            release=json.loads(r.path.read_text())
            names=list(release['profiles'])
            release['install_cache_rebuild']=True
            release['profiles'][names[0]]['install_cache_rebuild']=True
            release['profiles'][names[1]]['install_cache_rebuild']=False
            self.assertTrue(INST.release_view(release,names[0])['install_cache_rebuild'])
            self.assertFalse(INST.release_view(release,names[1])['install_cache_rebuild'])

    def test_the_9_2_downloads_only_its_own(self):
        host = FakeMac(uid=0)
        with TwoProfiles() as r, FakeWriter() as w:
            fake = FakeAcquire()
            c, _, _ = console(['', '1', 'disk4'])
            a = ns(dry_run=False, release=str(r.path), cache=str(r.cache))
            self.assertEqual(INST.create_installer(a, c, host, acquire=fake.api), 0)
            view = json.loads(Path(a.release_view['path']).read_text())
            # the USB installer carries the signed release (beside its .sig), not the unsigned view
            self.assertEqual(Path(w.calls[0][1]['release']), r.path)
        self.assertEqual(sorted(fake.fetched), ['UniversalMac_27.2.1_26B131_Restore.ipsw', 'esp_mbp92.tar'])
        rec = w.calls[0][1]['record']
        self.assertEqual((rec['model'], rec['target'], rec['assembly'], rec['unproven']),
                         ('MacBookPro9,2', 'mbp92', 'mbp92-26B131', []))
        self.assertNotIn('profile', rec)
        self.assertEqual((view['profile'], view['assembly'], view['target']),
                         ('golden-gate-26B131', 'mbp92-26B131', 'mbp92'))
        self.assertNotIn('profiles', view)

    def test_profile_generic_flag_writes_its_view_and_record(self):
        host = FakeMac(uid=0)
        with TwoProfiles() as r, FakeWriter() as w:
            fake = FakeAcquire()
            c, _, asked = console([])
            a = ns(dry_run=False, profile='generic', disk='disk4', confirm='disk4', release=str(r.path),
                   cache=str(r.cache))
            self.assertEqual(INST.create_installer(a, c, host, acquire=fake.api), 0)
            view = json.loads(Path(a.release_view['path']).read_text())
            self.assertEqual(Path(w.calls[0][1]['release']), r.path)     # the signed release, not the view
        self.assertEqual(asked, [])
        self.assertEqual(sorted(fake.fetched), ['UniversalMac_27.2.1_26B131_Restore.ipsw', 'esp_generic.tar'])
        rec = w.calls[0][1]['record']
        self.assertEqual((rec['profile'], rec['target'], rec['assembly']), ('generic', 'generic', 'generic-26B131'))
        self.assertTrue(rec['unproven'])                  # the stick's install launcher passes --unproven
        self.assertEqual((view['assembly'], view['harvested'], [x['row'] for x in view['assets']]),
                         ('generic-26B131', ['caches'], ['esp_generic']))
        self.assertEqual(Path(a.release_view['path']).parent, r.cache / 'releases')

    def test_this_unknown_mac_is_asked_and_stops_by_default(self):
        mini = dict(model='Macmini6,2', board=b'Mac-F65AE981FFA204ED\0')
        with TwoProfiles() as r:
            c, out, _ = console(['2', '2', '1'])         # This Mac, yes the generic image, the first stick
            a = ns(release=str(r.path), cache=str(r.cache), unproven=True)     # a Mac: only with --unproven
            self.assertEqual(INST.create_installer(a, c, FakeMac(**mini)), 0)
            self.assertIn('This Mac (Macmini6,2) is not a model this release can install on', out.getvalue())
            self.assertEqual((a.image_profile, a.assembly, a.profile_data['model']),
                             ('generic', 'generic-26B131', 'Macmini6,2'))
            c, _, _ = console(['2', ''])                 # This Mac, then Return: the default is to stop
            with self.assertRaises(CON.Stopped) as x:
                INST.create_installer(ns(release=str(r.path), cache=str(r.cache), unproven=True), c, FakeMac(**mini))
            self.assertIn('not one this release can install on', x.exception.what)
            c, out, _ = console(['2'])                   # without --unproven a Mac is not offered the generic image
            with self.assertRaises(CON.Stopped) as x:
                INST.create_installer(ns(release=str(r.path), cache=str(r.cache)), c, FakeMac(**mini))
            self.assertNotIn('Make the generic image', out.getvalue())

    def test_a_release_without_the_profile_refuses_it(self):
        with Release() as r, GenericTree():
            c, _, _ = console(['1'])
            with self.assertRaises(CON.Stopped) as x:
                INST.create_installer(ns(release=str(r.path), cache=str(r.cache), profile='generic'), c, FakeMac())
            self.assertIn('has no image profile generic', x.exception.what)
            c, out, _ = console(['', '1'])               # and does not offer it
            INST.create_installer(ns(release=str(r.path), cache=str(r.cache)), c, FakeMac())
            self.assertNotIn('generic image', out.getvalue())

    def test_view(self):
        with TwoProfiles() as r:
            rel = json.loads(r.path.read_text())
        v = INST.release_view(rel, 'generic')
        self.assertEqual((v['profile'], v['assembly'], v['target'], v['patch_set']),
                         ('generic', 'generic-26B131', 'generic', 'generic'))
        self.assertEqual([x['row'] for x in v['downloads'] + v['assets']], ['ipsw_27_2_1', 'esp_generic'])
        self.assertEqual(INST.release_assembly(rel), 'mbp92-26B131')
        self.assertEqual(INST.release_assembly(rel, 'generic'), 'generic-26B131')


def writes(host):
    return [c for c in host.calls if any(x in c for x in ('eraseDisk', 'partitionDisk', 'unmountDisk', 'apfs',
                                                           'newfs_apfs', 'dd')) and c[:3] != ['diskutil', 'apfs', 'list']]


def passing_doctor(purpose, where, release, cache, offline=False):
    """hao doctor's survey with every check ok: the tests below do not ask this Mac (Xcode, the network)."""
    return [dict(name='python', state='ok', detail='Python 3.14', fix=None)]


def setUpModule():
    global REAL_DOCTOR
    REAL_DOCTOR, INST.DOCTOR_CHECKS = INST.DOCTOR_CHECKS, passing_doctor


def tearDownModule():
    INST.DOCTOR_CHECKS = REAL_DOCTOR


class DoctorFirstTest(unittest.TestCase):
    """create-installer runs hao doctor --for create-installer before any question; a failed check stops it (5)."""

    def test_a_failed_check_stops_before_any_question(self):
        calls = []

        def failing(purpose, where, release, cache, offline=False):
            calls.append((purpose, offline))
            return [dict(name='python', state='ok', detail='Python 3.14', fix=None),
                    dict(name='xcode', state='fail', detail='no Xcode', fix='install Xcode 27.2')]
        INST.DOCTOR_CHECKS = failing
        try:
            host = FakeMac(uid=0)
            with Release() as r:
                c, out, asked = console(['', '1', 'disk4'])
                with self.assertRaises(CON.Stopped) as x:
                    INST.create_installer(ns(dry_run=False, release=str(r.path), cache=str(r.cache)), c, host)
                self.assertFalse(r.cache.exists())
        finally:
            INST.DOCTOR_CHECKS = passing_doctor
        self.assertEqual(x.exception.code, 5)
        self.assertEqual(x.exception.what, '1 check failed: xcode')
        self.assertIn('install Xcode 27.2', x.exception.options)
        self.assertEqual(asked, [])
        self.assertEqual(calls, [('create-installer', False)])
        self.assertEqual([e['name'] for e in c.events if e['ev'] == 'doctor.check'], ['python', 'xcode'])
        self.assertEqual(writes(host), [])

    def test_passing_checks_go_on_to_the_questions(self):
        c, out, asked = console(['', '1'])
        self.assertEqual(INST.create_installer(ns(), c, FakeMac()), 0)
        self.assertEqual(asked, ['Choice [1]: ', 'Choice: '])
        self.assertEqual(c.events[0]['ev'], 'doctor.check')


class InstallQuestionTest(unittest.TestCase):
    """hao install in the stick's Recovery with no disk named: it asks which internal disk."""

    def test_asks_for_the_internal_disk(self):
        import builder as BUILD
        host = FakeMac(root_device='disk9s1')
        c, out, asked = console(['1'])
        a = ns(model='MacBookPro9,2', dry_run=True)
        self.assertEqual(BUILD.install(a, c, host), 0)
        self.assertEqual(a.disk, 'disk0')
        self.assertIn('Which disk should macOS 27 be installed on?', out.getvalue())
        self.assertIn('APPLE SSD SM512E', out.getvalue())

    def test_running_system_disk_cannot_be_chosen(self):
        import builder as BUILD
        host = FakeMac()                              # the running system is on disk0 (container disk1)
        c, out, _ = console([])
        with self.assertRaises(CON.Stopped) as x:
            BUILD.install(ns(model='MacBookPro9,2', dry_run=True), c, host)
        self.assertEqual(x.exception.what, 'no internal disk macOS 27 can be installed on')
        self.assertIn('holds the running system', out.getvalue())


class CreateInstallerTest(unittest.TestCase):
    def test_every_input_place_protects_its_drive(self):
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'Volumes/Ext/kit'; kit.mkdir(parents=True)
            link = Path(d) / 'bs.dmg'; os.symlink(kit / 'BaseSystem.dmg', link)
            a = ns(kit=str(kit), basesystem=str(link), work=str(Path(d) / 'Volumes/Ext/work/not-yet'))
            got = INST.input_paths(a, Path(d) / 'cache', Path(d) / 'release.json')
            self.assertIn(str(kit), got)
            self.assertIn(str((Path(d) / 'Volumes/Ext/kit').resolve()), got)      # the link's target folder
            self.assertIn(str(Path(d) / 'Volumes/Ext'), got)                       # nearest existing folder of --work

    def test_folder_must_be_new_or_empty(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'old').write_text('x')
            c, out, _ = console([])
            with self.assertRaises(CON.Stopped) as x:
                INST.create_installer(ns(folder=d, dry_run=False), c, FakeMac())
            self.assertIn('is not a new or empty folder', x.exception.what)

    def test_staged_kit_is_removed_after_the_write_unless_kept(self):
        with tempfile.TemporaryDirectory() as d:
            def staged():
                kit = Path(d) / 'work/stage/share'
                kit.mkdir(parents=True, exist_ok=True)
                (kit / 'kit.json').write_text('{}')
                (kit / 'big.dmg').write_bytes(b'x' * 4096)
                return kit
            kit = staged()
            c, out, _ = console([])
            left = INST.finish_kit(ns(), c, kit)
            self.assertFalse((Path(d) / 'work/stage').exists())
            self.assertIn('freed on this Mac', left[0])
            self.assertIn('removed the staged kit', out.getvalue())
            self.assertEqual(c.events[-1]['ev'], 'kit.removed')
            kit = staged()
            self.assertIn('--keep-kit', INST.finish_kit(ns(keep_kit=True), c, kit)[0])
            self.assertTrue(kit.is_dir())
            self.assertEqual(INST.finish_kit(ns(kit=str(kit)), c, kit), [])     # a kit given with --kit is not ours
            self.assertTrue(kit.is_dir())

    def test_the_plan_counts_the_staged_kit_and_its_removal(self):
        import space as SPACE
        p = SPACE.plan({'downloads': [], 'assets': []})
        stages = {name: (mac, drive) for name, mac, drive, _ in p['stages']}
        self.assertAlmostEqual(stages['write'][0] - stages['after'][0], SPACE.load()['measured']['kit_stage_mac_gb'])
        self.assertEqual(p['mac_peak'], max(m for m, _ in stages.values()))

    def test_dry_run_asks_and_shows_the_plan(self):
        host = FakeMac()
        with Release() as r:
            c, out, asked = console(['', '1'])          # the default Mac, the first stick
            a = ns(release=str(r.path), cache=str(r.cache))
            self.assertEqual(INST.create_installer(a, c, host), 0)
            self.assertFalse(r.cache.exists())          # nothing downloaded
        text = out.getvalue()
        self.assertIn('Which Mac is the installer for?', text)
        self.assertIn('    1  MacBookPro9,2', text)
        self.assertIn('2  This Mac (', text)           # an Intel Mac may detect itself
        self.assertIn('Which disk is the USB drive?', text)
        self.assertIn('    1  disk4  SanDisk Ultra 62.0 GB', text)
        self.assertIn('-  disk6  Small Stick 16.0 GB  it is too small: a USB drive of at least 64 GB is needed', text)
        for never in ('disk0', 'disk1', 'disk3'):      # internal, the running system's, a disk image
            self.assertNotIn(never, text)
        self.assertEqual(asked, ['Choice [1]: ', 'Choice: '])
        self.assertIn('[1/3] Download', text)
        self.assertIn('2 files, 9 B, each checked by size and sha256', text)
        self.assertIn('[3/3] Write the USB installer', text)
        self.assertIn('dry run: nothing was downloaded or written', text)
        self.assertNotIn('Type disk4', text)             # no confirmation in a dry run
        self.assertEqual(a.disk, 'disk4')
        self.assertEqual(a.model, 'MacBookPro9,2')
        evs = [e for e in c.events if e['ev'] in ('question', 'answer')]
        self.assertEqual([(e['ev'], e['key']) for e in evs], [('question', 'mac'), ('answer', 'mac'),
                                                               ('question', 'disk'), ('answer', 'disk')])
        self.assertEqual(writes(host), [])

    def test_bad_answers_ask_again(self):
        c, out, asked = console(['x', '9', '1', '', '2', '1'])
        a = ns(dry_run=True)
        self.assertEqual(INST.create_installer(a, c, FakeMac()), 0)
        self.assertEqual(a.disk, 'disk4')
        self.assertIn('Type a number from 1 to 4.', out.getvalue())   # 4: this checkout's generic image
        self.assertIn('Type a number from 1 to 1.', out.getvalue())
        self.assertIn('no release.json beside hao', out.getvalue())

    def test_no_keyboard(self):
        c, _, _ = console([])
        with self.assertRaises(CON.Stopped) as x:
            INST.create_installer(ns(), c, FakeMac())
        self.assertIn('got none', x.exception.what)
        self.assertIn('./hao create-installer', x.exception.options[0])
        self.assertNotIn('sudo', x.exception.options[0])

    def test_refuses_internal_system_image_and_small_disks(self):
        for disk, words in (('/dev/disk0', "this Mac's internal disk"), ('disk1', 'disk image'),
                            ('disk3', 'disk image'), ('disk6', 'too small'), ('disk4s1', 'whole disk'),
                            ('disk9', 'no such disk')):
            c, _, asked = console([])
            with self.assertRaises(CON.Stopped) as x:
                INST.create_installer(ns(model='MacBookPro9,2', disk=disk), c, FakeMac())
            self.assertIn(words, x.exception.what, disk)
            self.assertIn('nothing was downloaded or written', x.exception.untouched)
            self.assertEqual(asked, [])
        # the boot disk is refused even when it is external
        host = FakeMac(root_device='disk4s1')
        c, _, _ = console([])
        with self.assertRaises(CON.Stopped) as x:
            INST.create_installer(ns(model='MacBookPro9,2', disk='disk4'), c, host)
        self.assertIn('the macOS you are running', x.exception.what)

    def test_no_usable_stick_waits_then_quits(self):
        host = FakeMac(listed=['disk0', 'disk6'])
        c, out, asked = console(['', 'q'])
        with self.assertRaises(CON.Stopped) as x:
            INST.create_installer(ns(model='MacBookPro9,2'), c, host)
        self.assertEqual(x.exception.what, 'no USB drive chosen')
        self.assertEqual(len(asked), 2)
        self.assertEqual(out.getvalue().count('No USB drive found. Plug in a USB drive of at least 64 GB.'), 2)
        self.assertIn('disk6  Small Stick 16.0 GB: it is too small', out.getvalue())

    def test_stick_holding_hao_is_not_offered(self):
        host = FakeMac()
        host.df[str(INST.TOP)] = 'disk4s2'
        c, out, _ = console(['1'])
        with self.assertRaises(CON.Stopped):              # disk6 is too small, disk4 holds hao: nothing to choose
            INST.create_installer(ns(model='MacBookPro9,2'), c, host)
        self.assertIn('it holds this hao folder or its downloads', out.getvalue())

    def test_needs_a_release_not_root(self):
        c, _, asked = console([])
        with self.assertRaises(CON.Stopped) as x:          # as a user: no refusal for that; a release is still needed
            INST.create_installer(ns(dry_run=False, release='/nonexistent/release.json'), c, FakeMac())
        self.assertIn('no release.json', x.exception.what)
        self.assertEqual(asked, [])                       # stopped before any question
        c, _, _ = console([])
        with self.assertRaises(CON.Stopped) as x:
            INST.create_installer(ns(dry_run=False, release='/nonexistent/release.json'), c, FakeMac(uid=0))
        self.assertIn('no release.json', x.exception.what)

    def test_a_user_is_asked_for_the_password_once_just_to_erase(self):
        import subprocess as sp
        asked = []
        ok = lambda argv: asked.append(argv) or sp.CompletedProcess(argv, 0)
        c, out, _ = console([])
        INST.administrator(c, FakeMac(), 'disk4', run=ok)
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0][:2], ['sudo', '-v'])
        self.assertIn('/dev/disk4', asked[0][-1])
        INST.administrator(c, FakeMac(uid=0), 'disk4', run=ok)      # as root: nothing asked
        self.assertEqual(len(asked), 1)
        with self.assertRaises(CON.Stopped) as x:
            INST.administrator(c, FakeMac(), 'disk4', run=lambda argv: sp.CompletedProcess(argv, 1))
        self.assertIn('not erased', x.exception.what)

    def test_confirmation_is_the_disk_name(self):
        host = FakeMac(uid=0)
        with Release() as r:
            for typed in ('ERASE', 'disk5', 'yes'):
                fake = FakeAcquire()
                c, out, asked = console(['', '1', typed])
                with self.assertRaises(CON.Stopped) as x:
                    INST.create_installer(ns(dry_run=False, release=str(r.path), cache=str(r.cache)), c, host,
                                          acquire=fake.api)
                self.assertEqual(x.exception.what, 'not confirmed')
                self.assertEqual(fake.fetched, [])
                self.assertEqual(asked[-1], 'Type disk4 to continue: ')
                self.assertIn('Everything on disk4  SanDisk Ultra 62.0 GB will be erased.', out.getvalue())
        self.assertEqual(writes(host), [])

    def test_downloads_prepares_and_writes(self):
        host = FakeMac(uid=0)
        with Release() as r, FakeWriter() as w:
            fake = FakeAcquire()
            c, out, _ = console(['', '1', 'disk4'])
            self.assertEqual(INST.create_installer(ns(dry_run=False, release=str(r.path), cache=str(r.cache)), c,
                                                   host, acquire=fake.api), 0)
        self.assertEqual(fake.fetched, ['UniversalMac_27.2.1_26B131_Restore.ipsw', 'shims.tar'])
        (disk, basesystem, opencore, kit, top), kw = w.calls[0]
        self.assertEqual((disk, basesystem.name, opencore.name, kit.name, top), ('disk4', 'BaseSystem.dmg', 'esp.tar',
                                                                              'kit', INST.TOP))
        self.assertEqual(kw['release'], r.path)
        self.assertEqual(kw['record']['model'], 'MacBookPro9,2')
        self.assertEqual(kw['record']['target'], 'mbp92')
        text = out.getvalue()
        self.assertIn('[1/3] Download', text)
        self.assertIn('2 files, 9 B', text)
        self.assertIn('[2/3] Prepare packs', text)
        self.assertIn('6 packs', text)
        self.assertIn('[3/3] Write the USB installer', text)
        self.assertIn('/Volumes/HAO/install', text)
        self.assertIn('ejected: you can unplug it', text)
        self.assertIn(['diskutil', 'eject', '/dev/disk4'], host.calls)
        self.assertEqual(writes(host), [])                # the fake writer touched no disk

    def test_write_failure_says_the_stick_is_incomplete(self):
        host = FakeMac(uid=0)
        with Release() as r, FakeWriter(fail='partitionDisk failed (1): busy') as w:
            c, _, _ = console(['', '1', 'disk4'])
            with self.assertRaises(CON.Stopped) as x:
                INST.create_installer(ns(dry_run=False, release=str(r.path), cache=str(r.cache)), c, host,
                                      acquire=FakeAcquire().api)
        self.assertEqual(x.exception.what, 'writing the USB installer failed')
        self.assertIn('busy', x.exception.cause)
        self.assertIn(INST.REPORT, x.exception.options)

    def test_image_needs_no_root_disk_or_confirmation(self):
        host = FakeMac(uid=501)
        with FakeWriter() as w, tempfile.TemporaryDirectory() as d:
            c, _, asked = console([])
            a = ns(dry_run=False, model='MacBookPro9,2', release='/nonexistent/release.json',
                   image=str(Path(d) / 'stick.img'), image_size=64 * GB)
            self.assertEqual(INST.create_installer(a, c, host), 0)
        self.assertEqual(asked, [])
        self.assertEqual(w.attached, [((Path(d) / 'stick.img').resolve(), 64 * GB)])
        self.assertEqual(w.calls[0][0][0], 'disk9')
        self.assertIsNone(w.calls[0][1]['release'])
        self.assertIn(['hdiutil', 'detach', '/dev/disk9'], host.calls)

    def test_a_given_kit_needs_no_release_and_downloads_nothing(self):
        host = FakeMac(uid=0)
        with FakeWriter() as w, tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'
            kit.mkdir()
            (kit / 'kit.json').write_text(json.dumps({'schema': 1, 'assembly': 'mbp92-26B131'}))
            fake = FakeAcquire()
            c, out, _ = console([])
            a = ns(dry_run=False, model='MacBookPro9,2', disk='disk4', confirm='disk4', kit=str(kit),
                   release='/nonexistent/release.json')
            self.assertEqual(INST.create_installer(a, c, host, acquire=fake.api), 0)
        self.assertEqual(fake.fetched, [])
        self.assertEqual(a.assembly, 'mbp92-26B131')
        self.assertIn('nothing is downloaded', out.getvalue())
        self.assertIsNone(w.calls[0][1]['release'])

    def test_flags_answer_every_question(self):
        host = FakeMac(uid=0)
        with Release() as r, FakeWriter():
            fake = FakeAcquire()
            c, _, asked = console([])
            a = ns(dry_run=False, model='MacBookPro9,2', disk='/dev/disk4', confirm='disk4', release=str(r.path),
                   cache=str(r.cache))
            self.assertEqual(INST.create_installer(a, c, host, acquire=fake.api), 0)
        self.assertEqual(asked, [])
        self.assertEqual(len(fake.fetched), 2)

    def test_download_failure(self):
        host = FakeMac(uid=0)
        with Release() as r, FakeWriter() as w:
            c, _, _ = console(['', '1', 'disk4'])
            with self.assertRaises(CON.Stopped) as x:
                INST.create_installer(ns(dry_run=False, release=str(r.path), cache=str(r.cache)), c, host,
                                      acquire=FakeAcquire(fail='x.ipsw: no source gave the pinned file').api)
        self.assertEqual(x.exception.what, 'a download failed')
        self.assertIn(INST.REPORT, x.exception.options)
        self.assertEqual(w.prepared, ['disk4'])          # the drive is erased first, then filled
        self.assertIn('holds no installer yet', x.exception.untouched)

    def test_this_mac_and_profile_file(self):
        c, out, _ = console(['2', '1'])                 # This Mac (detected), first stick
        a = ns()
        self.assertEqual(INST.create_installer(a, c, FakeMac()), 0)
        self.assertEqual(a.profile_data['target'], 'mbp92')
        self.assertIn('intel-hd4000', out.getvalue())
        # an Apple silicon Mac is not offered "This Mac"; the profile file is choice 2 there
        with tempfile.TemporaryDirectory() as d:
            prof = Path(d) / 'my mac.json'
            prof.write_text(json.dumps(DETECT.for_model('MacBookPro9,2')))
            c, out, _ = console(['2', "'%s' " % prof, '1'])
            a = ns()
            self.assertEqual(INST.create_installer(a, c, FakeMac(machine='arm64')), 0)
            self.assertNotIn('This Mac (', out.getvalue())
            self.assertEqual(a.profile, str(prof))
        # an Intel Mac this release has no target for: asked about the checkout's generic image, stops by default
        c, _, _ = console(['2', ''])
        with self.assertRaises(CON.Stopped) as x:
            INST.create_installer(ns(), c, FakeMac(model='Macmini6,2', board=b'Mac-F65AE981FFA204ED\0'))
        self.assertIn('not one this release can install on', x.exception.what)


class ReportTest(unittest.TestCase):
    ENV = {'USER': NAME, 'SUDO_USER': NAME, 'LOGNAME': 'root', 'HOME': HOME}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.logs, self.state = self.d / 'logs', self.d / 'state'
        self.state.mkdir()
        events = [{'ev': 'command', 'argv': [HOME + '/Downloads/hao/hao', 'create-installer']},
                  {'ev': 'note', 'text': 'IOPlatformSerialNumber = "%s"' % SERIAL},
                  {'ev': 'note', 'text': 'Wi-Fi SSID: HomeNetwork5G, owner alice@example.com, en1 a4:83:e7:01:02:03'},
                  {'ev': 'run.end', 'ok': False, 'what': 'a download failed on Alices-MacBook-Pro'}]
        old = REP.save_log('build-disk', ['build-disk'], [], self.logs, ver={},
                           now=datetime.datetime(2026, 11, 12, 9, 0, 0))
        os.utime(old, (1, 1))
        self.log = REP.save_log('create-installer', ['create-installer'], events, self.logs, ver={'commit': 'abc'},
                                now=datetime.datetime(2026, 11, 13, 10, 0, 0))
        (self.state / 'install-record.json').write_text(json.dumps({'disk': 'disk0', 'serial': SERIAL,
                                                                    'local': HOME + '/hao-install'}))

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self):
        return REP.collect(FakeMac(), dict(self.ENV), [self.logs], self.state,
                           ver={'release': '27.2.1-26B131.0', 'commit': 'abc123'},
                           now=datetime.datetime(2026, 11, 13, 11, 0, 0))

    def test_contents(self):
        text = self.collect()
        for want in ('hao report  2026-11-13 11:00:00', 'For a GitHub issue. Removed before writing', '== hao',
                     'release         27.2.1-26B131.0', 'commit          abc123', '== This Mac',
                     'ProductVersion:\t\t27.2.1', 'model          MacBookPro9,2', 'memory         16 GB',
                     'machine        x86_64', '== hao detect', '"board_id": "Mac-6F01561E16C75D06"',
                     '"target": "mbp92"', '== Last command (create-installer-20261113-100000.json)',
                     'a download failed', '== Earlier command logs', 'build-disk-20261112-090000.json',
                     '== Install record', '"disk": "disk0"'):
            self.assertIn(want, text)

    def test_no_private_data(self):
        text = self.collect()
        for secret in (SERIAL, UUID, NAME, 'Alice', 'Alices-MacBook-Pro', 'HomeNetwork5G', 'example.com',
                       'a4:83:e7', HOME):
            self.assertNotIn(secret, text)
        self.assertIn(os.path.join('/', 'Users', 'USER', 'Downloads', 'hao', 'hao'), text)
        self.assertIn('[serial]', text)
        self.assertIn('[computer name]', text)

    def test_scrub(self):
        self.assertEqual(REP.scrub(os.path.join('/', 'Users', 'bob', 'x') + ' /Users/Shared/y', {}),
                         os.path.join('/', 'Users', 'USER', 'x') + ' /Users/Shared/y')
        self.assertEqual(REP.scrub('"IOPlatformSerialNumber": "ABC"', {}), '"IOPlatformSerialNumber": "[removed]"')
        self.assertEqual(REP.scrub('Serial Number (system): XYZ123', {}), 'Serial Number (system): [removed]')
        self.assertEqual(REP.scrub('SSID: Home Net', {}), 'SSID: [wifi]')
        # a secret is replaced as a whole word only
        self.assertEqual(REP.scrub('bob bobcat', {'bob': 'USER'}), 'USER bobcat')

    def test_no_logs(self):
        text = REP.collect(FakeMac(), dict(self.ENV), [self.d / 'none'], self.d / 'nostate', ver={})
        self.assertIn('no hao command log', text)
        self.assertIn('Install record\nnone', text)
        self.assertIn('version unknown', text)

    def test_save_log_keeps_the_newest(self):
        folder = self.d / 'many'
        for i in range(REP.KEEP + 3):
            p = REP.save_log('install', [], [], folder, ver={}, now=datetime.datetime(2026, 1, 1, 0, 0, i))
            os.utime(p, (i, i))
        names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(len(names), REP.KEEP)
        self.assertEqual(names[0], 'install-20260101-000003.json')

    def test_report_writes_the_file(self):
        out = self.d / 'r.txt'
        c, text, _ = console([])
        a = argparse.Namespace(output=str(out))
        self.assertEqual(REP.report(a, c, FakeMac(), dict(self.ENV), [self.logs], self.state, ver={}), 0)
        self.assertTrue(out.read_text().startswith('hao report'))
        self.assertIn('report written to %s' % out.resolve(), text.getvalue())
        self.assertIn('GitHub issue', text.getvalue())
        self.assertEqual(REP.default_output(datetime.datetime(2026, 11, 13, 1, 2, 3), self.d),
                         self.d / 'hao-report-20261113-010203.txt')


class CommandLineTest(unittest.TestCase):
    def hao(self, *argv, env=None):
        return subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'hao')] + list(argv), capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, env=dict(os.environ, **(env or {})))

    def test_create_installer_without_a_keyboard_stops_and_keeps_its_log(self):
        with tempfile.TemporaryDirectory() as d:
            if self.hao('doctor', '--offline', env={'HAO_LOGS': d}).returncode:
                self.skipTest('this Mac fails hao doctor, so create-installer stops first (DoctorFirstTest)')
            r = self.hao('create-installer', '--dry-run', env={'HAO_LOGS': d})
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn('Which Mac is the installer for?', r.stdout)
            self.assertIn('STOPPED', r.stderr)
            self.assertIn('./hao report', r.stderr)
            (log,) = Path(d).glob('create-installer-*.json')
            evs = [e for e in json.loads(log.read_text())['events'] if e['ev'] != 'doctor.check']
            self.assertEqual(evs[0]['ev'], 'question')
            self.assertEqual(evs[-1]['ev'], 'run.end')

    def test_report(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'report.txt'
            r = self.hao('report', '--output', str(out), env={'HAO_LOGS': d})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('report written to', r.stdout)
            text = out.read_text()
            self.assertIn('== This Mac', text)
            user = os.environ.get('USER', '')
            if len(user) >= 3:
                self.assertNotIn(user, text)
            self.assertNotIn(str(Path.home()), text)


class PrelinkedKit(unittest.TestCase):
    """By default the install links the kernel collections on the target; --prelinked (development) needs a kit that
    carries darwin27-kernel's prelinked collections."""
    def test_link_on_target_is_the_default(self):
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'share'; (kit / 'hooks/01-darwin27-kernel').mkdir(parents=True)
            (kit / 'kit.json').write_text('{}')
            c = CON.Console(out=io.StringIO(), err=io.StringIO(), level='quiet')
            self.assertEqual(kit.resolve(), INST.prepare_kit(argparse.Namespace(kit=str(kit)), c, None))
            ran = []
            with self.assertRaises(CON.Stopped):        # staging: golden-run is asked for a kit without them
                INST.prepare_kit(argparse.Namespace(work=d + '/w'), c, Path(d) / 'asm.json', run=lambda cmd: ran.append(cmd) or 0)
            self.assertIn('--link-on-target', ran[0])

    def test_staging_with_prelinked_does_not_ask_for_linking(self):
        with tempfile.TemporaryDirectory() as d:
            c = CON.Console(out=io.StringIO(), err=io.StringIO(), level='quiet')
            ran = []
            with self.assertRaises(CON.Stopped):
                INST.prepare_kit(argparse.Namespace(work=d + '/w', prelinked=True), c, Path(d) / 'asm.json',
                                 run=lambda cmd: ran.append(cmd) or 0)
            self.assertNotIn('--link-on-target', ran[0])

    def test_kit_without_prelinked_collections_is_refused_with_prelinked(self):
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'share'; (kit / 'hooks/01-darwin27-kernel').mkdir(parents=True)
            (kit / 'kit.json').write_text('{}')
            c = CON.Console(out=io.StringIO(), err=io.StringIO(), level='quiet')
            a = argparse.Namespace(kit=str(kit), prelinked=True)
            with self.assertRaises(CON.Stopped) as x:
                INST.prepare_kit(a, c, None)
            self.assertIn('no prelinked kernel collections', x.exception.what)
            p = kit / 'hooks/01-darwin27-kernel/prelinked'; p.mkdir()
            (p / 'prelinked.json').write_text(json.dumps(dict(key='k1', files={})))
            self.assertEqual(kit.resolve(), INST.prepare_kit(a, c, None))


if __name__ == '__main__':
    unittest.main()
