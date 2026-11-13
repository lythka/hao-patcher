"""Make the already installed Golden Gate System run natively on Intel (run by src/kc-hook.sh).

The base layers (pipeline/stages/golden-image.py) already copied Golden Gate's System/Data and installed the Tahoe
donors and OCLP's hardware overlay; nothing here replaces or keeps System trees. This installs the combined OS cryptex
(Golden Gate SystemOS + RosettaOS x86_64 cache + Tahoe's x86_64 DriverKit cache, with the cache repairs and the code
identity carrier) and the App cryptex, each with the hao stubs of its arm64-only programs (src/cryptex_stubs.py),
the OCLP compat libraries, the CoreWiFi compat library in front of Tahoe's CoreWiFi, the display-state shim and its
re-signed clients, and disables jobs that cannot run on this Mac. src/cryptex.py builds the two images; with
--cryptex-cache, src/cryptex_cache.py copies an earlier build of the same inputs, code and builder tools instead."""
import argparse
from contextlib import contextmanager, ExitStack
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from macho import add_load_dylib, section_ranges, set_install_name
# install.py's own steps use run, disk_info, image_mount and remove; the rest is named here for the tests.
from cryptex import (build_app_image, build_os_image, disk_info, image_mount, remove, run,  # noqa: F401
                     matrix_plan, audio_plan, replace_tree, restore_special_modes)
import cryptex_cache


@contextmanager
def volume_mount(device, folder):
    info = disk_info(device)
    previous = info.get('MountPoint')
    if previous:
        # chown/ditto keep owners only with ownership enabled; do not rely on an earlier hook or the builder's history
        run('diskutil', 'enableOwnership', device)
        yield Path(previous)
    else:
        folder.mkdir(mode=0o755)
        run('diskutil', 'mount', '-mountOptions', 'nobrowse', '-mountPoint', folder, device)
        run('diskutil', 'enableOwnership', device)
        try:
            yield folder
        finally:
            run('diskutil', 'unmount', device)



# Tahoe programs kept for their x86_64 code but serving 27.0 clients take the 27.0 program's identity: its embedded
# Info.plist (__TEXT,__info_plist), entitlements and identifier, re-signed ad hoc. Tahoe's
# WindowServer launcher traps in BSServiceConnectionListener when 27.0's SkyLight listens on BKTouchStream, a
# BoardServices service that only 27.0's embedded BSServiceDomains declares (entitlements alone did not help).
RESIGN_WITH_27_ENTITLEMENTS = (
    'System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer',
)
# 27.0 jobs that cannot run on this Mac, shipped with Disabled=true (the key Apple uses for a job it does not start), in
# the job's plist and in its entry of launchd's job cache. hybridsearchd (HybridSearchRuntime) stops itself on any Mac
# without Apple silicon ("Not supported on non-Apple silicon device") and launchd restarts it: a crash loop.
DISABLED_JOBS = {'com.apple.hybridsearchd': 'System/Library/LaunchAgents/com.apple.hybridsearchd.plist'}
LAUNCHD_CACHE = 'System/Library/xpc/launchd.plist'


def disabled_job(data, label, job_path):
    """PLIST bytes (a job plist, or launchd's cache keyed by '/' + job_path) with that job's Disabled set to true, in the
    file's own format. Apple's value is a feature-flag condition; it becomes the plain boolean. A missing job stops."""
    fmt = plistlib.FMT_BINARY if data.startswith(b'bplist') else plistlib.FMT_XML
    root = plistlib.loads(data)
    job = root['LaunchDaemons']['/' + job_path] if 'LaunchDaemons' in root else root
    if job.get('Label') != label:
        raise SystemExit('disable %s: found %r at %s' % (label, job.get('Label'), job_path))
    job['Disabled'] = True
    return plistlib.dumps(root, fmt=fmt, sort_keys=False)


def disable_jobs(system):
    done = {}
    for label, job_path in DISABLED_JOBS.items():
        for rel in (job_path, LAUNCHD_CACHE):
            target = system/rel
            data = disabled_job(target.read_bytes(), label, job_path)
            run('chflags', 'noschg,nouchg,norestricted', target)
            target.write_bytes(data)
            os.chown(target, 0, 0)
            os.chmod(target, 0o644)
            run('chflags', 'restricted', target)
            done.setdefault(label, []).append(rel)
            print('Disabled job:', label, 'in', rel, flush=True)
    return done


