import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('golden_install', Path(__file__).resolve().parents[1]/'src/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReplaceTest(unittest.TestCase):
    def test_restore_special_modes_and_skip_symlink_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root/'source', root/'target'
            source.mkdir(); target.mkdir()
            for name, mode in [('login', 0o4555), ('group', 0o2555), ('plain', 0o644)]:
                (source/name).write_text(name); (source/name).chmod(mode)
                (target/name).write_text(name); (target/name).chmod(mode & 0o777)
            outside = root/'outside'; outside.write_text('outside'); outside.chmod(0o600)
            (source/'link').symlink_to(outside); (target/'link').symlink_to(outside)
            module.restore_special_modes(source, target)
            self.assertEqual((target/'login').stat().st_mode & 0o7777, 0o4555)
            self.assertEqual((target/'group').stat().st_mode & 0o7777, 0o2555)
            self.assertEqual(outside.stat().st_mode & 0o7777, 0o600)

    def test_prepared_golden_system_keeps_applied_donors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            system, data = root/'system', root/'data'
            system.mkdir(); data.mkdir()
            (system/'native-driver').write_bytes(b'Tahoe donor after Golden copy')
            (data/'first-boot-template').write_bytes(b'Golden template')
            module.install_base_trees(root/'absent-original', system, data, set(), set(), set(), prepared=True)
            self.assertEqual((system/'native-driver').read_bytes(), b'Tahoe donor after Golden copy')
            self.assertEqual((data/'first-boot-template').read_bytes(), b'Golden template')

    def test_prepared_mode_cannot_inherit_a_tahoe_cryptex(self):
        with self.assertRaises(SystemExit):
            module.arguments(['system', 'golden', 'shim', 'wifi', '--prepared-system'])
        args = module.arguments(['system', 'golden', 'shim', 'wifi', '--prepared-system',
                                 '--driverkit-artifact', 'donor', '--donor-tool', 'extractor'])
        self.assertEqual(args.driverkit_artifact, Path('donor'))

    def test_explicit_driverkit_family_needs_no_installed_donor_image(self):
        import shutil
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root/'extracted', root/'golden/System/DriverKit/dyld'
            source.mkdir()
            for name in ('dyld_shared_cache_x86_64', 'dyld_shared_cache_x86_64.01', 'dyld_shared_cache_arm64e'):
                (source/name).write_text(name)
            with patch.object(module, 'copy_path', side_effect=shutil.copy2):
                copied = module.install_driverkit_cache(source, target)
            self.assertEqual(copied, ['dyld_shared_cache_x86_64', 'dyld_shared_cache_x86_64.01'])
            self.assertEqual(sorted(p.name for p in target.iterdir()), copied)
            self.assertFalse((root/'os.dmg').exists())

    def test_missing_explicit_driverkit_family_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, 'no x86_64 DriverKit cache'):
                module.install_driverkit_cache(root, root/'output')
            self.assertFalse((root/'output').exists())

    def test_external_mps_plan_preserves_binary_edits_and_paths(self):
        cache = Path('/input/cache family')
        payload = '{"/input/cache family/dyld_shared_cache_x86_64.01": [[32,"00ff","aabb","repair"]]}'
        with patch.object(module.subprocess, 'check_output', return_value=payload) as call:
            result = module.matrix_plan(cache)
        self.assertEqual(result, {cache/'dyld_shared_cache_x86_64.01': [(32, b'\x00\xff', b'\xaa\xbb', 'repair')]})
        args = call.call_args.args[0]
        self.assertEqual(args[-3:], ['mps-plan', '--cache', str(cache)])
        self.assertTrue(args[2].endswith('/inputs/metal_fix_source/metal-fixes'))

    def test_external_audio_plan_resolves_subcache_and_binary_edit(self):
        cache = Path('/input/cache family')
        payload = '{"dyld_shared_cache_x86_64.03": [[563,"ff158738771e","660f1f440000","no-console repair"]]}'
        with patch.object(module.subprocess, 'check_output', return_value=payload) as call:
            result = module.audio_plan(cache)
        self.assertEqual(result, {cache/'dyld_shared_cache_x86_64.03': [
            (563, bytes.fromhex('ff158738771e'), bytes.fromhex('660f1f440000'), 'no-console repair')]})
        args = call.call_args.args[0]
        self.assertTrue(args[2].endswith('/inputs/audio_fix_source/audiomxd-fix'))
        self.assertEqual(args[-3:], ['plan', '--cache', str(cache)])

    def test_replaces_data_contents_without_unlinking_firmlink_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root/'source', root/'target'
            for folder in (source, target):
                (folder/'usr/share/snmp').mkdir(parents=True)
            anchor = target/'usr/share/snmp'
            inode = anchor.stat().st_ino
            (anchor/'obsolete').write_text('old')
            (source/'usr/share/snmp/new').write_text('Golden Gate')
            original_remove = module.remove

            def guarded_remove(path):
                self.assertFalse(path == anchor or path in anchor.parents)
                original_remove(path)

            with patch.object(module, 'remove', guarded_remove):
                module.replace_tree(source, target, set(), anchors={'usr/share/snmp'})
            self.assertEqual(anchor.stat().st_ino, inode)
            self.assertFalse((anchor/'obsolete').exists())
            self.assertEqual((anchor/'new').read_text(), 'Golden Gate')

    def test_preserves_driver_and_firmlink_but_removes_obsolete_userspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target, data = [root/name for name in ('source', 'target', 'data')]
            for folder in (source, target, data):
                folder.mkdir()
            for folder in (source, target):
                (folder/'System/Extensions').mkdir(parents=True)
                (folder/'System/Extensions/driver').write_text(folder.name)
            (target/'Library').symlink_to(data)
            (source/'Library').mkdir()
            (data/'untouched').write_text('firmlink target')
            (target/'System/obsolete').write_text('old')
            (source/'System/new-service').write_text('Golden Gate')
            (source/'etc').symlink_to('private/etc')
            module.replace_tree(source, target, {'System/Extensions', 'Library'})
            self.assertEqual((target/'System/Extensions/driver').read_text(), 'target')
            self.assertEqual((data/'untouched').read_text(), 'firmlink target')
            self.assertFalse((target/'System/obsolete').exists())
            self.assertEqual((target/'System/new-service').read_text(), 'Golden Gate')
            self.assertEqual((target/'etc').readlink(), Path('private/etc'))

    def test_never_walks_an_unexpected_target_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'source').mkdir()
            (root/'elsewhere').mkdir()
            (root/'target').symlink_to(root/'elsewhere')
            with self.assertRaisesRegex(ValueError, 'ancestor is a symlink'):
                module.replace_tree(root/'source', root/'target', {'kept'})
