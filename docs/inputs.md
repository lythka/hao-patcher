# Build inputs

This repository contains no Apple software or generated binaries. You download the
Apple and third-party archives yourself, build the prepared inputs from them, and
record each file's SHA256 in an input row.

## Downloads

The current build is macOS 27.2.1 (26B131) with Tahoe 26.7.3 (25G342) as the donor
(`pipeline/assemblies/mbp92-26B131.json`). Checksums are the ones in the input rows
(`pipeline/inputs/golden/26B131/`, `pipeline/inputs/25G342/`).

| Input | Version and source | SHA256 |
|---|---|---|
| Golden Gate IPSW | macOS **27.2.1 (26B131)**, `UniversalMac_27.2.1_26B131_Restore.ipsw` from Apple | `233e85a09887579d47d0b195e7db72be7f204261059c6946f719acf6b300814f` |
| Tahoe installer | macOS **26.7.3 (25G342)**, `InstallAssistant.pkg` from Apple's software update catalog (product `142-54128`) | `3d9551b7a18c1fa2f86e7002a132ec3db34d7915fa842cb1e890480b51be4ef3` |
| Tahoe Kernel Debug Kit | **26.7.3 build 25G342**, [Kernel_Debug_Kit_26.7.3_build_25G342.dmg](https://github.com/dortania/KdkSupportPkg/releases/download/25G342/Kernel_Debug_Kit_26.7.3_build_25G342.dmg) from Dortania's [KdkSupportPkg](https://github.com/dortania/KdkSupportPkg) mirror, or from [Apple Developer downloads](https://developer.apple.com/download/all/?q=Kernel%20Debug%20Kit) (Apple Account required) | `219f27c7e039b6ab3426496e0d5852d55a31d00b3d3984d855cba04377c5dba0` |
| OCLP support files | [PatcherSupportPkg **2.0.5** Universal-Binaries.dmg](https://github.com/dortania/PatcherSupportPkg/releases/download/2.0.5/Universal-Binaries.dmg), matching [OCLP **3.0.0-rc.5**](https://github.com/dortania/OpenCore-Legacy-Patcher/releases/tag/3.0.0-rc.5) | `1c290781bc726759ff37a110ffcb7182a6c6fd3d8ebce112d134d482c8de52a3` |
| OpenCore | [OpenCore **1.0.5**](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.5) `OpenCore-1.0.5-RELEASE.zip` (images; nothing on screen at boot), and `OpenCore-1.0.5-DEBUG.zip` for diagnosis (assembly `"opencore": "debug"`) | RELEASE `2608743afe81001114a85b6fba6555edbc43813b82538e1f70220aeb0662d8be`, DEBUG `db0720000455502b34ad5a0fc53114f7cd6d388bc6af7636e8a530276a6bf0a1` |
| hao-runtime payload | Built from [hao-runtime](https://github.com/lythka/hao-runtime) 273ba98; see [hao-runtime payload](#hao-runtime-payload) | directory row; payloads with `payload-manifest.json` are checked file by file |

Check each download with `shasum -a 256 FILE`. Use these exact versions: other
builds can have the same file names but different contents. The builder extracts
the IPSW; it is never restored onto the Intel Mac. No Golden Gate KDK is needed.

The older 27.0 build (`mbp92-golden-first.json`, no longer maintained) used the
26A428 IPSW (`2a5d3c695d501022b7fad9adaffcf2627bcb867d993fb5662dcd41bac99a2836`), the
26.7.2 (25G332) installer (`e7d52c10b58b199ea0060e14ad5c74417514d3a2b19cb9ca39422bc53103196b`)
and the 25G330 KDK (`621eb51de4e5b48f1e5db0708297f8b9491a4cd171a4d55c4506f525fce86ab1`);
their rows are in `pipeline/inputs/25G332/`.

## hao-runtime payload

The `hao_runtime` row (`pipeline/inputs/golden/26B131/hao_runtime`) names a payload
directory under `store/artifacts/`. A release download of the payload is planned; until
then, build it from [hao-runtime](https://github.com/lythka/hao-runtime) at the commit the
row's comment names (`273ba98` today) on the same Apple silicon host:

```sh
git clone https://github.com/lythka/hao-runtime.git && cd hao-runtime
git checkout 273ba98
make
tools/hao-image-payload [OPTIONS] OUT ROOT
```

`ROOT` is the 26B131 System root (a read-only mount of the IPSW's filesystem image, with
its App and OS cryptexes, whose programs the image also embeds). The options (interface
units, AOT list, x86_64 shared cache) are described at the top of `tools/hao-image-payload`
and in hao-runtime's README ("Installing into a system"); the payload's `build.json`
records the ones used. Put `OUT` at the row's path, or edit the row's path to point at it.

## Third-party dependencies and EFI

End users supply the three Apple archives: Tahoe installer, Golden Gate IPSW and
Tahoe KDK. OCLP/OpenCore and versioned hao packages are software dependencies;
contract discovery and ARM reference tests belong to their release process.

The pinned OCLP source comes from Dortania's repository at commit
`e2107e05d9ce574e9ac3fd8b223e20a2edb7ba53`, with the integration patch reproduced
by `tools/oclp-integration/make`. Its `payloads/` supplies model configuration,
ACPI, selected kext archives and RSRRepair. `Universal-Binaries.dmg` comes from
the separately pinned PatcherSupportPkg release above. OpenCore's core,
bootstrap and matching drivers come from the selected Acidanthera release archive.
The OCLP application, GUI and updater are not installed or run by this build.

`pipeline/stages/build-efi.py` invokes only OCLP's model configuration builder,
using explicit target settings and an external model. It does not probe the
build host or read its OCLP preferences. The Golden Gate runner now builds this
base EFI instead of consuming the historical cached ESP archive. The old `esp`
target row remains for the retired Tahoe/Recovery paths and historical inputs.
The build record includes `base-efi.json` with source/version/hash provenance.

After image-specific repairs are applied, the full build publishes a matching
`boot-efi.zip` for the user's final EFI installation step. See [Building an
image](building.md). A stock EFI regenerated by OCLP does not contain our additions.

The current runner still uses prepared input rows and an ARM builder VM. Automatic
three-archive preparation and an Intel VM backend remain separate implementation
work; the dependency split above does not claim those paths are complete.

## Prepared inputs

| Input rows | Built by |
|---|---|
| `golden_gate_userspace`, `darwin27_kernel` | [`patches/golden-gate-userspace/build`](../patches/golden-gate-userspace/build/README.md) and [`tools/kernel-foundation`](../tools/kernel-foundation/README.md) from the 26B131 IPSW |
| `tahoe_native_donors`, `tahoe_extensions_donors`, `tahoe_boot_donors`, `tahoe_driverkit_donors`, `tahoe_recovery_donors` | [`tools/tahoe-donors`](../tools/tahoe-donors/README.md) from the 25G342 installer and KDK |
| `oclp_hardware` | [`tools/oclp-hardware`](../tools/oclp-hardware/README.md) from PatcherSupportPkg 2.0.5 and OCLP's `RSRRepair` |
| OCLP integration source | [`tools/oclp-integration`](../tools/oclp-integration/README.md) from the declared upstream OCLP revision |
| Kernel, Metal, display, CoreSpotlight and audio fixes | Each fix's builder; see the [fix table](progress.md#fixes) |
| `hao_runtime` | A [hao-runtime](https://github.com/lythka/hao-runtime) image payload |
| Builder helpers | Python 3.14 on the host; the arm64 Python archive and helper tools named in `pipeline/inputs/common/` for the builder VM |

A few rows feed only the fix builders and QEMU tests, not the image itself:
`darwin27_boot_kc`, `sandbox_op_layout_26A428`, `tahoe_amfi_25G330`,
`tahoe_sandbox_25G330`, `qemu_reference_efi` and `qemu_esp_hao`. Their READMEs
in `tools/` describe them.

`pipeline/patches.py plan` lists every row a patch set needs (see
[Building an image](building.md#1-prepare-the-inputs)). The assembly recipe
additionally names the five donor rows and `oclp_hardware`.

## Tahoe donors

Golden Gate ships no x86_64 kernel extensions, so the Intel parts of the system
come from Tahoe:

| Source | Provides |
|---|---|
| Tahoe Kernel Debug Kit | Intel kernel-extension binaries, overlaid on the installer's extension tree |
| Tahoe installer | Extension resources, DriverKit runtime and extensions, HID and other native plugins, selected executables and graphics framework bases |
| Tahoe SystemOS | The generic x86_64 DriverKit dyld cache; CoreWiFi, cut out of its x86_64h OS cache (`cache_images`) |
| Tahoe boot and Recovery | Intel boot files, BaseSystem and Recovery resources |
| OCLP Universal-Binaries | Legacy graphics, wireless and audio drivers for this model |

The KDK alone is not enough. It lacks about 1,600 files of the installer's
extension tree, as well as DriverKit, the userspace executables and Recovery.
Ivy Bridge needs the generic x86_64 DriverKit cache (an x86_64h-only cache is
not enough), and the Tahoe framework bases must be installed before the OCLP
overlays.

## Input rows

Each file under `pipeline/inputs/` (`golden/26B131/`, `25G342/`, `common/`, ...) is one
input row: optional `#` comment lines, then `SHA256 PATH` (or `- PATH` for a
directory). A directory row may add `# check: payload os_build=B layout_version=N
[manifest_sha256=H]`: the folder is then a hao-runtime payload, and every read checks its
`payload-manifest.json` with the vendored [hao-assets](https://github.com/lythka/hao-assets) checker
(clean build, OS build, install-layout version, every file's sha256). A row is looked up first in `pipeline/inputs/golden/<golden_build>/`
(rows made from that Golden Gate build: its IPSW, kernel, userspace and the fixes
derived from them), then in the donor build's folder (`25G342`, named by the
assembly's `donor_build`), then in `common/`. Rows not yet moved to a
`golden/` folder belong to 26A428.

The checked-in rows give the expected hash and a path under `store/`. Put your
files at those paths, or edit the path; never change a hash to make a different
file fit. A hash is checked whenever a row is read.

```sh
python3.14 -I pipeline/lock.py path pipeline/inputs/25G342 kdk
```
