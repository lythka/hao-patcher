"""Host tests of the Recovery spikes: fixtures and stubs only. Nothing here attaches, formats or mounts a disk, and
nothing reaches the laptop (run.sh is driven through a stub LAPTOP_SSH)."""
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest

TOOL = Path(__file__).resolve().parents[1]
TOP = TOOL.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


spikes = load('recovery_spikes', TOOL/'lib/spikes.py')
sys.path.insert(0, str(TOOL/'remote'))
spikelib = load('spikelib', TOOL/'remote/spikelib.py')
kmutil_spike = load('kmutil_spike', TOOL/'remote/kmutil_spike.py')
group_spike = load('group_spike', TOOL/'remote/group_spike.py')
cryptex_spike = load('cryptex_spike', TOOL/'remote/cryptex_spike.py')

PYTHON_PASS = '''GATE OK
CHECK upload PASS 2 files match the host's sha256
TIME unpack 3
CHECK python-starts PASS 3.12.14 x86_64 darwin
CHECK imports PASS 41 modules
CHECK tools-present PASS codesign ditto
INFO tools-other kmutil=/usr/bin/kmutil
CHECK ctypes-libc PASS getxattr setxattr listxattr fsctl
CHECK ctypes-corefoundation PASS CFArray of 2 CFNumbers, count 2
CHECK apfs-framework PASS APFSContainerVolumeGroupAdd resolved
CHECK diskutil-plist PASS root disk1s1 macOS Base System
RESULT sha256_mb_s 412
CHECK suite:edit-engine PASS Ran 48 tests in 1.2s; OK
CHECK suite:kc-binder PASS Ran 27 tests in 1.0s; OK (skipped=4)
CHECK suite:hao PASS Ran 41 tests in 0.8s; OK
CHECK suite:darwin27-kernel PASS Ran 7 tests in 0.0s; OK (skipped=1)
END python rc=0
'''

KMUTIL_FULL = '''CHECK upload PASS 4 files
CHECK python-starts PASS 3.12.14 x86_64 darwin
CHECK internal-system-found PASS System volumes on internal disks: ['disk2s1']
CHECK internal-readonly PASS disk2s1 at /Volumes/HAOSPIKE-kmutil/mnt/internal-system, writable False
CHECK internal-bootkc PASS 203 members
CHECK memory PASS scope full (asked auto)
CHECK tree-copied PASS 4 paths, 1380 MB, tar rc 0/0
CHECK kmutil-boot:tahoe PASS rc=0, 203 members
CHECK kmutil-sys:tahoe PASS rc=0, 411 members
CHECK members-equal-internal PASS 203 members
CHECK kc-bind PASS kc-binder bind rc=0
CHECK kmutil-boot:recovery PASS rc=0
CHECK kmutil-sys:recovery FAIL rc=71
END kmutil rc=1
'''


class Lock(unittest.TestCase):
    def test_rows_parse_and_live_in_store(self):
        rows = spikes.load_lock_module().rows(str(spikes.TOOL/'inputs.lock'))
        self.assertTrue(rows)
        for name, (sha, path) in rows.items():
            self.assertRegex(sha, r'^[0-9a-f]{64}$', name)
            self.assertTrue(os.path.relpath(path, TOP).startswith('store/'), name)

    def test_mirrors_equal_the_pipeline_rows(self):
        lock = spikes.load_lock_module()
        rows = lock.rows(str(spikes.TOOL/'inputs.lock'))
        for name, (folder, row) in spikes.MIRRORS.items():
            self.assertEqual(rows[name], lock.rows(str(TOP/folder))[row], name)

    def test_python_is_the_archive_run_9a_uses(self):
        text = (TOP/'tools/hao/laptop/run-9a.sh').read_text()
        want = re.search(r'^py_sha=([0-9a-f]{64})$', text, re.M).group(1)
        rows = spikes.load_lock_module().rows(str(spikes.TOOL/'inputs.lock'))
        self.assertEqual(rows['python_x86_64'][0], want)

    def test_every_spike_names_known_rows_and_a_script(self):
        rows = spikes.load_lock_module().rows(str(spikes.TOOL/'inputs.lock'))
        self.assertEqual(spikes.ORDER, list(spikes.SPIKES))
        for spike, config in spikes.SPIKES.items():
            self.assertLessEqual(set(config['inputs']), set(rows), spike)
            self.assertEqual(len(set(config['inputs'].values())), len(config['inputs']), spike)
            script = TOOL/'remote'/('survey.sh' if spike == 'survey' else '%s_spike.py' % spike)
            self.assertTrue(script.is_file(), script)

    def test_margin_matches_common_sh(self):
        self.assertIn('$((mb + %d))' % spikes.MARGIN_MB, (TOOL/'remote/common.sh').read_text())

    def test_ramdisk_sizes(self):
        self.assertEqual(spikes.ramdisk_mb('survey'), 0)
        self.assertEqual(spikes.ramdisk_mb('cryptex', 256), 1152)
        self.assertEqual(spikes.ramdisk_mb('cryptex', 0), 896)


