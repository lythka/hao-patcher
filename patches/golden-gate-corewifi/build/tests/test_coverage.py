"""Host tests for the CoreWiFi override's coverage check (corewifi_coverage.py), on authored fixtures: the method pass
(gaps on every 27.x class, superclass and category answers, absent classes, compat methods that would shadow Tahoe's),
the symbol pass (strong vs weak, code exported as data), the patch-table reader (self-only symbols need no answer,
uniqued GOT uses count as foreign) and the compat source's method table.
Run: python3.14 -I -B -m unittest discover -s patches/golden-gate-corewifi/build/tests"""
from pathlib import Path
import re
import sys
import unittest

BUILD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUILD))
import corewifi_coverage as coverage  # noqa: E402


def cls(methods, super_='NSObject', defined=True):
    return {'super': super_, 'defined': defined, 'methods': set(methods)}


class Methods(unittest.TestCase):
    build = {
        'CWFInterface': cls({'-activate', '-setBTCoexActiveProfileName:error:', '-ssid'}),
        'CWFWiFiUserAgent': cls({'-start'}, 'CWFInterface'),
        'CWFLinkQualityAssessment': cls({'-qualityAssessment'}),
        'CWFColocatedConsentResult': cls({'-status'}),
    }
    tahoe = {
        'CWFInterface': cls({'-activate', '-ssid'}),
        'CWFWiFiUserAgent': cls({'-start'}, 'CWFInterface'),
    }

    def test_gap_on_unbound_class_and_inherited(self):
        clients = {'WirelessRadioManagerd [x86_64]': (set(), {'setBTCoexActiveProfileName:error:', 'activate'})}
        gaps, absent, shadowed = coverage.assess_methods(self.build, self.tahoe, {}, set(), clients)
        self.assertEqual(sorted(gaps), [('CWFInterface', '-setBTCoexActiveProfileName:error:'),
                                        ('CWFWiFiUserAgent', '-setBTCoexActiveProfileName:error:')])
        self.assertEqual((absent, shadowed), ({}, []))

    def test_compat_category_on_superclass_answers_subclass(self):
        compat = {'CWFInterface': cls({'-setBTCoexActiveProfileName:error:'}, None, False)}
        clients = {'c': (set(), {'setBTCoexActiveProfileName:error:'})}
        gaps, _, shadowed = coverage.assess_methods(self.build, self.tahoe, compat, set(), clients)
        self.assertEqual((gaps, shadowed), ({}, []))

    def test_nsobject_answers(self):
        clients = {'c': (set(), {'setBTCoexActiveProfileName:error:'})}
        gaps, _, _ = coverage.assess_methods(self.build, self.tahoe, {}, {'-setBTCoexActiveProfileName:error:'},
                                             clients)
        self.assertEqual(gaps, {})

    def test_absent_unless_bound(self):
        clients = {'SymptomEvaluator': (set(), {'qualityAssessment'})}
        gaps, absent, _ = coverage.assess_methods(self.build, self.tahoe, {}, set(), clients)
        self.assertEqual((gaps, absent), ({}, {'CWFLinkQualityAssessment': ['-qualityAssessment']}))
        compat = {'CWFColocatedConsentResult': cls(set(), 'NSObject', True)}
        clients = {'SpeakerGroupSetupKit': ({'CWFColocatedConsentResult'}, {'status'})}
        gaps, absent, _ = coverage.assess_methods(self.build, self.tahoe, compat, set(), clients)
        self.assertEqual(sorted(gaps), [('CWFColocatedConsentResult', '-status')])

    def test_shadowing_is_reported(self):
        compat = {'CWFWiFiUserAgent': cls({'-ssid'}, None, False)}
        _, _, shadowed = coverage.assess_methods(self.build, self.tahoe, compat, set(), {})
        self.assertEqual(shadowed, [('CWFWiFiUserAgent', '-ssid')])


class Symbols(unittest.TestCase):
    def test_strong_weak_and_kind(self):
        imports = {'_CWFEventLinkQualityAssessmentKey': {'SymptomEvaluator [x86_64]'},
                   '_CWFNewThing': {'nearbyd [x86_64] (weak)'},
                   '_CWFGetOSLog': {'shared cache'},
                   '_CWFDataThing': {'shared cache'}}
        providers = [('compat', {'_CWFEventLinkQualityAssessmentKey': '__DATA_CONST'}),
                     ('Tahoe CoreWiFi', {'_CWFGetOSLog': '__TEXT', '_CWFDataThing': '__DATA'})]
        resolved, errors = coverage.assess_symbols(imports, {'_CWFDataThing': 'code'}, providers)
        self.assertEqual(resolved, {'_CWFEventLinkQualityAssessmentKey': 'compat', '_CWFNewThing': 'weak, unresolved',
                                    '_CWFGetOSLog': 'Tahoe CoreWiFi'})
        self.assertEqual(len(errors), 1)
        self.assertIn('_CWFDataThing', errors[0])


PATCHES = '''0x7ff843641d60: _OBJC_CLASS_$_CWFLinkQualityAssessment
    0x7ff842e36198: PATCH  CoreWiFi
    0x7ff842e573d0: PATCH  CoreWiFi
0x7ff843641400: _OBJC_CLASS_$_CWFInterface
    0x7ff8402dce78: PATCH (weak_import) Network
    0x7ff842e36188: PATCH  CoreWiFi
0x7ff842e110f8: _CWFEventLinkQualityMetricKey
    0x7ff940063ba0: GOT (weak_import)
0x7ff8436417c0: _OBJC_CLASS_$_CWFUserSettings
    0x7ff842e36080: PATCH  CoreWiFi
    0x7ffe40040ec0: GOT
'''


class PatchTable(unittest.TestCase):
    def test_self_only_and_uniqued_got(self):
        saved = coverage.out
        coverage.out = lambda *args: PATCHES
        try:
            bound, self_only = coverage.cache_imports('cache')
        finally:
            coverage.out = saved
        self.assertEqual(bound, {'_OBJC_CLASS_$_CWFInterface', '_CWFEventLinkQualityMetricKey',
                                 '_OBJC_CLASS_$_CWFUserSettings'})
        self.assertEqual(self_only, {'_OBJC_CLASS_$_CWFLinkQualityAssessment'})


class Table(unittest.TestCase):
    def test_compat_source_table(self):
        entries = coverage.compat_table(BUILD/'corewifi_compat.m')
        self.assertEqual(len(entries), 16)
        self.assertEqual(len({(c, s) for c, s, _, _ in entries}), 16)
        self.assertIn(('CWFInterface', 'setBTCoexActiveProfileName:error:', 'c32@0:8@16^@24', 'SetBTCoexProfile'),
                      entries)
        for _, selector, types, imp in entries:
            parts = re.findall(r'(\D+)(\d+)', types)   # return type and frame size, self, _cmd, arguments
            self.assertEqual([t for t, _ in parts[1:3]], ['@', ':'], types)
            self.assertEqual(len(parts) - 3, selector.count(':'), (selector, types))
            if imp == 'ReturnZero':
                self.assertIn(types[0], 'Qciq@')
            if imp in ('IgnoreValue', 'DoNothing'):
                self.assertEqual(types[0], 'v')


if __name__ == '__main__':
    unittest.main()
