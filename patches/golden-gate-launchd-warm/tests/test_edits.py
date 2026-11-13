"""The edits/ specs declare what src/warm.py writes: the engine makes the same bytes.
python3.14 -I -B -m unittest discover -s patches/golden-gate-launchd-warm/tests

Real inputs, skipped when unset:
  LAUNCHD_WARM_SYSTEM=PATH   a read-only 26B131 System volume (or a copy of its three files at their paths): the job
                             plists com.apple.kernelmanager_helper (2de9e533...) and com.apple.mbsystemadministration
                             (34d524fb...), and the pristine System/Library/xpc/launchd.plist (4b1277b3...); the test
                             first applies golden-gate-userspace's launchd-job-cache.json, which runs earlier in the set"""
import json
import os
from pathlib import Path
import plistlib
import re
import runpy
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
import engine   # noqa: E402
import spec as S   # noqa: E402

WARM = runpy.run_path(str(PATCH / 'src/warm.py'))
FILES = {f['path']: f for f in json.loads((PATCH / 'patch.json').read_text())['files']}
SPECS = {p: S.load(PATCH / f['edits']) for p, f in FILES.items()}
USERSPACE_CACHE_SPEC = S.load(TOP / 'patches/golden-gate-userspace/edits/launchd-job-cache.json')
CACHE = WARM['CACHE']
JOBS = {path: (label, keys) for label, path, keys in WARM['JOBS']}
KM = 'System/Library/LaunchDaemons/com.apple.kernelmanager_helper.plist'
MB = 'System/Library/LaunchDaemons/com.apple.mbsystemadministration.plist'

# Apple's jobs, key for key. kernelmanager_helper: 26B131 and Tahoe 25G332 have the same file (2de9e533...).
# mbsystemadministration: 26B131 (34d524fb...).
APPLE = {
    KM: {'_PanicOnCrash': {'PanicOnConsecutiveCrash': True}, 'EnablePressuredExit': True, 'EnableTransactions': True,
         'Label': 'com.apple.kernelmanager_helper', 'MachServices': {'com.apple.KernelManagementHelperServer': True},
         'POSIXSpawnType': 'Adaptive', 'ProgramArguments': ['/usr/libexec/kernelmanager_helper'],
         'StandardErrorPath': '/dev/console'},
    MB: {'EnablePressuredExit': False, 'EnableTransactions': False, 'Label': 'com.apple.mbsystemadministration',
         'MachServices': {'com.apple.mbsystemadministration': True, 'com.apple.mbsystemadministration.override': True},
         'POSIXSpawnType': 'Adaptive',
         'ProgramArguments': ['/System/Library/CoreServices/Setup Assistant.app/Contents/Resources/mbsystemadministration'],
         'UserName': 'root'},
}
WARMED = {
    KM: dict(APPLE[KM], RunAtLoad=True, POSIXSpawnType='Interactive'),
    MB: dict(APPLE[MB], KeepAlive={'PathState': {'/private/var/db/.AppleSetupDone': False}}, POSIXSpawnType='Interactive'),
}


def synthetic_cache():
    return plistlib.dumps({'LaunchDaemons': {
        '/System/Library/LaunchDaemons/com.apple.kernelmanagerd.plist': {'Label': 'com.apple.kernelmanagerd',
                                                                         'RunAtLoad': True},
        '/' + KM: dict(APPLE[KM]),
        '/' + MB: dict(APPLE[MB]),
        '/System/Library/LaunchAgents/com.apple.hybridsearchd.plist': {'Label': 'com.apple.hybridsearchd'}},
        'VersionNumber': 1}, fmt=plistlib.FMT_BINARY, sort_keys=False)


def warm(path, data):
    if path == CACHE:
        return WARM['warm_cache'](data)
    return WARM['warm_job'](data, *JOBS[path])


