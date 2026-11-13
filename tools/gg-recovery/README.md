# gg-recovery

Experiments toward a macOS Recovery that is Golden Gate's own (27.2.1, 26B131) BaseSystem, booted on the
MacBookPro9,2 with this repo's Darwin 27 x86_64 kernel, Tahoe Intel kexts and hao. Not part of any image set; the
image's Recovery is still the Tahoe one from `pipeline/recovery`.

## Stage A0: a shell in QEMU

`a0 build` makes, in an isolated builder VM clone (the builder lock is taken as `pipeline/golden-run` takes it),
a BaseSystem in the Tahoe BaseSystem's container whose "macOS Base System" volume is the 26B131 tree with:

| From the main image (`--system-image`, read-only) | Where |
|---|---|
| every Mach-O whose slices all equal slices of the image's file at the same path (universal or hao-embedded) | same path |
| `usr/lib/dyld`, `usr/libexec/hao`, `usr/lib/hao` without `aot` | same path |
| `/System/Library/Extensions` | replaces the BaseSystem's arm64e kexts |
| BootKernelExtensions.kc (+ `.elides`), SystemKernelExtensions.kc (also as BaseSystemKernelExtensions.kc) | `/System/Library/KernelCollections`; the BootKC also in Preboot |
| Tahoe `boot.efi`, PlatformSupport.plist | CoreServices of both volumes |
| the repaired x86_64 shared cache (OS cryptex) and x86_64 DriverKit cache | `/System/Library/dyld`, beside the BaseSystem's own arm64e cache |

Two more changes make it boot from a disk:

- hao runs an arm64 program only if the kernel registered its signature (the x86_64 kernel takes only 4 KiB pages)
  or its cdhash is in `usr/lib/hao/trusted-cdhashes`; the main image's list has no BaseSystem-only programs
  (`rc.temporaryDataVolume` was refused and the boot task panicked). Every arm64/arm64e slice the overlay left that
  satisfies `anchor apple` (the builder's `codesign`, bundle resources not checked) is appended in the list's format.
- `rc.temporaryDataVolume` copies `System/Library/Templates/Data` onto a tmpfs Data volume. Writing the
  `com.apple.rootless` xattr and `SF_RESTRICTED` there is refused unless the booter set `kBootArgsFlagCSRBoot`
  (a Recovery boot); `csr-active-config` 0xfef does not lift it, and the failed copy panics the boot
  (`restore-datapartition`). The template loses those xattrs and flags; the RAM Data volume keeps no protected state.

Plus a marker job (`src/access/marker.zsh`: `GG-A0 ...` lines on the serial console every 60 s) and root SSH by the
dev key on port 22 (`src/access`, rows `test_host_key`, `test_authorized_key`: dev only). Inputs: rows
`golden_gate_basesystem` (golden/26B131), `unpack` (25G342, its `BaseSystem-x86_64.dmg`), `python_arm64`.

```sh
python3.14 -I tools/gg-recovery/a0 build                    # -> store/recovery/gg-a0-STAMP
python3.14 -I tools/gg-recovery/a0 esp OUT/esp              # row qemu_esp_hao with DmgLoading Any
python3.14 -I tools/qemu-vm/qemu-vm start --image OUT/disk.img --esp OUT/esp --memory 24G --name gg-a0
```

`disk.img` is the container as a raw disk (OpenCore boots the volume from Preboot, no RAM disk); `dmgdisk.img` is
an HFS+ disk holding `com.apple.recovery.boot/BaseSystem.dmg` for OpenCore's DmgLoading path. With the full x86_64
cache the dmg is 10.7 GiB, and OpenCore 1.0.5 refuses it (`OCB: LoadImage failed - Unsupported`): the RAM-disk path
needs a dmg under 4 GiB (stage A1).

QEMU 2026-11-13, build gg-a0-20261113-041548: `disk.img` booted the Darwin 27 kernel to translated launchd ("BaseSystem
environment starting"), the Data volume, the marker (`GG-A0 zsh 5.9 ... build 27.2.1 (26B131) basesystem yes`) and
root SSH (`sw_vers` 26B131, `kern.hao.routed` 20). WindowServer exits (stage B).

## Verify
```sh
python3.14 -I -B -m unittest discover -s tools/gg-recovery/tests
```
Host fixtures check the Mach-O slice match, the OpenCore changes and the guest job. A build and a QEMU boot are
separate checks.
