# opencore-mbp121

The MacBookPro12,1's part of the image's OpenCore (pack `mbp121`), beyond the config OCLP's own generator makes for
that model. There is no harvested EFI: `pipeline/stages/build-efi.py` builds the 12,1's config from the pinned OCLP
source, as for every model.

## How it works

`apply.sh` runs `src/model.py` on the EFI partition right before [hao-opencore](../hao-opencore/README.md). It refuses
an EFI generated for another model (NVRAM `OCLP-Model`), and adds:

| Setting | Why |
|---|---|
| CryptexFixup.kext (Acidanthera 1.0.5), right after Lilu | OCLP adds it only on CPUs without AVX2 (`efi_builder/firmware.py`). The image needs its APFS root-hash patch on every CPU: without it the modified OS cryptex does not graft (graftdmg 80) and the boot stops before userspace (QEMU 2026-11-17, IvyBridge). Taken from OCLP's RELEASE payload in the row `oclp_source`, byte-identical to hao-opencore's pinned copy. On an AVX2 CPU the kext patches only with the boot-arg `-crypt_force_avx`, which hao-opencore sets. |

What OCLP's generator gives the 12,1 and the 9,2 does not get (the 12,1's recorded facts):
the SPI top-case kexts (AppleHSSPISupport, AppleHSSPIHIDDriver, AppleTopCaseInjector: the internal keyboard and
trackpad), AppleCameraInterface (PCIe FaceTime camera), IOS3XeFamily (S1X/S3X SSDs), the UEFI quirk `EnableVmx`, the
booter patch `Patch SkipLogo` and its own USB maps. It leaves out the 9,2's AppleIntelCPUPowerManagement (Broadwell
uses XCPM), the USB top case, `revpatch=f16c`, `ThirdPartyDrives` and SSDT-PCI. Their reasons and kext provenance are
the target's `platform_data` (`opencore_reasons`, `kext_provenance` in `pipeline/targets/mbp121.json`), so the OS
layer names no model. `src/oclp-elements.txt` is `config_reasons.py list` of the generated config at the pinned OCLP.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/opencore-mbp121/tests`: the fixture run, every element of the
  generated config (and CryptexFixup) has a reason and every kext a provenance; with the store, the added kext is the
  pinned bytes.
- Build log: `opencore-mbp121: CryptexFixup after Lilu (OCLP leaves it out on AVX2 CPUs)`.
- QEMU with `-cpu Broadwell-noTSX`: the boot passes the OS cryptex graft (no `graftdmg` 80); the kernel log shows
  CryptexFixup forcing its patch (`forcing AVX patch upon user request`).

## Validation

NEEDS VALIDATION: host tests only; no 12,1 image is built or booted yet.

## Revisions
- r1 2026-11-17 New: CryptexFixup for the MacBookPro12,1, which OCLP leaves out on AVX2 CPUs.
