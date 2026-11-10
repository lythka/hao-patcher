# Build inputs

This repository contains no Apple software or generated binaries. You download the
Apple and third-party archives yourself, build the prepared inputs from them, and
record each file's SHA256 in an input row.

## Downloads

| Input | Version and source | SHA256 |
|---|---|---|
| Golden Gate IPSW | macOS **27.0 (26A428)**, [UniversalMac_27.0_26A428_Restore.ipsw](https://updates.cdn-apple.com/2026FallFCS/afcfc88e-bbe6-44bf-a5da-07c56eebc06c/UniversalMac_27.0_26A428_Restore.ipsw) | `2a5d3c695d501022b7fad9adaffcf2627bcb867d993fb5662dcd41bac99a2836` |
| Tahoe installer | macOS **26.7.2 (25G332)**, [InstallAssistant.pkg](https://swcdn.apple.com/content/downloads/38/31/142-50020-A_7SHECS0ACF/ncy3h2j7nwse4aoz0rjot59fp0z9s4yf3o/InstallAssistant.pkg) (catalog product `142-50020`) | `e7d52c10b58b199ea0060e14ad5c74417514d3a2b19cb9ca39422bc53103196b` |
| Tahoe Kernel Debug Kit | **26.7.2 build 25G330**, `Kernel_Debug_Kit_26.7.2_build_25G330.dmg` from [Apple Developer downloads](https://developer.apple.com/download/all/?q=Kernel%20Debug%20Kit) (Apple Account required) | `621eb51de4e5b48f1e5db0708297f8b9491a4cd171a4d55c4506f525fce86ab1` |
| OCLP support files | [PatcherSupportPkg **2.0.5** Universal-Binaries.dmg](https://github.com/dortania/PatcherSupportPkg/releases/download/2.0.5/Universal-Binaries.dmg), matching [OCLP **3.0.0-rc.5**](https://github.com/dortania/OpenCore-Legacy-Patcher/releases/tag/3.0.0-rc.5) | `1c290781bc726759ff37a110ffcb7182a6c6fd3d8ebce112d134d482c8de52a3` |
| OpenCore | [OpenCore **1.0.5** DEBUG](https://github.com/acidanthera/OpenCorePkg/releases/tag/1.0.5), `OpenCore-1.0.5-DEBUG.zip` | `db0720000455502b34ad5a0fc53114f7cd6d388bc6af7636e8a530276a6bf0a1` |

Check each download with `shasum -a 256 FILE`. Use these exact versions: other
builds can have the same file names but different contents. The builder extracts
the IPSW; it is never restored onto the Intel Mac.

The image deliberately pairs the 25G330 Kernel Debug Kit with the 25G332 installer,
because no 25G332 KDK was available. Do not assume other KDK versions are
interchangeable. No Golden Gate KDK is needed.

## Prepared inputs

| Input rows | Built by |
|---|---|
| `golden_gate_userspace`, `darwin27_kernel` | [`patches/golden-gate-userspace/build`](../patches/golden-gate-userspace/build/README.md) and [`tools/kernel-foundation`](../tools/kernel-foundation/README.md) from the 26A428 IPSW |
| `tahoe_native_donors`, `tahoe_extensions_donors`, `tahoe_boot_donors`, `tahoe_driverkit_donors`, `tahoe_recovery_donors` | [`tools/tahoe-donors`](../tools/tahoe-donors/README.md) from the 25G332 installer |
| `oclp_hardware` | [`tools/oclp-hardware`](../tools/oclp-hardware/README.md) from PatcherSupportPkg 2.0.5 and OCLP's `RSRRepair` |
| OCLP integration source | [`tools/oclp-integration`](../tools/oclp-integration/README.md) from the declared upstream OCLP revision |
| Kernel, Metal, display, CoreSpotlight and audio fixes | Each fix's builder; see the [fix table](../README.md#fixes) |
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
| Tahoe SystemOS | The generic x86_64 DriverKit dyld cache |
| Tahoe boot and Recovery | Intel boot files, BaseSystem and Recovery resources |
| OCLP Universal-Binaries | Legacy graphics, wireless and audio drivers for this model |

The KDK alone is not enough. It lacks about 1,600 files of the installer's
extension tree, as well as DriverKit, the userspace executables and Recovery.
Ivy Bridge needs the generic x86_64 DriverKit cache (an x86_64h-only cache is
not enough), and the Tahoe framework bases must be installed before the OCLP
overlays.

## Input rows

Each file under `pipeline/inputs/25G332/` and `pipeline/inputs/common/` is one
input row: optional `#` comment lines, then `SHA256 PATH` (or `- PATH` for a
directory). The build-specific folder overrides `common/`. `25G332` names the
donor build; the installed system is still 26A428.

The checked-in rows give the expected hash and a path under `store/`. Put your
files at those paths, or edit the path; never change a hash to make a different
file fit. A hash is checked whenever a row is read.

```sh
python3.14 -I pipeline/lock.py path pipeline/inputs/25G332 kdk
```
