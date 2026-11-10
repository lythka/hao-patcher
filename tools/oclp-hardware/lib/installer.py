"""Apply the explicit hardware donor map to already mounted offline System/Data roots."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import runpy
import shutil
import stat
import subprocess

NONMETAL_KEYS = ['Moraea_DarkMenuBar', 'Moraea_BlurBeta', 'Moraea.EnableSpinHack', 'Amy.MenuBar2Beta',
                 'Moraea_RimBetaDisabled', 'Moraea_ColorWidgetDisabled', 'Moraea_BacklightHack',
                 'ShowDate', 'InternalDebugUseGPUProcessForCanvasRenderingEnabled',
                 'WebKitExperimentalUseGPUProcessForCanvasRenderingEnabled',
                 'WebKitPreferences.acceleratedDrawingEnabled', 'NSEnableAppKitMenus', 'NSZoomButtonShowMenu']


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def edit_preferences(path, keys, boolean_only=False):
    if not path.exists():
        return []
    raw = path.read_bytes(); values = plistlib.loads(raw)
    changed = [key for key in keys if key in values and
               (not boolean_only or values[key] in (False, True, '0', '1', 'false', 'true'))]
    if changed:
        for key in changed:
            del values[key]
        path.write_bytes(plistlib.dumps(values, fmt=plistlib.FMT_BINARY if raw.startswith(b'bplist') else plistlib.FMT_XML,
                                       sort_keys=False))
    return changed


def cleanup(system, data):
    removed = []
    plugins = data/'Library/Application Support/SkyLightPlugins'
    if plugins.exists():
        remove(plugins); removed.append(str(plugins.relative_to(data)))
    preferences = {}
    for name, keys, limited in [('com.apple.CoreDisplay.plist', ['useMetal', 'useIOP'], True),
                                 ('.GlobalPreferences.plist', NONMETAL_KEYS, False)]:
        path = data/'Library/Preferences'/name
        changed = edit_preferences(path, keys, limited)
        if changed:
            preferences[name] = changed
    old_record = system/'System/Library/CoreServices/OpenCore-Legacy-Patcher.plist'
    if old_record.exists():
        for patch in plistlib.loads(old_record.read_bytes()).values():
            if not isinstance(patch, dict):
                continue
            for operation in ('Overwrite System Volume', 'Overwrite Data Volume', 'Merge System Volume', 'Merge Data Volume'):
                for folder, entries in patch.get(operation, {}).items():
                    if not folder.endswith('Extensions'):
                        continue
                    for name in entries:
                        path = data/'Library/Extensions'/name
                        if name.endswith('.kext') and path.exists():
                            remove(path); removed.append(str(path.relative_to(data)))
    relocated = data/'Library/Relocated Extensions'
    relocated.mkdir(parents=True, exist_ok=True, mode=0o755)
    for path in (data/'Library/Extensions').glob('*.kext'):
        if datetime.datetime.fromtimestamp(path.stat().st_mtime) < datetime.datetime(2021, 10, 1):
            destination = relocated/path.name
            remove(destination); shutil.move(str(path), destination)
            removed.append(str(path.relative_to(data))+' -> '+str(destination.relative_to(data)))
    return dict(removed=removed, preferences=preferences)


def target_identity(system):
    version = plistlib.loads((system/'System/Library/CoreServices/SystemVersion.plist').read_bytes())
    kernel = system/'System/Library/Kernels/kernel'
    match = re.search(rb'Darwin Kernel Version (\d+)\.(\d+)\.(\d+)', kernel.read_bytes())
    if match is None:
        raise ValueError('target kernel has no Darwin identity')
    return dict(product_version=version['ProductVersion'], product_build=version['ProductBuildVersion'],
                darwin_major=int(match[1]), darwin_minor=int(match[2]),
                kernel_sha256=hashlib.sha256(kernel.read_bytes()).hexdigest())


def apply(artifact, system, data, owner='root:wheel'):
    artifact, system, data = (Path(path).resolve() for path in (artifact, system, data))
    if system == Path('/') or data == Path('/'):
        raise ValueError('offline System/Data roots are required; refusing the booted root')
    profile = json.loads((artifact/'install-map.json').read_text())
    build = json.loads((artifact/'build.json').read_text())
    identity = target_identity(system)
    if identity['darwin_major'] != profile['target_darwin_major']:
        raise ValueError('hardware map applies to Darwin '+str(profile['target_darwin_major']))
    helpers = runpy.run_path(str(Path(__file__).with_name('files.py')))
    helpers['subprocess_wrapper'].owner = owner
    clean = cleanup(system, data)
    installed = []
    for entry in profile['entries']:
        destination = system/entry['destination'].lstrip('/')
        # Unlike OCLP's silent skip, a missing GG parent is an unapplied map entry.
        if not destination.parent.is_dir():
            raise FileNotFoundError('hardware destination parent missing: '+str(destination.parent))
        source = artifact/'payload'/entry['support_package_relative_path']
        helpers['install_new_file'](str(source.parent), str(destination.parent), source.name, entry['operation'])
        installed.append(entry['destination'])
    repair = data/'private/etc/rc.server'
    repair.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    subprocess.run(['ditto', str(artifact/'payload/RSRRepair'), str(repair)], check=True)
    subprocess.run(['/bin/chmod', '755', str(repair)], check=True)
    subprocess.run(['/usr/sbin/chown', owner, str(repair)], check=True)
    record = {'OpenCore Legacy Patcher': 'v'+profile['oclp_version'], 'PatcherSupportPkg': 'v'+profile['psp_version'],
              'Time Patched': datetime.datetime.now().isoformat(), 'OCLP Source Commit': profile['source_commit'],
              'OS Version': '%s.%s (%s)' % (identity['darwin_major'], identity['darwin_minor'], identity['product_build']),
              'Hardware Donor Policy': profile['donor_policy'], 'Custom Signature': build['psp_custom_signature'],
              'Kernel Debug Kit Used': 'Explicit external donor stage; see image build record',
              'Metal Library Used': 'Explicit static PSP hardware map', **profile['patches']}
    path = system/'System/Library/CoreServices/OpenCore-Legacy-Patcher.plist'
    path.write_bytes(plistlib.dumps(record, sort_keys=False))
    path.chmod(0o644)
    subprocess.run(['/usr/sbin/chown', owner, str(path)], check=True)
    return dict(operation='apply-oclp-hardware', actual_target=identity, donor_policy=profile['donor_policy'],
                installed=installed, cleanup=clean, rsrrepair_sha256=hashlib.sha256(repair.read_bytes()).hexdigest(),
                finalization='REQUIRED: declared donor KDK, final Boot/System KC, Preboot and snapshot stages')
