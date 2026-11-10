import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
TOP = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


live = load('identity_live', HERE/'src/live_text.py')
mapping = load('identity_mapping_test', HERE/'src/map.py')
editor = load('identity_editor_test', HERE/'src/macho_dependencies.py')
fixtures = load('identity_fixture_test', HERE/'tests/macho_fixture.py')
macho = load('identity_fat_test', HERE/'src/macho.py')
fat = types.SimpleNamespace(CPU_ARM64=0x100000c,
    slices=lambda data: [(cpu, sub, body) for (cpu, sub), body in macho.slices(data).items()],
    combine=lambda base, extra, **kw: macho.combine(base, extra))


class LiveTextIdentityTests(unittest.TestCase):
    def setUp(self):
        self.source = struct.pack('<8I', 0xfeedfacf, 0x100000c, 2, 2, 0, 0, 0, 0) + b'original ARM'
        self.stub = fixtures.fixture()
        self.embedded = fat.combine(self.source, self.stub)
        self.entry = {'path': live.PHOTOS, 'stub': 'stubs/photos',
                      'source_sha256': live.digest(self.source),
                      'stub_sha256': live.digest(self.stub),
                      'result_sha256': live.digest(self.embedded)}

    def test_reconstruct_preserves_original_arm_and_signature_slots(self):
        final, report, arms = live.reconstruct(self.source, self.stub, self.entry, fat, editor)
        self.assertNotEqual(live.digest(final), self.entry['result_sha256'])
        self.assertEqual(arms[0]['sha256'], live.digest(self.source))
        self.assertTrue(report['special_slots_unchanged'])
        self.assertTrue(report['entitlement_blobs_unchanged'])

    def test_versioned_root_changes_exact_final_identity(self):
        original, _, _ = live.reconstruct(self.source, self.stub, self.entry, fat, editor)
        root = '/Library/hao-live-text/r4'
        final, report, _ = live.reconstruct(self.source, self.stub, self.entry, fat, editor, root)
        self.assertNotEqual(live.digest(original), live.digest(final))
        self.assertEqual(report['added_dependencies'], [root+'/libLiveText.dylib'])

    def test_rejects_noncanonical_root_or_wrong_dependency(self):
        for root in ('/Library/hao-live-text/r4/', '/Library/hao-live-text/r4/../r5',
                     '/Library/hao-live-text/r01', '/Library/hao-live-text/r0',
                     '/Library//hao-live-text/r4', '/Library/hao-live-text/./r4',
                     '/Library/hao-live-text/r4/sub', '/tmp/r4', None):
            with self.subTest(root=root), self.assertRaises(ValueError):
                live.package_root({'install_root': root, 'load_paths':[str(root)+'/libLiveText.dylib']})
        with self.assertRaises(ValueError):
            live.package_root({'install_root':'/Library/hao-live-text/r4','load_paths':[live.DEPENDENCY]})

    def test_source_stub_and_manifest_result_must_match(self):
        for key in ('source_sha256', 'stub_sha256', 'result_sha256'):
            with self.subTest(key=key):
                entry = dict(self.entry, **{key: '00'*32})
                with self.assertRaises(ValueError):
                    live.reconstruct(self.source, self.stub, entry, fat, editor)

    def test_changed_original_arm_refused_even_if_editor_reports_success(self):
        def corrupt(data, paths):
            result, report = editor.append_dylibs(data, paths)
            result = result.replace(b'original ARM', b'changed! ARM')
            return result, report
        with self.assertRaisesRegex(ValueError, 'ARM slices changed'):
            live.reconstruct(self.source, self.stub, self.entry, fat,
                             types.SimpleNamespace(append_dylibs=corrupt))

    def test_transform_updates_photos_aliases_only_and_does_not_mutate_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload, original, runtime = (root/name for name in ('payload','original','runtime'))
            source = original/live.PHOTOS
            source.parent.mkdir(parents=True)
            source.write_bytes(self.source)
            (source.parent.parent/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'Photos'}))
            stub = payload/'embed/stubs/photos'
            stub.parent.mkdir(parents=True)
            stub.write_bytes(self.stub)
            (payload/'hao_fat.py').write_bytes(b'authored fat fixture')
            helper = runtime/'install-tools/macho_dependencies.py'
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b'authored editor fixture')
            record = {'tool':'tools/live-text-runtime','revision':3,'install_root':live.ROOT,
                      'load_paths':[live.DEPENDENCY], 'commit':'fixture',
                      'files':{'install-tools/macho_dependencies.py':live.digest(helper.read_bytes())}}
            (runtime/'build.json').write_text(json.dumps(record))
            (original/'Other').write_bytes(self.source)
            other = dict(self.entry, path='Other')
            manifest = {'files':[self.entry, other]}
            before = copy.deepcopy(manifest)
            with patch.object(live, 'load', side_effect=[fat, editor]):
                transformed, provenance = live.transform(manifest,payload,original,runtime)
            self.assertEqual(manifest,before)
            self.assertEqual(transformed['files'][1],other)
            rows = mapping.rows(transformed,original)
            self.assertEqual(rows[0][3],rows[1][3])
            self.assertEqual(rows[1][0],'/System/Applications/Photos.app')
            self.assertNotEqual(rows[0][3],bytes.fromhex(self.entry['result_sha256']))
            self.assertEqual(rows[2][3],bytes.fromhex(other['result_sha256']))
            self.assertEqual(provenance['final_sha256'],rows[0][3].hex())
            helper.write_bytes(b'changed editor')
            with self.assertRaisesRegex(ValueError, 'editor hash differs'):
                live.transform(manifest,payload,original,runtime)


if __name__ == '__main__':
    unittest.main()