class Stage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        lines = []
        for name in ('python_x86_64', 'fl', 'fl_noent'):
            p = self.tmp/('src-'+name)
            p.write_bytes(name.encode()*100)
            lines.append('%s %s %s' % (name, spikes.sha256(p), p))
        self.lock = self.tmp/'test.lock'
        self.lock.write_text('\n'.join(lines)+'\n')
        self.saved = spikes.LOCK
        spikes.LOCK = self.lock
        self.addCleanup(setattr, spikes, 'LOCK', self.saved)

    def test_stage_copies_hashes_and_lists_files(self):
        files = spikes.stage('group', self.tmp/'out')
        self.assertEqual(files, ['inputs/python.tar.gz', 'inputs/fl', 'inputs/fl-noent', 'inputs/code.tar',
                                 'inputs/manifest.tsv'])
        manifest = [l.split('\t') for l in (self.tmp/'out/inputs/manifest.tsv').read_text().splitlines()]
        for name, sha, size in manifest:
            self.assertEqual(spikes.sha256(self.tmp/'out/inputs'/name), sha)
            self.assertEqual((self.tmp/'out/inputs'/name).stat().st_size, int(size))
        with tarfile.open(self.tmp/'out/inputs/code.tar') as tar:
            names = set(tar.getnames())
            self.assertTrue(all(m.uid == 0 and m.gid == 0 for m in tar.getmembers()))
        for want in ('tools/recovery-spikes/remote/group_spike.py', 'tools/recovery-spikes/remote/spikelib.py',
                     'pipeline/stages/golden-layout.py', 'patches/darwin27-kernel/src/members.py',
                     'patches/golden-gate-userspace/src/install.py', 'kcstage/stage.json',
                     'kcstage/tools/kc-binder/kc-binder', 'CODE.json'):
            self.assertIn(want, names)
        self.assertFalse([n for n in names if '__pycache__' in n or n.endswith('.pyc')])

    def test_a_changed_input_is_refused(self):
        (self.tmp/'src-fl').write_bytes(b'other')
        with self.assertRaises(SystemExit) as caught:
            spikes.stage('group', self.tmp/'out')
        self.assertIn('fl', str(caught.exception))


