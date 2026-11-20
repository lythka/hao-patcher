import importlib.util
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'prespecialization-adoption.py'
spec = importlib.util.spec_from_file_location('prespecialization_adoption', TOOL)
adoption = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adoption)

WANT = dict(schema=1, cache_uuid='31da228748ef3350a12e097b4380dc8f', data='0x7ffe42f0f070', map='0x7ff87e92d000')
SLIDE = 0xdfda000


def log(map_unslid=0x7ff87e92d000, founds=('0x7ffe52ff0ba8', '0x0')):
    lines = ['Prespecializations library: Got dataPtr %#x from _dyld_get_swift_prespecialized_data' % (0x7ffe42f0f070 + SLIDE),
             'Prespecializations library:   pointerKeyedMetadataMap=%#x' % (map_unslid + SLIDE)]
    lines += ['Prespecializations library: Looking up description 0x7ff811baf20c in dyld table, found %s.' % f
              for f in founds]
    return '\n'.join(lines) + '\n'


class AdoptionTests(unittest.TestCase):
    def test_generated_map_with_a_hit_passes(self):
        r = adoption.judge(WANT, log())
        self.assertEqual(r['result'], 'PASS')
        self.assertEqual((r['lookups'], r['found']), (2, 1))

    def test_another_map_fails(self):
        r = adoption.judge(WANT, log(map_unslid=0x7ffe43000000))
        self.assertEqual(r['result'], 'FAIL')
        self.assertIn('not the cache build', r['reasons'][0])

    def test_no_hit_fails(self):
        self.assertEqual(adoption.judge(WANT, log(founds=('0x0',)))['result'], 'FAIL')

    def test_no_logging_fails(self):
        r = adoption.judge(WANT, 'nothing\n')
        self.assertEqual(r['result'], 'FAIL')
        self.assertIn('LOGGING unset', r['reasons'][0])


if __name__ == '__main__':
    unittest.main()