class Declaration(unittest.TestCase):
    def test_every_file_the_script_writes_is_declared(self):
        self.assertEqual(set(FILES), set(JOBS) | {CACHE})
        self.assertEqual(set(JOBS), {KM, MB})
        for path, spec in SPECS.items():
            self.assertEqual(S.validate(spec), [])
            self.assertEqual(spec['path'], path)
            self.assertEqual(FILES[path]['install'], {'mode': '0644', 'owner': 'root:wheel', 'flags': ['restricted']})
            self.assertIsNone(spec['sign'])

    def test_spec_values_are_the_scripts(self):
        for path, (_, keys) in JOBS.items():
            self.assertEqual([(e['args']['key'], e['args']['value']) for e in SPECS[path]['edits']],
                             [([k], v) for k, v in keys])
        self.assertEqual([(e['args']['key'], e['args']['value']) for e in SPECS[CACHE]['edits']],
                         [(['LaunchDaemons', '/' + path, k], v) for _, path, keys in WARM['JOBS'] for k, v in keys])

    def test_setup_service_starts_only_until_setup_is_done(self):
        _, keys = JOBS[MB]
        self.assertEqual(dict(keys)['KeepAlive'], {'PathState': {'/private/var/db/.AppleSetupDone': False}})
        self.assertNotIn('RunAtLoad', dict(keys))

    def same(self, spec, data):
        out, record = engine.apply(spec, data, repo=str(TOP))
        self.assertEqual(out, warm(spec['path'], data))
        return out, record

    def test_same_bytes_synthetic_jobs_xml(self):
        for path in JOBS:
            out, _ = self.same(SPECS[path], plistlib.dumps(APPLE[path]))
            self.assertEqual(plistlib.loads(out), WARMED[path])

    def test_same_bytes_synthetic_cache_binary_only_the_jobs_change(self):
        data = synthetic_cache()
        out, _ = self.same(SPECS[CACHE], data)
        self.assertTrue(out.startswith(b'bplist00'))
        before, after = plistlib.loads(data), plistlib.loads(out)
        for path in JOBS:
            self.assertEqual(after['LaunchDaemons'].pop('/' + path), WARMED[path])
            before['LaunchDaemons'].pop('/' + path)
        self.assertEqual(before, after)

    def test_another_label_stops(self):
        for path in JOBS:
            with self.assertRaises(SystemExit):
                warm(path, plistlib.dumps(dict(APPLE[path], Label='com.apple.other')))
        cache = plistlib.loads(synthetic_cache())
        cache['LaunchDaemons']['/' + MB]['Label'] = 'com.apple.other'
        with self.assertRaises(SystemExit):
            WARM['warm_cache'](plistlib.dumps(cache, fmt=plistlib.FMT_BINARY))

    def test_script_writes_every_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            system, out = Path(temporary) / 'system', Path(temporary) / 'out'
            for path in JOBS:
                (system / path).parent.mkdir(parents=True, exist_ok=True)
                (system / path).write_bytes(plistlib.dumps(APPLE[path]))
            (system / CACHE).parent.mkdir(parents=True)
            (system / CACHE).write_bytes(synthetic_cache())
            out.mkdir()
            subprocess.run([sys.executable, '-I', str(PATCH / 'src/warm.py'), str(system), str(out)], check=True)
            written = sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())
            self.assertEqual(written, sorted(FILES))
            for path in FILES:
                self.assertEqual((out / path).read_bytes(), warm(path, (system / path).read_bytes()))

    def test_hook_installs_every_file_like_apple_and_after_the_script(self):
        hook = (PATCH / 'src/kc-hook.sh').read_text()
        self.assertIn('"${OCLP_PYTHON:?}" -I "$H/warm.py" "$S" "$W"', hook)
        loop = re.search(r'for f in (.*?); do', hook, re.S).group(1)
        self.assertEqual(sorted(loop.replace('\\', ' ').split()), sorted(FILES))
        self.assertIn('install -o root -g wheel -m 644 "$W/$f" "$F"', hook)
        self.assertIn('chflags restricted "$F"', hook)
        self.assertNotIn('codesign', hook)

    @unittest.skipUnless(os.environ.get('LAUNCHD_WARM_SYSTEM'), 'set LAUNCHD_WARM_SYSTEM')
    def test_same_bytes_real_files(self):
        system = Path(os.environ['LAUNCHD_WARM_SYSTEM'])
        for path in JOBS:
            _, record = self.same(SPECS[path], (system / path).read_bytes())
            self.assertEqual(record['expected'], 'match', path)
        after_userspace, record = engine.apply(USERSPACE_CACHE_SPEC, (system / CACHE).read_bytes(), repo=str(TOP))
        self.assertEqual(record['expected'], 'match')
        _, record = self.same(SPECS[CACHE], after_userspace)
        self.assertEqual(record['expected'], 'match')


if __name__ == '__main__':
    unittest.main()