class Verdicts(unittest.TestCase):
    def judge(self, spike, text, host=None):
        return spikes.judge(spike, spikes.parse(text), host)

    def test_python_pass(self):
        verdict, questions, failures = self.judge('python', PYTHON_PASS)
        self.assertEqual((verdict, failures), ('PASS', []))

    def test_python_failed_suite_or_missing_check_fails(self):
        self.assertEqual(self.judge('python', PYTHON_PASS.replace(
            'CHECK suite:hao PASS', 'CHECK suite:hao FAIL'))[0], 'FAIL')
        verdict, _, failures = self.judge('python', PYTHON_PASS.replace(
            'CHECK apfs-framework PASS APFSContainerVolumeGroupAdd resolved\n', ''))
        self.assertEqual(verdict, 'FAIL')
        self.assertIn('required check apfs-framework is MISSING', failures)

    def test_no_end_line_fails(self):
        verdict, questions, failures = self.judge('python', PYTHON_PASS.replace('END python rc=0\n', ''))
        self.assertEqual(verdict, 'FAIL')
        self.assertIn('no END line', failures[-1])

    def test_spike_error_fails(self):
        self.assertEqual(self.judge('python', PYTHON_PASS.replace(
            'END python', 'CHECK spike-error FAIL OSError: x\nEND python'))[0], 'FAIL')

    def test_cryptex(self):
        base = 'CHECK upload PASS\nCHECK python-starts PASS\n'
        both = base+'CHECK seal:recovery PASS\nCHECK readback:recovery PASS\nCHECK seal:carried PASS\n' \
                    'CHECK readback:carried PASS\nEND cryptex rc=0\n'
        self.assertEqual(self.judge('cryptex', both)[0], 'PASS')
        carried = both.replace('seal:recovery PASS', 'seal:recovery FAIL apfs_prepare_cryptex absent')
        verdict, questions, _ = self.judge('cryptex', carried)
        self.assertEqual((verdict, questions['seal-recovery'], questions['seal-carried']), ('PARTIAL', 'FAIL', 'PASS'))
        self.assertEqual(self.judge('cryptex', carried.replace('readback:carried PASS', 'readback:carried FAIL'))[0],
                         'FAIL')

    def test_group(self):
        good = ('CHECK upload PASS\nCHECK python-starts PASS\nCHECK seed-altlink PASS\nCHECK group-add PASS u\n'
                'CHECK group-add-readback PASS\nCHECK fl-entitled PASS rc=0\nCHECK fl-readback PASS\n'
                'CHECK other-roles PASS\nCHECK group-with PASS\nEND group rc=0\n')
        self.assertEqual(self.judge('group', good)[0], 'PASS')
        fallback = good.replace('group-add PASS', 'group-add FAIL').replace('group-add-readback PASS',
                                                                          'group-add-readback SKIP')
        verdict, questions, _ = self.judge('group', fallback)
        self.assertEqual((verdict, questions['group-add'], questions['group-with']), ('PARTIAL', 'FAIL', 'PASS'))
        no_fl = good.replace('fl-entitled PASS', 'fl-entitled FAIL killed by signal 9')
        verdict, questions, _ = self.judge('group', no_fl)
        self.assertEqual((verdict, questions['fl']), ('PARTIAL', 'FAIL'))
        neither = fallback.replace('group-with PASS', 'group-with FAIL')
        self.assertEqual(self.judge('group', neither)[0], 'FAIL')

    def test_kmutil_full_boot_and_unsafe(self):
        verdict, questions, _ = self.judge('kmutil', KMUTIL_FULL)
        self.assertEqual((verdict, questions['link-boot'], questions['link-sys']), ('PASS', 'PASS', 'PASS'))
        boot = KMUTIL_FULL.replace('kmutil-sys:tahoe PASS', 'kmutil-sys:tahoe SKIP boot scope')
        verdict, questions, _ = self.judge('kmutil', boot)
        self.assertEqual((verdict, questions['link-sys']), ('PARTIAL', 'NOT RUN (boot scope)'))
        unsafe = KMUTIL_FULL.replace('internal-readonly PASS', 'internal-readonly FAIL')
        verdict, _, failures = self.judge('kmutil', unsafe)
        self.assertEqual(verdict, 'FAIL')
        self.assertIn('the internal System volume was not mounted read-only', failures)
        verdict, _, _ = self.judge('kmutil', KMUTIL_FULL, {'host-bind-equal': ('FAIL', 'differ')})
        self.assertEqual(verdict, 'FAIL')
        self.assertEqual(self.judge('kmutil', KMUTIL_FULL.replace('kc-bind PASS', 'kc-bind FAIL'))[0], 'FAIL')

    def test_survey_facts_and_budget(self):
        log = textwrap.dedent('''\
            SURVEY build 25G241
            SURVEY arch x86_64
            SURVEY mem_available_mb 1800
            SURVEY df /dev/disk3 2000 KB free of 4000 KB at /private/var/tmp
            SURVEY df /dev/disk4 9000 KB free of 9000 KB at /Volumes
            SURVEY apfs_volume disk2s1 role System store disk0s2 name macOS
            END survey rc=0
            ''')
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'log').write_text(log)
            verdict, text = spikes.verdict('survey', Path(tmp, 'log'), out=Path(tmp, 'v.json'))
            record = json.loads(Path(tmp, 'v.json').read_text())
        self.assertEqual(verdict, 'PASS')
        self.assertEqual(len(record['survey']['df']), 2)
        self.assertEqual(record['survey']['build'], '25G241')
        self.assertTrue(record['ram_budget']['python']['fits'])
        self.assertFalse(record['ram_budget']['group']['fits'])
        self.assertIn('DOES NOT FIT', text)


