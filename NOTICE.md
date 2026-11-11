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
| Metal fix sources | See NOTICE | [tools/metal-fixes/NOTICE.md](tools/metal-fixes/NOTICE.md) |
| Display fix sources | See NOTICE | [tools/display-fixes/NOTICE.md](tools/display-fixes/NOTICE.md) |
| Audio fix sources | See NOTICE | [tools/audiomxd-fix/NOTICE.md](tools/audiomxd-fix/NOTICE.md) |
| CoreSpotlight adapter sources | See NOTICE | [patches/golden-gate-corespotlight-compat/build/NOTICE.md](patches/golden-gate-corespotlight-compat/build/NOTICE.md) |

This product includes software developed by Dortania and the OpenCore Legacy
Patcher contributors.

Upstream commits of the kernel-fix sources are in
[tools/kernel-fixes/vendor/provenance.json](tools/kernel-fixes/vendor/provenance.json). `boot-efi.zip` carries
the OpenCorePkg and OCLP license texts in `LICENSES/`.

Apple binaries, installers, disk images and generated caches are never part of this
repository. You supply them as build inputs, and the build writes its outputs to
your machine.
