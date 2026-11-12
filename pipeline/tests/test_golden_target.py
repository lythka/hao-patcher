"""The image target issues exactly the commands it did before the Target refactor; fixtures only, no disk tools.

fixtures/golden-image-commands.json was recorded from golden-image.py as of 7df266df (before Target existed):
every command, its capture flag and its environment, and the build record, for four fixture runs. Regenerate it
only for an intended change: python3.14 -I -B pipeline/tests/test_golden_target.py --write
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
EXPECTED = HERE/'fixtures/golden-image-commands.json'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


golden = load('golden_image', HERE.parent/'stages/golden-image.py')
fixture = load('golden_image_fixture', HERE/'test_golden_image.py')
fixture.golden = golden   # the fake reads nothing from golden; keep one module instance anyway

# A fixed process environment, so the recorded environments do not depend on the host running the test.
ENVIRONMENT = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/var/root', 'LANG': 'C'}


class RecordingCommands(fixture.FakeCommands):
    """The existing fake, also keeping each call's capture flag."""
    def run(self, command, env=None, capture=False):
        try:
            return super().run(command, env=env, capture=capture)
        finally:
            self.entries[-1]['capture'] = capture


def normalise(value, root):
    if isinstance(value, dict):
        return {key: normalise(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [normalise(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(root, '<root>')
    return value


def scenario(name, assemble=None):
    """One fixture run of the existing test's configuration: normalised commands, build record and error type."""
    case = fixture.GoldenImageTests('test_full_order_preserves_golden_identity_and_declared_roles')
    case.setUp()
    try:
        config = case.config
        fake = RecordingCommands(config)
        if name == 'displaced-preboot':
            fake.displace_preboot = True
        elif name == 'hardware-failure':
            fake.fail = lambda a: config['hardware']['tool'] in a
        elif name == 'unmount-failure':
            data = str(Path(config['work'])/'mnt/data')
            fake.fail = lambda a: a == ['diskutil', 'unmount', data]
        error = None
        with patch.dict(os.environ, ENVIRONMENT, clear=True), contextlib.redirect_stdout(io.StringIO()):
            try:
                (assemble or golden.assemble)(config, fake)
            except Exception as caught:
                error = type(caught).__name__
        record = json.loads((Path(config['work'])/'build.json').read_text())
        for key in ('date', 'source_sha256', 'configuration_sha256'):
            record.pop(key)
        for row in record['steps']:
            row.pop('started'); row.pop('seconds', None)
        root = str(case.root)
        environments = []
        def environment(value):
            # Most commands share one environment: keep each distinct one once and refer to it by index.
            value = normalise(value, root)
            if value not in environments:
                environments.append(value)
            return environments.index(value)
        rows = [dict(command=normalise(row['command'], root), capture=row['capture'],
                     environment=environment(row['environment'])) for row in fake.entries]
        record = normalise(record, root)
        record['commands'] = [dict(row, environment=environment(row['environment'])) for row in record['commands']]
        return dict(commands=rows, environments=environments, record=record, error=error)
    finally:
        case.doCleanups()


SCENARIOS = ('full', 'displaced-preboot', 'hardware-failure', 'unmount-failure')


def observe():
    return {name: scenario(name) for name in SCENARIOS}


class ImageTargetCommandSequence(unittest.TestCase):
    maxDiff = None

    def test_image_target_issues_the_recorded_commands(self):
        expected = json.loads(EXPECTED.read_text())
        actual = observe()
        self.assertEqual(sorted(expected), sorted(actual))
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                self.assertEqual(expected[name]['error'], actual[name]['error'])
                self.assertEqual(len(expected[name]['commands']), len(actual[name]['commands']))
                for index, (want, got) in enumerate(zip(expected[name]['commands'], actual[name]['commands'])):
                    self.assertEqual(want, got, 'command %d of %s' % (index, name))
                self.assertEqual(expected[name]['environments'], actual[name]['environments'])
                self.assertEqual(expected[name]['record'], actual[name]['record'])

    def test_recording_covers_every_layer(self):
        expected = json.loads(EXPECTED.read_text())
        steps = [row['name'] for row in expected['full']['record']['steps']]
        self.assertEqual([n for n in steps if not n.startswith(('recipe ', 'post '))], [n for n, _, _ in golden.LAYERS])
        self.assertEqual(expected['full']['commands'][-1]['command'], ['hdiutil', 'detach', '/dev/disk70'])



import plistlib
import subprocess

layout = load('golden_layout', HERE.parent/'stages/golden-layout.py')

ELIGIBLE = dict(WholeDisk=True, Internal=True, VirtualOrPhysical='Physical', Removable=False, RemovableMedia=False,
                Ejectable=False, BusProtocol='SATA', TotalSize=500*10**9, MediaName='Fixture SSD')
READS = (['diskutil', 'info', '-plist'], ['diskutil', 'apfs', 'list', '-plist'], ['df', '-P'], ['mount'])


class DiskCommands(RecordingCommands):
    """The fixture fake plus the reads a whole-disk target makes: the disk, the running root, df and mount.
    disk70 holds an old install (container disk72 on disk70s2) until partitionDisk replaces it with disk71."""
    def __init__(self, config, info=ELIGIBLE, root='disk1s1s1', df=None, mounted=()):
        super().__init__(config)
        self.info = info; self.root = root; self.df = df or {}; self.still_mounted = list(mounted)
        self.partitioned = False

    def answer(self, a):
        if a[:3] == ['diskutil', 'info', '-plist'] and a[3] in ('disk70', '/'):
            if a[3] == '/':
                return plistlib.dumps(dict(DeviceIdentifier=self.root, APFSContainerReference='disk1'))
            if self.info is None:
                raise subprocess.CalledProcessError(1, a)
            return plistlib.dumps(self.info)
        if a == ['diskutil', 'apfs', 'list', '-plist'] and not self.partitioned:
            return plistlib.dumps({'Containers': [
                dict(ContainerReference='disk1', PhysicalStores=[{'DeviceIdentifier': 'disk0s2'}], Volumes=[]),
                dict(ContainerReference='disk72', PhysicalStores=[{'DeviceIdentifier': 'disk70s2'}], Volumes=[])]})
        if a[:2] == ['df', '-P']:
            return ('Filesystem 512-blocks Used Available Capacity Mounted on\n'+''.join(
                '%s 1 1 1 1%% %s\n' % (self.df.get(path, '/dev/disk1s5'), path) for path in a[2:])).encode()
        if a == ['mount']:
            return ''.join('%s on %s (apfs, local)\n' % row for row in
                           [('/dev/disk1s1s1', '/'), ('/dev/disk1s5', '/System/Volumes/Data')]+self.still_mounted).encode()
        if a[:2] == ['diskutil', 'unmountDisk']:
            return b''
        if a[:2] == ['diskutil', 'partitionDisk']:
            self.partitioned = True
        return None

    def run(self, command, env=None, capture=False):
        a = list(map(str, command))
        if a[0] in ('df', 'mount') or a[:2] == ['diskutil', 'unmountDisk'] or (
                a[:3] == ['diskutil', 'info', '-plist'] and a[3] in ('disk70', '/')) or (
                a == ['diskutil', 'apfs', 'list', '-plist'] and not self.partitioned):
            self.entries.append(dict(command=a, environment=dict(env or {}), capture=capture))
            return self.answer(a)
        self.answer(a)
        return super().run(command, env=env, capture=capture)


class DiskTargetTests(unittest.TestCase):
    """A whole-disk target, every command recorded by the fake; nothing runs on the host."""
    maxDiff = None

    def setUp(self):
        self.case = fixture.GoldenImageTests('test_full_order_preserves_golden_identity_and_declared_roles')
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.config = dict(self.case.config); del self.config['image']
        self.config.update(disk='/dev/disk70', confirm_disk='disk70')

    def assemble(self, fake):
        with contextlib.redirect_stdout(io.StringIO()):
            return golden.assemble(self.config, fake)

    def refused(self, why, **fake):
        fake = DiskCommands(self.config, **fake)
        with self.assertRaisesRegex(ValueError, why):
            self.assemble(fake)
        commands = [row['command'] for row in fake.entries]
        self.assertEqual([], [c for c in commands if not any(c[:len(r)] == r for r in READS)],
                         'a refused disk saw only reads')
        record = json.loads((Path(self.config['work'])/'build.json').read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['target'], dict(kind='disk', disk=self.config['disk'], minimum_bytes=32768, accepted=False))
        return commands

    def test_eligible_disk_runs_the_image_layers_on_the_disk(self):
        fake = DiskCommands(self.config)
        record = self.assemble(fake)
        commands = [row['command'] for row in fake.entries]
        self.assertEqual(record['status'], 'assembled')
        self.assertEqual(record['target'], dict(kind='disk', disk='/dev/disk70', minimum_bytes=32768, accepted=True))
        self.assertIsNone(record['attached_device'])
        start = commands.index(['diskutil', 'partitionDisk', '/dev/disk70', 'GPT', 'APFS', 'Golden', '100%'])
        root = str(self.case.root)
        self.assertEqual(commands[:start], [
            ['diskutil', 'info', '-plist', 'disk70'],
            ['diskutil', 'apfs', 'list', '-plist'],
            ['diskutil', 'info', '-plist', '/'],
            ['df', '-P', root+'/work', '/staged/python', '/staged/golden-payload', '/staged/kernel', '/staged/kdk',
             '/staged/fl', root+'/esp', '/staged/tahoe-donors/make']+[root+'/'+k for k in
                ('native', 'extensions', 'driverkit', 'boot', 'recovery')]+['/staged/hardware'],
            ['diskutil', 'apfs', 'list', '-plist'],
            ['diskutil', 'unmountDisk', 'disk72'], ['diskutil', 'unmountDisk', 'disk70'],
            ['mount']])
        self.assertFalse(any(c[0] == 'hdiutil' for c in commands))
        self.assertFalse(Path(root+'/output.img').exists())
        # From the partition on, the layers issue the image target's commands: only the binding and the
        # device differ, and there is no image to detach at the end.
        image = scenario('full')['commands']
        image = [row['command'] for row in image]
        self.assertEqual(image[0][:2], ['hdiutil', 'attach']); self.assertEqual(image[-1], ['hdiutil', 'detach', '/dev/disk70'])
        expected = [[('/dev/disk70' if part == '<root>/output.img' else part.replace('<root>', root)) for part in c]
                    for c in image[1:-1]]
        self.assertEqual(expected, commands[start:])
        self.assertIn(['/sbin/mount_msdos', '-o', 'nobrowse', '/dev/disk70s1', root+'/work/mnt/esp'], commands)

    def test_not_a_whole_disk_name(self):
        self.config['disk'] = '/dev/disk70s2'
        self.assertEqual(self.refused('not a whole disk name'), [])

    def test_raw_device_name_is_the_same_disk(self):
        self.config['disk'] = '/dev/rdisk70'
        self.assertEqual(self.assemble(DiskCommands(self.config))['status'], 'assembled')

    def test_diskutil_info_failure(self):
        self.refused('diskutil info failed', info=None)

    def test_partition_not_whole_disk(self):
        self.refused('not a whole disk', info=dict(ELIGIBLE, WholeDisk=False))

    def test_external_disk(self):
        self.refused('not internal', info=dict(ELIGIBLE, Internal=False))

    def test_disk_image_or_ram_disk(self):
        self.refused('virtual', info=dict(ELIGIBLE, VirtualOrPhysical='Virtual'))

    def test_removable_media(self):
        for key in ('Removable', 'RemovableMedia', 'Ejectable'):
            with self.subTest(key=key):
                self.setUp()
                self.refused('removable', info=dict(ELIGIBLE, **{key: True}))

    def test_usb_disk(self):
        self.refused('USB', info=dict(ELIGIBLE, BusProtocol='USB'))

    def test_disk_of_the_running_system(self):
        self.refused('holds the running system', root='disk70s1')

    def test_container_of_the_running_system_on_the_disk(self):
        self.refused('holds the running system', root='disk72s1s1')   # disk72's store is disk70s2

    def test_disk_too_small(self):
        self.refused('too small', info=dict(ELIGIBLE, TotalSize=4096))

    def test_size_falls_back_to_size_key(self):
        info = dict(ELIGIBLE); del info['TotalSize']; info['Size'] = 4096
        self.refused('too small', info=info)

    def test_work_folder_on_the_disk(self):
        self.refused('work folder or inputs', df={str(self.case.root)+'/work': '/dev/disk72s1'})

    def test_input_on_the_disk(self):
        self.refused('work folder or inputs', df={'/staged/kernel': '/dev/disk70s3'})

    def test_unconfirmed(self):
        self.config['confirm_disk'] = 'disk7'
        self.refused('not confirmed')
        del self.config['confirm_disk']; self.setUp(); del self.config['confirm_disk']
        self.refused('not confirmed')

    def test_still_mounted_after_unmount(self):
        fake = DiskCommands(self.config, mounted=[('/dev/disk72s1', '/Volumes/Old Mac')])
        with self.assertRaisesRegex(ValueError, '/dev/disk72s1 is still mounted at /Volumes/Old Mac'):
            self.assemble(fake)
        commands = [row['command'] for row in fake.entries]
        self.assertFalse(any(c[:2] == ['diskutil', 'partitionDisk'] for c in commands))

    def test_similar_disk_numbers_are_not_confused(self):
        # disk7 is not disk70: its slices and containers are not on the target.
        self.assertFalse(golden.DiskTarget.on('/dev/disk700s1', ['disk70']))
        self.assertTrue(golden.DiskTarget.on('/dev/disk70s1', ['disk70']))
        self.assertTrue(golden.DiskTarget.on('disk70', ['disk70']))

    def test_configuration_names_exactly_one_target(self):
        both = dict(self.config, image='/tmp/x.img')
        with self.assertRaisesRegex(ValueError, 'exactly one target'):
            golden.target_for(both)
        neither = dict(self.config); del neither['disk']
        with self.assertRaisesRegex(ValueError, 'exactly one target'):
            golden.target_for(neither)
        self.assertIsInstance(golden.target_for(self.case.config), golden.ImageTarget)
        self.assertIsInstance(golden.target_for(self.config), golden.DiskTarget)


class LayoutDiskBindingTests(unittest.TestCase):
    """golden-layout.py binds each System mutation to a container on the explicit disk."""
    def bind(self, container='disk71', info=ELIGIBLE, root='disk1s1s1', store='disk70s2', running_store='disk0s2'):
        def plist(*command):
            command = list(map(str, command))
            if command == ['diskutil', 'info', '-plist', 'disk70']:
                return info
            if command == ['diskutil', 'info', '-plist', '/']:
                return dict(DeviceIdentifier=root, APFSContainerReference='disk1')
            if command == ['diskutil', 'apfs', 'list', '-plist', 'disk1']:
                return dict(Containers=[dict(PhysicalStores=[dict(DeviceIdentifier=running_store)])])
            if command == ['diskutil', 'apfs', 'list', '-plist', container]:
                return dict(Containers=[dict(ContainerReference=container, PhysicalStores=[dict(DeviceIdentifier=store)])])
            raise AssertionError(command)
        with patch.object(layout, 'plist', side_effect=plist):
            return layout.bound_container('/dev/disk70', container)

    def test_container_on_the_disk_is_bound(self):
        self.assertEqual(self.bind()['ContainerReference'], 'disk71')

    def test_store_on_another_disk_refused(self):
        with self.assertRaisesRegex(ValueError, 'do not belong'):
            self.bind(store='disk700s2')

    def test_running_container_refused(self):
        with self.assertRaisesRegex(ValueError, 'maintenance container'):
            self.bind(container='disk1')

    def test_running_system_on_the_disk_refused(self):
        with self.assertRaisesRegex(ValueError, 'running system'):
            self.bind(root='disk70s1')
        with self.assertRaisesRegex(ValueError, 'running system'):
            self.bind(running_store='disk70s3')

    def test_virtual_or_partial_disk_refused(self):
        with self.assertRaisesRegex(ValueError, 'physical whole disk'):
            self.bind(info=dict(ELIGIBLE, VirtualOrPhysical='Virtual'))
        with self.assertRaisesRegex(ValueError, 'physical whole disk'):
            self.bind(info=dict(ELIGIBLE, WholeDisk=False))

    def test_regular_image_still_takes_the_image_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary)/'image'; image.write_bytes(b'fixture')
            with patch.object(layout, 'plist', return_value=dict(APFSContainerReference='disk9')), \
                    patch.object(layout, 'bound_disk', side_effect=AssertionError('image went to the disk binding')):
                with self.assertRaisesRegex(ValueError, 'maintenance container'):
                    layout.bound_container(image, 'disk9')


if __name__ == '__main__':
    if sys.argv[1:] == ['--write']:
        EXPECTED.write_text(json.dumps(observe(), indent=1, sort_keys=True)+'\n')
    else:
        unittest.main()
