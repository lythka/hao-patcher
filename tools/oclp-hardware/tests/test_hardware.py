"""PSP hardware extraction/application on generated ordinary-file fixtures only."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
TOOL = runpy.run_path(str(HERE/'make'))
INSTALL = runpy.run_path(str(HERE/'lib/installer.py'))
OWNER = '%d:%d' % (os.getuid(), os.getgid())


class Hardware(unittest.TestCase):
    def inputs(self, root):
        profile = json.loads((HERE/'profiles/mbp92.json').read_text())
        archive = root/'PSP.dmg'; archive.write_bytes(b'synthetic PSP archive fixture')
        profile['psp_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        profile_path = root/'profile.json'; profile_path.write_text(json.dumps(profile))
        psp = root/'psp'; psp.mkdir(); (psp/'.signed').touch()
        for entry in profile['entries']:
            path = psp/entry['support_package_relative_path']
            if path.suffix in ('.framework', '.bundle', '.kext'):
                (path/'Versions/A').mkdir(parents=True, exist_ok=True)
                (path/'Versions/A/payload').write_text(entry['support_package_relative_path'])
                stamp = 1600000000+int(hashlib.sha256(entry['source_version'].encode()).hexdigest()[:6],16)
                os.utime(path/'Versions/A/payload', (stamp,stamp))
                if path.name == 'Metal.framework':
                    (path/'Versions/A/Metal').write_bytes(b'PSP Metal shim')
                    (path/'Versions/A/MetalOld.dylib').write_bytes(b'PSP already-renamed old Metal')
                    (path/'Versions/A/Shared').write_bytes(b'stock identical shared resource')
                    (path/'Versions/A/Shared').chmod(0o644)
                    os.utime(path/'Versions/A/Shared', (1234567891, 1234567891))
                    if not (path/'Versions/Current').is_symlink():
                        (path/'Versions/Current').symlink_to('A')
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(entry['support_package_relative_path'])
        rsr = root/'RSRRepair'; rsr.write_bytes(b'fixture RSRRepair executable')
        return profile, profile_path, psp, archive, rsr

    def target(self, root, profile):
        system, data = root/'System-root', root/'Data-root'
        for entry in profile['entries']:
            (system/entry['destination'].lstrip('/')).parent.mkdir(parents=True, exist_ok=True)
        (system/'System/Library/CoreServices').mkdir(parents=True, exist_ok=True)
        version = system/'System/Library/CoreServices/SystemVersion.plist'
        version.write_bytes(plistlib.dumps(dict(ProductVersion='27.0', ProductBuildVersion='26A428')))
        kernel = system/'System/Library/Kernels/kernel'; kernel.parent.mkdir(parents=True)
        kernel.write_bytes(b'fixture Darwin Kernel Version 27.0.0: real target identity')
        (data/'Library/Extensions').mkdir(parents=True)
        return system, data

    def test_map_is_exact_nine_sets_and_twenty_five_ordered_operations(self):
        p = json.loads((HERE/'profiles/mbp92.json').read_text())
        self.assertEqual(9, len(p['patchset_order']))
        self.assertEqual(25, len(p['entries']))
        self.assertEqual(16, sum(e['operation'] == 'Overwrite System Volume' for e in p['entries']))
        self.assertEqual(9, sum(e['operation'] == 'Merge System Volume' for e in p['entries']))
        wireless = [e for e in p['entries'] if e['patch'] == 'Modern Wireless Common']
        self.assertEqual(3, len(wireless)); self.assertTrue(all(e['source_version']=='13.7.2-25' for e in wireless))
        self.assertEqual(25, p['donor_policy']['darwin_major'])
        self.assertEqual(27, p['target_darwin_major'])

    def test_extracted_artifact_installs_independently_and_preserves_copy_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile, profile_path, psp, archive, rsr = self.inputs(root)
            artifact = TOOL['build'](psp, archive, rsr, root/'artifact', profile_path)
            record = json.loads((artifact/'build.json').read_text())
            self.assertEqual(profile['source_commit'], record['oclp_source_commit'])
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), record['inputs']['psp_archive']['sha256'])
            self.assertTrue(record['psp_custom_signature'])
            system, data = self.target(root, profile)
            version = system/'System/Library/CoreServices/SystemVersion.plist'; version_before = version.read_bytes()
            kernel = system/'System/Library/Kernels/kernel'; kernel_before = kernel.read_bytes()
            metal = system/'System/Library/Frameworks/Metal.framework/Versions/A'; metal.mkdir(parents=True)
            untouched = metal/'NotInPSP'; untouched.write_bytes(b'unrelated GG file'); untouched.chmod(0o600)
            shared = root/'shared-outside'; shared.write_bytes(b'stock identical shared resource'); shared.chmod(0o644)
            os.utime(shared, (1234567891, 1234567891)); os.link(shared, metal/'Shared')
            kext = system/'System/Library/Extensions/AppleHDA.kext'; kext.mkdir(); (kext/'stale-child').write_text('remove me')
            preferences = data/'Library/Preferences'; preferences.mkdir()
            (preferences/'.GlobalPreferences.plist').write_bytes(plistlib.dumps(dict(Moraea_DarkMenuBar=True, KeepMe='stock')))
            (preferences/'com.apple.CoreDisplay.plist').write_bytes(plistlib.dumps(dict(useMetal=False, useIOP='unrelated')))
            plugins = data/'Library/Application Support/SkyLightPlugins'; plugins.mkdir(parents=True); (plugins/'old').touch()
            shutil.rmtree(psp)  # The installer must consume only its independent artifact, never source roots.
            command = [sys.executable, '-I', '-B', str(artifact/'install-tools/apply'), 'apply', '--artifact', str(artifact),
                       '--system-root', str(system), '--data-root', str(data), '--owner', OWNER]
            result = subprocess.run(command, cwd='/private/tmp', capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            applied = json.loads(result.stdout)
            self.assertEqual(25, len(applied['installed']))
            self.assertEqual(27, applied['actual_target']['darwin_major'])
            self.assertEqual(25, applied['donor_policy']['darwin_major'])
            self.assertEqual(version_before, version.read_bytes()); self.assertEqual(kernel_before, kernel.read_bytes())
            self.assertEqual(b'PSP Metal shim', (metal/'Metal').read_bytes())
            self.assertEqual(b'PSP already-renamed old Metal', (metal/'MetalOld.dylib').read_bytes())
            self.assertEqual('A', os.readlink(metal.parent/'Current'))
            self.assertEqual(0o600, stat.S_IMODE(untouched.stat().st_mode))
            self.assertEqual(0o644, stat.S_IMODE(shared.stat().st_mode))
            self.assertFalse((kext/'stale-child').exists())
            self.assertTrue((kext/'Versions/A/payload').read_text().startswith('26.0 Beta 1/'))
            opencl = system/'System/Library/Frameworks/OpenCL.framework/Versions/A/payload'
            self.assertTrue(opencl.read_text().startswith('11.6/'))
            self.assertFalse(plugins.exists())
            self.assertEqual({'KeepMe':'stock'}, plistlib.loads((preferences/'.GlobalPreferences.plist').read_bytes()))
            self.assertEqual({'useIOP':'unrelated'}, plistlib.loads((preferences/'com.apple.CoreDisplay.plist').read_bytes()))
            self.assertEqual(rsr.read_bytes(), (data/'private/etc/rc.server').read_bytes())
            self.assertEqual(0o755, stat.S_IMODE((data/'private/etc/rc.server').stat().st_mode))
            patched = plistlib.loads((system/'System/Library/CoreServices/OpenCore-Legacy-Patcher.plist').read_bytes())
            self.assertEqual('27.0 (26A428)', patched['OS Version'])
            self.assertEqual(25, patched['Hardware Donor Policy']['darwin_major'])
            self.assertEqual(profile['patchset_order'], [k for k in patched if k in profile['patchset_order']])

    def test_wrong_archive_fails_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); profile, profile_path, psp, archive, rsr = self.inputs(root)
            archive.write_bytes(b'wrong input')
            with self.assertRaisesRegex(ValueError, 'PSP archive differs'):
                TOOL['build'](psp, archive, rsr, root/'artifact', profile_path)
            self.assertFalse((root/'artifact').exists())

    def test_cleanup_uses_only_declared_target_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); system=root/'system';data=root/'data'
            record=system/'System/Library/CoreServices/OpenCore-Legacy-Patcher.plist';record.parent.mkdir(parents=True)
            record.write_bytes(plistlib.dumps({'old':{'Overwrite System Volume':{'/System/Library/Extensions':{'Old.kext':'old'}}}}))
            extensions=data/'Library/Extensions';extensions.mkdir(parents=True)
            (extensions/'Old.kext').mkdir();(extensions/'Keep.kext').mkdir();(extensions/'Ancient.kext').mkdir()
            os.utime(extensions/'Ancient.kext',(946684800,946684800))
            INSTALL['cleanup'](system,data)
            self.assertFalse((extensions/'Old.kext').exists())
            self.assertTrue((extensions/'Keep.kext').is_dir())
            self.assertTrue((data/'Library/Relocated Extensions/Ancient.kext').is_dir())

    def test_live_root_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'offline System/Data'):
            INSTALL['apply']('/unused','/','/unused')


if __name__ == '__main__':
    unittest.main()
