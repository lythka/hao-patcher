# Notices

The MIT license in [LICENSE](LICENSE) covers the original code in this repository.
It does not relicense third-party source, Apple software, SDKs or any other input
you supply. Those keep their own terms.

## Third-party source

| Component | License | Location |
|---|---|---|
| OpenCore Legacy Patcher integration | BSD 4-Clause | [tools/oclp-integration/LICENSE.txt](tools/oclp-integration/LICENSE.txt) |
| OCLP hardware support policy | BSD 4-Clause | [tools/oclp-hardware/LICENSE.txt](tools/oclp-hardware/LICENSE.txt) |
| XNU `vm_shared_region.c` (Apple open source), kernel fixes | APSL 2.0 | [tools/kernel-fixes/vendor/XNU/APPLE_LICENSE](tools/kernel-fixes/vendor/XNU/APPLE_LICENSE) |
| MacKernelSDK headers (Acidanthera; Apple kernel headers) | APSL 2.0 | [tools/kernel-fixes/vendor/MacKernelSDK/LICENSE.txt](tools/kernel-fixes/vendor/MacKernelSDK/LICENSE.txt) |
| Lilu headers (Acidanthera) | BSD 3-Clause | [tools/kernel-fixes/vendor/Lilu/LICENSE.txt](tools/kernel-fixes/vendor/Lilu/LICENSE.txt) |
| XNU headers used by kernel preparation | APSL 2.0 | [tools/kernel-foundation/licenses](tools/kernel-foundation/licenses) |
| OpenCorePkg (Acidanthera), in the image's ESP and the exported `boot-efi.zip` | BSD 3-Clause | [pipeline/licenses/OpenCorePkg-LICENSE.txt](pipeline/licenses/OpenCorePkg-LICENSE.txt) |
| frida-gum `gumx86relocator.h` (17.25.0), shared-cache tools | wxWindows Library Licence 3.1 | [tools/shared-cache/vendor/frida-gum/COPYING](tools/shared-cache/vendor/frida-gum/COPYING) |
| Swift remote-mirror headers (caab90f), shared-cache tools | Apache 2.0 with Runtime Library Exception | [tools/shared-cache/vendor/swift-remote-mirror/LICENSE.txt](tools/shared-cache/vendor/swift-remote-mirror/LICENSE.txt) |
| Shared-cache toolchain headers (Apple open source) | APSL 2.0, Apache 2.0, BSD (see NOTICE) | [tools/shared-cache/NOTICE.md](tools/shared-cache/NOTICE.md) |
| Rich 13.9.4 (Will McGugan), the `hao` console | MIT | [tools/hao/vendor/rich/LICENSE](tools/hao/vendor/rich/LICENSE), [tools/hao/vendor/NOTICE.md](tools/hao/vendor/NOTICE.md) |
| RFC 8032 section 6 Ed25519 reference code (S. Josefsson, I. Liusvaara; Copyright (c) 2017 IETF Trust and the persons identified as the document authors), the `hao` release signature check | Simplified BSD (IETF Trust Legal Provisions 4.e) | [tools/hao/src/signing.py](tools/hao/src/signing.py), [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) |
| Metal fix sources | See NOTICE | [tools/metal-fixes/NOTICE.md](tools/metal-fixes/NOTICE.md) |
| Display fix sources | See NOTICE | [tools/display-fixes/NOTICE.md](tools/display-fixes/NOTICE.md) |
| Audio fix sources | See NOTICE | [tools/audiomxd-fix/NOTICE.md](tools/audiomxd-fix/NOTICE.md) |
| CoreSpotlight adapter sources | See NOTICE | [patches/golden-gate-corespotlight-compat/build/NOTICE.md](patches/golden-gate-corespotlight-compat/build/NOTICE.md) |

## Release builder tools

The builder uses python-build-standalone CPython 3.12.14 (release 20260924),
PSF-2.0 and the bundled dependencies' licenses, carried in the Python archives.
The release pins both upstream architecture archives and redistributes them unchanged.
LLVM 15.0.7 tools use Apache-2.0 WITH LLVM-exception; see
[their notice](tools/metal-llvm/NOTICE.md). Their license accompanies the binaries.

## Downloaded on your Mac

Fetched from their projects by `hao` while it makes the USB installer, checked against the sha256 in `release.json`,
and not redistributed by this project:

| Component | License |
|---|---|
| VirtualSMC 1.3.8 (Acidanthera), its kext in the QEMU reference EFI | BSD 3-Clause |
| Apple's HFS+ UEFI driver, as Acidanthera's OcBinaryData distributes it, in the QEMU reference EFI | Apple's terms |

This product includes software developed by Dortania and the OpenCore Legacy
Patcher contributors.

Upstream commits of the kernel-fix sources are in
[tools/kernel-fixes/vendor/provenance.json](tools/kernel-fixes/vendor/provenance.json). `boot-efi.zip` carries
the OpenCorePkg and OCLP license texts in `LICENSES/`.

Apple binaries, installers, disk images and generated caches are never part of this
repository. You supply them as build inputs, and the build writes its outputs to
your machine.