class RemoteHelpers(unittest.TestCase):
    def test_vm_stat(self):
        text = ('Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free:      262144.\n'
                'Pages active:     99999.\nPages inactive:   131072.\nPages speculative:  0.\n'
                'Pages purgeable:   131072.\n')
        self.assertEqual(spikelib.parse_vm_stat(text), 2048)

    def test_attach_results(self):
        attached = {'system-entities': [
            {'dev-entry': '/dev/disk7', 'content-hint': 'GUID_partition_scheme'},
            {'dev-entry': '/dev/disk7s1', 'content-hint': '41504653-0000-11AA-AA11-00306543ECAC'}]}
        self.assertEqual(spikelib.whole_disk(attached), '/dev/disk7')
        self.assertEqual(spikelib.apfs_volume_entity(attached), '/dev/disk7s1')
        listing = {'Containers': [{'ContainerReference': 'disk8', 'PhysicalStores': [{'DeviceIdentifier': 'disk7s1'}]},
                                  {'ContainerReference': 'disk1', 'PhysicalStores': [{'DeviceIdentifier': 'disk0s2'}]},
                                  {'ContainerReference': 'disk9', 'PhysicalStores': [{'DeviceIdentifier': 'disk70s1'}]}]}
        self.assertEqual(spikelib.container_on(listing, '/dev/disk7')['ContainerReference'], 'disk8')
        with self.assertRaises(spikelib.CommandFailed):
            spikelib.container_on(listing, '/dev/disk5')

    def test_internal_system_skips_virtual_and_external(self):
        listing = {'Containers': [
            {'PhysicalStores': [{'DeviceIdentifier': 'disk1s1'}],   # the BaseSystem ramdisk
             'Volumes': [{'DeviceIdentifier': 'disk3s1', 'Roles': ['System']}]},
            {'PhysicalStores': [{'DeviceIdentifier': 'disk0s2'}],
             'Volumes': [{'DeviceIdentifier': 'disk2s1', 'Roles': ['Data']},
                         {'DeviceIdentifier': 'disk2s2', 'Roles': ['System']},
                         {'DeviceIdentifier': 'disk2s3', 'Roles': ['Preboot']}]},
            {'PhysicalStores': [{'DeviceIdentifier': 'disk5s2'}],   # a USB stick
             'Volumes': [{'DeviceIdentifier': 'disk6s1', 'Roles': ['System']}]}]}
        info = {'disk1s1': {'Internal': True, 'VirtualOrPhysical': 'Virtual'},
                'disk0s2': {'Internal': True, 'VirtualOrPhysical': 'Physical'},
                'disk5s2': {'Internal': False, 'VirtualOrPhysical': 'Physical'}}
        self.assertEqual(kmutil_spike.internal_systems(listing, info), ['disk2s2'])

    def test_select_bundles_by_member_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def kext(path, ident):
                (root/path/'Contents').mkdir(parents=True)
                with open(root/path/'Contents/Info.plist', 'wb') as f:
                    plistlib.dump({'CFBundleIdentifier': ident}, f, fmt=plistlib.FMT_BINARY)
            kext('System/Library/Extensions/A.kext', 'com.x.a')
            kext('System/Library/Extensions/B.kext', 'com.x.b')
            kext('System/Library/Extensions/B.kext/Contents/PlugIns/C.kext', 'com.x.c')
            kext('Library/Extensions/D.kext', 'com.x.d')
            (root/'System/Library/Extensions/notes.txt').write_text('x')
            picked, found = kmutil_spike.select_bundles(root, {'com.x.a', 'com.x.c', 'com.x.d', 'com.x.gone'})
        self.assertEqual(picked, ['System/Library/Extensions/A.kext', 'System/Library/Extensions/B.kext',
                                  'Library/Extensions/D.kext'])
        self.assertEqual(found, {'com.x.a', 'com.x.c', 'com.x.d'})

    def test_scope(self):
        choose = kmutil_spike.choose_scope
        self.assertEqual(choose('auto', 2000, 400, 4000), 'full')
        self.assertEqual(choose('auto', 2000, 400, 2500), 'boot')
        self.assertIsNone(choose('auto', 2000, 400, 1000))
        self.assertIsNone(choose('full', 2000, 400, 2500))
        self.assertEqual(choose('boot', 2000, 400, 9000), 'boot')
        with self.assertRaises(ValueError):
            choose('sys', 1, 1, 9000)
        self.assertEqual(kmutil_spike.need_mb(1000 << 20, True), 1150+640)

    def test_altlink_value_is_the_builds(self):
        # what golden-pristine's copy-altlink printed in the 26B131 builds
        self.assertEqual(group_spike.ALTLINK_HEX, '2f53797374656d2f566f6c756d65732f507265626f6f742f43727970746578'
                                                  '65732f496e636f6d696e672f4f53')

    def test_fl_outcome(self):
        self.assertIn('killed by signal 9', group_spike.fl_outcome(-9, ''))
        self.assertEqual(group_spike.fl_outcome(0, 'set x'), 'rc=0 set x')

    def test_cryptex_listing_skips_kept_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'usr/bin').mkdir(parents=True)
            (root/'usr/bin/x').write_bytes(b'12')
            (root/'.fseventsd').mkdir()
            os.symlink('usr', root/'link')
            got = cryptex_spike.listing(root, {'.fseventsd'})
        self.assertEqual(set(got), {'usr', 'usr/bin', 'usr/bin/x', 'link'})
        self.assertEqual(got['usr/bin/x'][:2], ('f', 2))
        self.assertEqual(got['link'][0], 'l')


