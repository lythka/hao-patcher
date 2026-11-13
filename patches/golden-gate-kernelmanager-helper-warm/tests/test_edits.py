"""edits/job.json and edits/launchd-job-cache.json declare what src/warm.py writes: the engine makes the same bytes.
python3.14 -I -B -m unittest discover -s patches/golden-gate-kernelmanager-helper-warm/tests

Real inputs, skipped when unset:
  KMHELPER_WARM_JOB=PATH     26B131's System/Library/LaunchDaemons/com.apple.kernelmanager_helper.plist (2de9e533...)
  KMHELPER_WARM_CACHE=PATH   26B131's pristine System/Library/xpc/launchd.plist (4b1277b3...); the test first applies
                             golden-gate-userspace's launchd-job-cache.json, which runs earlier in the set"""
import json
import os
from pathlib import Path
import plistlib
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
JOB_SPEC = S.load(PATCH / 'edits/job.json')
CACHE_SPEC = S.load(PATCH / 'edits/launchd-job-cache.json')
USERSPACE_CACHE_SPEC = S.load(TOP / 'patches/golden-gate-userspace/edits/launchd-job-cache.json')
FILES = {f['path']: f for f in json.loads((PATCH / 'patch.json').read_text())['files']}
JOB = WARM['JOB']

# Apple's job, key for key (26B131 and Tahoe 25G332 have the same file, 2de9e533...).
APPLE_JOB = {'_PanicOnCrash': {'PanicOnConsecutiveCrash': True}, 'EnablePressuredExit': True,
             'EnableTransactions': True, 'Label': 'com.apple.kernelmanager_helper',
             'MachServices': {'com.apple.KernelManagementHelperServer': True}, 'POSIXSpawnType': 'Adaptive',
             'ProgramArguments': ['/usr/libexec/kernelmanager_helper'], 'StandardErrorPath': '/dev/console'}


def synthetic_cache():
    return plistlib.dumps({'LaunchDaemons': {
        '/System/Library/LaunchDaemons/com.apple.kernelmanagerd.plist': {'Label': 'com.apple.kernelmanagerd',
                                                                         'RunAtLoad': True},
        '/' + JOB: dict(APPLE_JOB),
        '/System/Library/LaunchAgents/com.apple.hybridsearchd.plist': {'Label': 'com.apple.hybridsearchd'}},
        'VersionNumber': 1}, fmt=plistlib.FMT_BINARY, sort_keys=False)


class Declaration(unittest.TestCase):
    def test_specs_follow_the_format_and_are_listed(self):
        for spec, name in ((JOB_SPEC, 'edits/job.json'), (CACHE_SPEC, 'edits/launchd-job-cache.json')):
            self.assertEqual(S.validate(spec), [])
            self.assertEqual(FILES[spec['path']]['edits'], name)
            self.assertEqual(FILES[spec['path']]['install'], {'mode': '0644', 'owner': 'root:wheel',
                                                              'flags': ['restricted']})
            self.assertIsNone(spec['sign'])
        self.assertEqual(JOB_SPEC['path'], JOB)
        self.assertEqual(CACHE_SPEC['path'], WARM['CACHE'])

    def test_spec_values_are_the_scripts(self):
        for spec, prefix in ((JOB_SPEC, []), (CACHE_SPEC, ['LaunchDaemons', '/' + JOB])):
            self.assertEqual([(e['args']['key'], e['args']['value']) for e in spec['edits']],
                             [(prefix + [k], v) for k, v in WARM['KEYS']])

    def same(self, spec, data):
        out, record = engine.apply(spec, data, repo=str(TOP))
        self.assertEqual(out, WARM['warm'](data))
        return out, record

    def test_same_bytes_synthetic_job_xml(self):
        out, _ = self.same(JOB_SPEC, plistlib.dumps(APPLE_JOB))
        job = plistlib.loads(out)
        self.assertEqual(job, dict(APPLE_JOB, RunAtLoad=True, POSIXSpawnType='Interactive'))

    def test_same_bytes_synthetic_cache_binary_only_the_helper_changes(self):
        data = synthetic_cache()
        out, _ = self.same(CACHE_SPEC, data)
        self.assertTrue(out.startswith(b'bplist00'))
        before, after = plistlib.loads(data), plistlib.loads(out)
        after_job = after['LaunchDaemons'].pop('/' + JOB)
        before['LaunchDaemons'].pop('/' + JOB)
        self.assertEqual(before, after)
        self.assertEqual(after_job, dict(APPLE_JOB, RunAtLoad=True, POSIXSpawnType='Interactive'))

    def test_another_label_stops(self):
        with self.assertRaises(SystemExit):
            WARM['warm'](plistlib.dumps(dict(APPLE_JOB, Label='com.apple.other')))

    def test_script_writes_both_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            t = Path(temporary)
            (t / 'job').write_bytes(plistlib.dumps(APPLE_JOB))
            (t / 'cache').write_bytes(synthetic_cache())
            subprocess.run([sys.executable, '-I', str(PATCH / 'src/warm.py'), str(t / 'job'), str(t / 'cache'), str(t)],
                           check=True)
            self.assertEqual((t / 'job.plist').read_bytes(), WARM['warm'](plistlib.dumps(APPLE_JOB)))
            self.assertEqual((t / 'cache.plist').read_bytes(), WARM['warm'](synthetic_cache()))

    def test_hook_installs_like_apple_and_after_the_script(self):
        hook = (PATCH / 'src/kc-hook.sh').read_text()
        self.assertIn('"${OCLP_PYTHON:?}" -I "$H/warm.py" "$J" "$C" "$W"', hook)
        self.assertIn('install -o root -g wheel -m 644 "$W/$f" "$F"', hook)
        self.assertIn('chflags restricted "$F"', hook)
        self.assertNotIn('codesign', hook)

    @unittest.skipUnless(os.environ.get('KMHELPER_WARM_JOB'), 'set KMHELPER_WARM_JOB')
    def test_same_bytes_real_job(self):
        _, record = self.same(JOB_SPEC, Path(os.environ['KMHELPER_WARM_JOB']).read_bytes())
        self.assertEqual(record['expected'], 'match')

    @unittest.skipUnless(os.environ.get('KMHELPER_WARM_CACHE'), 'set KMHELPER_WARM_CACHE')
    def test_same_bytes_real_cache_after_userspace(self):
        pristine = Path(os.environ['KMHELPER_WARM_CACHE']).read_bytes()
        after_userspace, record = engine.apply(USERSPACE_CACHE_SPEC, pristine, repo=str(TOP))
        self.assertEqual(record['expected'], 'match')
        _, record = self.same(CACHE_SPEC, after_userspace)
        self.assertEqual(record['expected'], 'match')


if __name__ == '__main__':
    unittest.main()