CPU_X86_64 = 0x01000007


def load_display_installer():
    # An explicit source input also works in flattened guest hooks and in sets
    # without the optional WindowServer filter patch. Its row pins the helper.
    path = Path(__file__).resolve().parent/'inputs/display_install_helper'
    loader = importlib.machinery.SourceFileLoader('display_install_helper', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def embedded_info_plist(path):
    data = path.read_bytes()
    found = {data[o:o+n] for o, n in section_ranges(data, b'__TEXT', b'__info_plist').values()}
    if len(found) != 1:
        raise SystemExit(f'{path}: expected one embedded Info.plist, found {len(found)}')
    return found.pop()


def replace_info_plist(kept, golden):
    """Write the 27.0 program's embedded Info.plist, without the whitespace between tags, into each slice of the
    kept program; the section cannot grow, so a plist that does not fit stops the build."""
    plist = re.sub(rb'>\s+<', b'><', plistlib.dumps(plistlib.loads(embedded_info_plist(golden)), sort_keys=False))
    data = bytearray(kept.read_bytes())
    sections = section_ranges(data, b'__TEXT', b'__info_plist')
    if not sections:
        raise SystemExit(f'{kept}: no embedded Info.plist')
    for offset, size in sections.values():
        if len(plist) > size:
            raise SystemExit(f'{kept}: 27.0 Info.plist is {len(plist)} bytes, the section holds {size}')
        data[offset:offset+size] = plist + b'\n'*(size-len(plist))
    kept.write_bytes(data)
    return len(plist)


def resign_with_entitlements(kept, golden, workspace, dylibs=()):
    run('chflags', 'noschg,nouchg,norestricted', kept)
    replace_info_plist(kept, golden)
    if dylibs:
        data = bytearray(kept.read_bytes())
        for dylib in dylibs:
            add_load_dylib(data, ('/'+dylib).encode(), CPU_X86_64)
        kept.write_bytes(data)
    entitlements = workspace/(kept.name+'.entitlements.plist')
    entitlements.write_bytes(run('codesign', '-d', '--entitlements', '-', '--xml', golden, output=True))
    plistlib.loads(entitlements.read_bytes())   # a 27.0 program without entitlements would be a recipe error
    details = subprocess.run(['codesign', '-dv', str(golden)], check=True, capture_output=True, text=True).stderr
    identifier = next(l.split('=', 1)[1] for l in details.splitlines() if l.startswith('Identifier='))
    metadata = kept.stat()
    run('codesign', '-f', '-s', '-', '-i', identifier, '--entitlements', entitlements, kept)
    os.chflags(kept, metadata.st_flags)
    return identifier


def install_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    os.chown(target, 0, 0)
    os.chmod(target, 0o755)


# airportd SIGBUS without this: OCLP's Modern Wireless wrapper (PatcherSupportPkg 13.7.2-25 IO80211, kept as
# an OCLP path) exports 7 functions 27.0's callers use as 8-byte data (a pointer to a CFString of the name), so a call
# from WiFiPolicy (-[WiFiUsageLinkSession eventDictionary:] -> apple80211_authtype_uppertoStr) runs the wrapper's
# __DATA, and it lacks Apple80211Get20MhzChannelIndexBitmap, which WiFiAnalytics binds. The compat library (row
# io80211_compat, made and checked against the build's clients by src/io80211/make) defines the 8 as the build's
# x86_64 IO80211 does and re-exports the wrapper, renamed IO80211OCLP.dylib, so every other name resolves as before
# and the wrapper's initializers still run (it re-exports IO80211Old.dylib itself).
IO80211 = 'System/Library/PrivateFrameworks/IO80211.framework/Versions/A/'
# coreaudiod, AirPlayXPCHelper, ControlCenter and sharingd abort in libobjc without this: OCLP's Modern Wireless
# WiFiPeerToPeer wrapper (13.7.2-25) lacks 36 Wi-Fi Aware classes 27.x's x86_64 cache binds, so dyld sets them to
# 0xbad4007 and loading APTransport's category on WiFiAwareDatapathInfo is fatal. The compat library (row
# wifip2p_compat, made and checked by src/wifip2p/make) defines them and re-exports the wrapper, renamed
# WiFiPeerToPeerOCLP.dylib, in the same way.
WIFIP2P = 'System/Library/PrivateFrameworks/WiFiPeerToPeer.framework/Versions/A/'
# airportd crashes on auto-join without this: the image's airportd is Tahoe's (27.x has no Intel one) and hosts
# CoreWiFi's XPC server, but 27.x's CoreWiFi changed three callout reply blocks (objc_retain(0x2)) and renumbered 213
# of its 302 request types. The native Tahoe donors put Tahoe 25G342's own CoreWiFi (cut from its shared cache by
# tools/tahoe-donors) at the framework path; the compat library (row corewifi_compat, made and checked by
# src/corewifi/make) re-exports it, renamed CoreWiFiTahoe.dylib, and adds the 2 symbols and 16 methods 27.x clients
# need beyond it, so airportd and every client share Tahoe's CoreWiFi.
COREWIFI = 'System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/'


def install_io80211(system, compat, workspace):
    install_wrapper_compat(system, compat, workspace, IO80211, 'IO80211')


def install_wifip2p(system, compat, workspace):
    install_wrapper_compat(system, compat, workspace, WIFIP2P, 'WiFiPeerToPeer')


def install_corewifi(system, compat, workspace):
    """The compat library in front of Tahoe's CoreWiFi, when the native donors supplied it (tahoe-donors recipe
    cache_images). Without it (an older donor artifact, e.g. the 27-first one of dev-golden-bootstrap) 27.x's cached
    CoreWiFi stays, and the record says so: Tahoe's airportd then crashes on auto-join."""
    tahoe = system/COREWIFI/'CoreWiFi'
    if not tahoe.is_file() or tahoe.is_symlink():
        print('CoreWiFi: NOT INSTALLED: the native donors supplied no Tahoe CoreWiFi; the cached 27.x CoreWiFi stays',
              flush=True)
        return 'not installed: no Tahoe CoreWiFi from the native donors (27.x cached CoreWiFi kept)'
    install_wrapper_compat(system, compat, workspace, COREWIFI, 'CoreWiFi', 'Tahoe')
    return 'Tahoe CoreWiFi (native donor) as CoreWiFiTahoe.dylib, under the compat library'


def install_wrapper_compat(system, compat, workspace, folder, leaf, suffix='OCLP'):
    """Rename the library SYSTEM/FOLDER/LEAF (OCLP's wrapper, or a Tahoe donor) to LEAF+SUFFIX.dylib (install name and
    file, re-signed with its identifier) and install COMPAT, which re-exports it, as LEAF."""
    wrapper, renamed = system/folder/leaf, system/folder/f'{leaf}{suffix}.dylib'
    if not wrapper.is_file() or wrapper.is_symlink():
        raise SystemExit(f'{wrapper}: no {leaf} library to put the compat library in front of')
    data = bytearray(wrapper.read_bytes())
    set_install_name(data, ('/'+folder+leaf).encode(), ('/'+folder+renamed.name).encode())
    details = subprocess.run(['codesign', '-dv', str(wrapper)], check=True, capture_output=True, text=True).stderr
    identifier = next(l.split('=', 1)[1] for l in details.splitlines() if l.startswith('Identifier='))
    staged = workspace/renamed.name
    staged.write_bytes(data)
    run('codesign', '-f', '-s', '-', '-i', identifier, '--preserve-metadata=entitlements,flags,runtime', staged)
    flags = wrapper.stat().st_flags
    run('chflags', 'noschg,nouchg,norestricted', wrapper)
    for source, target in ((staged, renamed), (compat, wrapper)):
        install_file(source, target)
        os.chflags(target, flags)
    print(f'{leaf}: compat library re-exports the original as', renamed.name, 'identifier', identifier, flush=True)


def install_cryptex(payload, preboot, group, golden_os, rosetta, workspace, driverkit_artifact, donor_tool,
                    codeid_carrier, hao, cache=None):
    """Install the combined OS cryptex and the App cryptex (cryptex.py builds them). With CACHE (cryptex_cache.Cache)
    an image pair built earlier from the same inputs, code and builder tools is copied instead, checked against its
    sha256; a new build is stored for the next one."""
    current = preboot/group/'cryptex1/current'
    os_work, app_work = current/'.golden-gate-os.dmg', current/'.golden-gate-app.dmg'
    reused = cache.restore(os_work, app_work, payload) if cache else None
    if reused:
        counts, app = reused
    else:
        counts = build_os_image(os_work, golden_os, rosetta, workspace, driverkit_artifact, donor_tool,
                                codeid_carrier, hao)
        app = build_app_image(payload, app_work, workspace, hao)
        if cache:
            cache.store(os_work, app_work, counts, app)
    print('Combined cryptex:', json.dumps(counts), flush=True)
    remove(current/'os.clone.dmg')
    remove(current/'os.dmg')
    os.replace(os_work, current/'os.dmg')
    run('cp', '-c', current/'os.dmg', current/'os.clone.dmg')
    run('sh', Path(__file__).with_name('cryptex-cache-alias.sh'), preboot)
    counts['rosetta_cache_alias'] = 'Cryptexes/Rosetta/System/Library/dyld -> ../../../OS/System/Library/dyld'
    remove(current/'app.dmg')
    os.replace(app_work, current/'app.dmg')
    counts['app'] = app
    return counts


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('system', 'payload', 'shim', 'io80211', 'wifip2p', 'corewifi'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--driverkit-artifact', type=Path, required=True,
                        help='tahoe_driverkit_donors artifact (x86_64 DriverKit cache for the cryptex)')
    parser.add_argument('--donor-tool', type=Path, required=True, help='tools/tahoe-donors/make')
    parser.add_argument('--hao-runtime', type=Path, required=True,
                        help='hao_runtime payload (its stubs for programs in the App and OS cryptexes)')
    parser.add_argument('--cryptex-cache', type=Path,
                        help='the build share\'s cryptex cache folder (golden-run): reuse or store the sealed images')
    return parser.parse_args(argv)


def main():
    args = arguments()
    system, payload, shim, io80211, wifip2p = args.system, args.payload, args.shim, args.io80211, args.wifip2p
    display_installer = load_display_installer()
    group, preboot_device = os.environ['GRP'], os.environ['PBU']
    metadata = json.loads((payload/'userspace.json').read_text())
    # Never recursively clean mountpoints after a failed unmount: Preboot is
    # writable and a TemporaryDirectory cleanup would walk its still-mounted files.
    workspace = Path(tempfile.mkdtemp(prefix='golden-gate-'))
    cache = cryptex_cache.Cache(args.cryptex_cache) if args.cryptex_cache else None
    with ExitStack() as stack:
        source = stack.enter_context(image_mount(payload/'filesystem.dmg', workspace/'filesystem'))
        golden_os = stack.enter_context(image_mount(payload/'os.dmg', workspace/'golden-os'))
        rosetta = stack.enter_context(image_mount(payload/'rosetta.dmg', workspace/'rosetta'))
        preboot = stack.enter_context(volume_mount(preboot_device, workspace/'preboot'))
        disabled = disable_jobs(system)
        display_installer.install_library(system, shim, install_file)
        install_io80211(system, io80211, workspace)
        install_wifip2p(system, wifip2p, workspace)
        corewifi = install_corewifi(system, args.corewifi, workspace)
        display_installer.resign_clients(system, source, workspace, RESIGN_WITH_27_ENTITLEMENTS,
                                        resign_with_entitlements)
        counts = install_cryptex(payload, preboot, group, golden_os, rosetta, workspace,
                                 driverkit_artifact=args.driverkit_artifact, donor_tool=args.donor_tool,
                                 codeid_carrier=Path(__file__).resolve().parent/'inputs/codeid_carrier',
                                 hao=args.hao_runtime, cache=cache)
        version = system/'System/Library/CoreServices/SystemVersion.plist'
        for rel in ('System/Library/CoreServices/SystemVersion.plist',
                    'boot/System/Library/CoreServices/SystemVersion.plist',
                    'cryptex1/current/SystemVersion.plist'):
            target = preboot/group/rel
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            remove(target)
            run('ditto', version, target)
        metadata.update(installation_order="golden-first", disabled_jobs=disabled, corewifi=corewifi,
                        cryptex=counts,
                        boot_metadata='Tahoe Intel bootloader, collection layout and cryptex tickets retained',
                        validation='Built only; full Golden Gate boot and desktop untested')
        record = system/'System/Library/CoreServices/golden-gate-userspace.json'
        # Golden Gate's System supplies CoreServices; create it explicitly (0755) if it ever does not.
        record.parent.mkdir(mode=0o755, exist_ok=True)
        record.write_text(json.dumps(metadata, indent=2)+'\n')
        os.chmod(record, 0o644)
        print('Installed userspace:', plistlib.loads(version.read_bytes()), flush=True)
    shutil.rmtree(workspace)


if __name__ == '__main__':
    main()