class Safety(unittest.TestCase):
    """The remote scripts write only to what they attached; the internal disk is only read."""
    SOURCES = sorted((TOOL/'remote').glob('*'))

    def test_no_literal_disks(self):
        for path in self.SOURCES:
            if path.suffix in ('.py', '.sh'):
                text = path.read_text()
                self.assertIsNone(re.search(r'/dev/disk[0-9]|\bdisk0\b', text), path)
                self.assertNotIn('eraseDisk', text, path)

    def test_formatting_targets_are_attached_devices(self):
        for path in self.SOURCES:
            if path.suffix != '.py':
                continue
            for m in re.finditer(r"'partitionDisk', (\w+)", path.read_text()):
                self.assertEqual(m.group(1), 'whole', path)   # whole = s.attach_raw(an image file on a RAM disk)
            # images are formatted only by install.py's own new_image (on a file it creates) or partitionDisk above
            self.assertIsNone(re.search(r"run\([^)]*(newfs|apfs_prepare)", path.read_text()), path)
        # erasevolume only on the RAM disk spikelib.ramdisk has just attached
        found = [(p.name, m.group(1)) for p in self.SOURCES if p.suffix == '.py'
                 for m in re.finditer(r"'erasevolume', 'HFS\+', [^,]+, (\w+)\)", p.read_text())]
        self.assertEqual(found, [('spikelib.py', 'device')])
        self.assertIn("'ram://%d'", (TOOL/'remote/spikelib.py').read_text())

    def test_internal_volume_is_mounted_read_only(self):
        text = (TOOL/'remote/kmutil_spike.py').read_text()
        mounts = [l for l in text.splitlines() if "'diskutil', 'mount'" in l and 'device' in l]
        self.assertEqual(len(mounts), 1)
        self.assertIn("'readOnly'", mounts[0])

    def test_shell_syntax(self):
        for path in self.SOURCES:
            if path.suffix == '.sh':
                subprocess.run(['sh', '-n', str(path)], check=True)
        subprocess.run(['bash', '-n', str(TOOL/'run.sh')], check=True)


STUB = r'''#!/bin/bash
# stub laptop-ssh: records each call, answers like the Recovery would
log=$STUB_DIR/calls.txt
args=("$@"); put=0
while [ $# -gt 0 ]; do case $1 in -r) shift ;; -t) shift 2 ;; --put) put=1; src=$2; dest=$3; shift 3 ;; --) shift; break ;; *) break ;; esac; done
if [ $put = 1 ]; then cat "$src" > /dev/null; echo "put $dest" >> "$log"; exit 0; fi
cmd="$*"; echo "run $cmd" >> "$log"
case $cmd in
*'command -v perl'*) echo yes ;;
*'common.sh gate'*) echo 'GATE OK' ;;
*'common.sh setup'*) echo 'SETUP OK /Volumes/HAOSPIKE-python /dev/disk9' ;;
*'common.sh clean'*) echo 'CLEAN OK' ;;
*'spike.sh'*) cat "$STUB_DIR/spike.log" ;;
*'tar -cf - results'*) tar -C "$STUB_DIR/remote" -cf - results ;;
*) ;;
esac
'''


