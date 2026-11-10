import importlib.util
from pathlib import Path
import plistlib
import unittest

spec = importlib.util.spec_from_file_location('golden_install', Path(__file__).resolve().parents[1]/'src/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

LABEL = 'com.apple.hybridsearchd'
JOB = 'System/Library/LaunchAgents/com.apple.hybridsearchd.plist'
CONDITION = {'#Else': False, '#IfFeatureFlagDisabled': 'GenerativeLearningPlatform/PlatformDaemons', '#Then': True}


def job():
    return {'Label': LABEL, 'Disabled': dict(CONDITION), 'ProgramArguments': ['/usr/libexec/hybridsearchd']}


class DisableJobTest(unittest.TestCase):
    def test_job_plist_keeps_xml(self):
        out = module.disabled_job(plistlib.dumps(job(), fmt=plistlib.FMT_XML), LABEL, JOB)
        self.assertTrue(out.startswith(b'<?xml'))
        result = plistlib.loads(out)
        self.assertIs(result['Disabled'], True)
        self.assertEqual(result['ProgramArguments'], ['/usr/libexec/hybridsearchd'])

    def test_launchd_cache_keeps_binary_and_other_jobs(self):
        other = {'Label': 'com.apple.other', 'Disabled': dict(CONDITION)}
        cache = {'LaunchDaemons': {'/' + JOB: job(), '/System/Library/LaunchDaemons/com.apple.other.plist': other},
                 'VersionNumber': 1}
        out = module.disabled_job(plistlib.dumps(cache, fmt=plistlib.FMT_BINARY), LABEL, JOB)
        self.assertTrue(out.startswith(b'bplist'))
        result = plistlib.loads(out)
        self.assertIs(result['LaunchDaemons']['/' + JOB]['Disabled'], True)
        self.assertEqual(result['LaunchDaemons']['/System/Library/LaunchDaemons/com.apple.other.plist'], other)
        self.assertEqual(result['VersionNumber'], 1)

    def test_other_job_stops(self):
        with self.assertRaises(SystemExit):
            module.disabled_job(plistlib.dumps({'Label': 'com.apple.other'}), LABEL, JOB)


if __name__ == '__main__':
    unittest.main()
