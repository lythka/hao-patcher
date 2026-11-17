"""The MacBookPro12,1's part of an image's OpenCore, on top of the config OCLP's generator made for it.

  model.py ESP OCLP_SOURCE

ESP is the image's EFI partition (EFI/OC/config.plist as pipeline/stages/build-efi.py made it for MacBookPro12,1).
OCLP_SOURCE is the row oclp_source (the pinned OCLP source tar). This adds, in place:

- CryptexFixup.kext (Acidanthera 1.0.5, OCLP's RELEASE payload), right after Lilu. OCLP adds it only for CPUs without
  AVX2 (efi_builder/firmware.py _cpu_compatibility_handling); the image needs its APFS root-hash patch on every CPU, or
  its modified OS cryptex does not graft (QEMU 2026-11-17). On an AVX2 CPU the kext patches nothing unless the
  boot-arg -crypt_force_avx is set (its 1.0.5 binary skips at "system natively support AVX2.0"); hao-opencore sets it.

Nothing else: the rest of the 12,1's OpenCore (SPI top case, PCIe camera, S3X SSD, EnableVmx, its USB map) is OCLP's
generator's, and each element's reason and kext provenance is the target's platform_data (hao-opencore reads it).
"""
import io
import plistlib
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

CRYPTEXFIXUP_ZIP = 'payloads/Kexts/Acidanthera/CryptexFixup-v1.0.5-RELEASE.zip'
CRYPTEXFIXUP_ENTRY = dict(Arch='x86_64', BundlePath='CryptexFixup.kext', Comment='CryptexFixup', Enabled=True,
                          ExecutablePath='Contents/MacOS/CryptexFixup', MaxKernel='', MinKernel='20.0.0',
                          PlistPath='Contents/Info.plist')            # OCLP's own entry


def oclp_kext(esp, oclp_tar, zip_path, bundle):
    """Put BUNDLE from OCLP's payload ZIP_PATH (in the pinned OCLP source tar) into EFI/OC/Kexts, replacing any copy."""
    with tarfile.open(oclp_tar) as tar:
        member = tar.extractfile(zip_path)
        if member is None:
            raise SystemExit('opencore-mbp121: %s is not in the OCLP source' % zip_path)
        data = member.read()
    dest = Path(esp)/'EFI/OC/Kexts'/bundle
    if dest.exists():
        shutil.rmtree(dest)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
        found = [p for p in Path(tmp).rglob(bundle) if p.is_dir() and 'dSYM' not in str(p)]
        if len(found) != 1:
            raise SystemExit('opencore-mbp121: expected one %s in the payload, found %d' % (bundle, len(found)))
        shutil.copytree(found[0], dest)


def add_cryptexfixup(config, esp, oclp_tar):
    """CryptexFixup.kext from OCLP's RELEASE payload, loaded right after Lilu (it is a Lilu plug-in)."""
    add = config['Kernel']['Add']
    names = [k['BundlePath'] for k in add]
    if 'Lilu.kext' not in names:
        raise SystemExit('opencore-mbp121: the config loads no Lilu.kext; CryptexFixup needs it')
    oclp_kext(esp, oclp_tar, CRYPTEXFIXUP_ZIP, 'CryptexFixup.kext')
    add[:] = [k for k in add if k['BundlePath'] != 'CryptexFixup.kext']
    add.insert([k['BundlePath'] for k in add].index('Lilu.kext') + 1, dict(CRYPTEXFIXUP_ENTRY))


def main(argv):
    esp, oclp_tar = Path(argv[1]), Path(argv[2])
    path = esp/'EFI/OC/config.plist'
    config = plistlib.loads(path.read_bytes())
    model = config.get('NVRAM', {}).get('Add', {}).get('4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102', {}).get('OCLP-Model')
    if model != 'MacBookPro12,1':
        raise SystemExit('opencore-mbp121: the EFI was generated for %s, not MacBookPro12,1' % model)
    add_cryptexfixup(config, esp, oclp_tar)
    path.write_bytes(plistlib.dumps(config))
    print('opencore-mbp121: CryptexFixup after Lilu (OCLP leaves it out on AVX2 CPUs)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