class Driver(unittest.TestCase):
    def run_driver(self, spike_log):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp/'remote/results').mkdir(parents=True)
        (tmp/'remote/results/commands.log').write_text('x\n')
        (tmp/'spike.log').write_text(spike_log)
        stub = tmp/'laptop-ssh'
        stub.write_text(STUB)
        stub.chmod(0o755)
        p = tmp/'py.tgz'
        p.write_bytes(b'python')
        (tmp/'test.lock').write_text('python_x86_64 %s %s\n' % (spikes.sha256(p), p))
        env = dict(os.environ, LAPTOP_SSH=str(stub), STUB_DIR=str(tmp), RESULTS=str(tmp/'results'),
                   HOST_PYTHON=sys.executable, LOCK=str(tmp/'nolock'), RECOVERY_SPIKES_LOCK=str(tmp/'test.lock'),
                   TMPDIR=str(tmp))
        r = subprocess.run(['bash', str(TOOL/'run.sh'), 'python'], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=300)
        calls = (tmp/'calls.txt').read_text().splitlines()
        return r, calls, tmp

    def test_order_and_verdict(self):
        r, calls, tmp = self.run_driver(PYTHON_PASS)
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertIn('SPIKE python: PASS', r.stdout)
        kinds = []
        for c in calls:
            for key in ('common.sh gate', 'common.sh setup', 'put /Volumes/HAOSPIKE-python/inputs/',
                        'spike.sh /Volumes/', 'tar -cf - results', 'common.sh clean'):
                if key in c and (not kinds or kinds[-1] != key):
                    kinds.append(key)
        self.assertEqual(kinds, ['common.sh gate', 'common.sh setup', 'put /Volumes/HAOSPIKE-python/inputs/',
                                 'spike.sh /Volumes/', 'tar -cf - results', 'common.sh clean'])
        puts = [c for c in calls if c.startswith('put /Volumes/')]
        self.assertEqual(puts[-1], 'put /Volumes/HAOSPIKE-python/inputs/manifest.tsv')
        out = next((tmp/'results').glob('python-*'))
        self.assertEqual(json.loads((out/'verdict.json').read_text())['verdict'], 'PASS')
        self.assertTrue((out/'results/commands.log').is_file())

    def test_a_dead_spike_fails_and_still_cleans(self):
        r, calls, _ = self.run_driver(PYTHON_PASS.split('CHECK imports')[0])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SPIKE python: FAIL', r.stdout)
        self.assertTrue(any('common.sh clean python' in c for c in calls))


class PythonSpikeOnHost(unittest.TestCase):
    """python_spike.py end to end on the host's Python with a stub diskutil: every required check is emitted."""
    def test_emits_every_required_check(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        spikes.code_tar(tmp/'code.tar')
        (tmp/'code').mkdir()
        with tarfile.open(tmp/'code.tar') as tar:
            tar.extractall(tmp/'code', filter='fully_trusted') if sys.version_info >= (3, 12) else tar.extractall(tmp/'code')
        bin_dir = tmp/'bin'
        bin_dir.mkdir()
        info = plistlib.dumps({'DeviceIdentifier': 'disk1s1', 'VolumeName': 'macOS Base System'})
        stub = bin_dir/'diskutil'
        stub.write_text('#!/bin/sh\ncat <<"EOF"\n%s\nEOF\n' % info.decode())
        stub.chmod(0o755)
        for tool in ('bless',):   # absent on some hosts' PATH; the Recovery has it
            if not shutil.which(tool):
                (bin_dir/tool).write_text('#!/bin/sh\n')
                (bin_dir/tool).chmod(0o755)
        env = dict(os.environ, PATH='%s:%s' % (bin_dir, os.environ.get('PATH', '/usr/bin:/bin')))
        r = subprocess.run([sys.executable, '-I', '-B', str(tmp/'code/tools/recovery-spikes/remote/python_spike.py'),
                            str(tmp)], env=env, capture_output=True, text=True, timeout=900)
        parsed = spikes.parse('CHECK upload PASS\nCHECK python-starts PASS\n'+r.stdout)
        self.assertEqual(parsed['end'], 0, r.stdout+r.stderr)
        for name in spikes.SPIKES['python']['required']:
            self.assertEqual(spikes.status(parsed, name), 'PASS', '%s\n%s' % (name, r.stdout))


if __name__ == '__main__':
    unittest.main()
