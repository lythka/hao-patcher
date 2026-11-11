"""Kernel identity selection must not silently fall back to a different ABI."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('profile_kit',str(ROOT/'kernel-fixes'))
spec=importlib.util.spec_from_loader(loader.name,loader)
kit=importlib.util.module_from_spec(spec);loader.exec_module(kit)

class Profiles(unittest.TestCase):
    def test_identity_requires_both_fields_and_reports_pending_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'fixture';folder.mkdir()
            profile=dict(kernel=dict(sha256='a'*64,uuid='b'*32),definitions={},pending={'early-vm.json':'needs Boot KC'})
            (folder/'profile.json').write_text(json.dumps(profile))
            with patch.object(kit,'PROFILES',root):
                self.assertEqual(kit.select_profile(sha256='a'*64),folder)
                self.assertEqual(kit.select_profile(uuid='b'*32),folder)
                with self.assertRaises(ValueError):kit.select_profile(sha256='c'*64,uuid='b'*32)
                with self.assertRaisesRegex(ValueError,'needs Boot KC'):kit.select_profile(uuid='b'*32,needs=['early-vm.json'])

    def test_changed_definition_and_explicit_wrong_kernel_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'fixture';folder.mkdir()
            (folder/'entry.json').write_text('changed')
            (folder/'profile.json').write_text(json.dumps(dict(kernel=dict(sha256='a'*64,uuid='b'*32),definitions={'entry.json':{'sha256':'c'*64}})))
            with patch.object(kit,'PROFILES',root), self.assertRaises(ValueError):
                kit.select_profile(uuid='b'*32,needs=['entry.json'])
            kernel=root/'kernel';kernel.write_bytes(b'another kernel')
            with self.assertRaisesRegex(ValueError,'another kernel'):
                kit.commpage_arguments(['--profile='+str(folder),'--kernel='+str(kernel)])

    def test_pending_profile_is_valid_capture_but_not_an_installable_artifact(self):
        for folder in (ROOT/'profiles').iterdir():
            if not (folder/'profile.json').exists():continue
            profile=json.loads((folder/'profile.json').read_text())
            for name, metadata in profile['definitions'].items():
                self.assertEqual(metadata['sha256'],hashlib.sha256((folder/name).read_bytes()).hexdigest())
            if (folder/'kc-recipe.json').exists():
                recipe=json.loads((folder/'kc-recipe.json').read_text())
                self.assertEqual(recipe['kernel_sha256'],profile['kernel']['sha256'])
                self.assertIn('early_vm_template',recipe)
