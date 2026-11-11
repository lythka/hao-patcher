# hao-opencore

Configures the image's OpenCore so the MacBookPro9,2 boots the Darwin 27.0 x86_64 kernel: the kernel repairs and
IntelCommpage that let this kernel start on Ivy Bridge hardware, `HaoExec` for arm64-only programs, early Tahoe
display and Ethernet drivers, OpenCore DEBUG file logging and the boot arguments below.

## How it works

`apply.sh` edits the image's OCLP ESP (`$ESP/EFI/OC`):

- OpenCore.efi, OpenRuntime.efi and every other OpenCore binary are already the official OpenCore 1.0.5 DEBUG
  release (the donor's version) when this runs: `pipeline/stages/build-efi.py` replaces all of them in OCLP's
  generated EFI from the `opencore_debug` archive. The DEBUG build makes the ESP log record each kernel patch result.
  `Misc.Debug.Target` keeps logging (0x01) and the file log (0x40) and clears on-screen output (0x02), which OCLP's
  base config enables.
- `src/repair_binding.py` binds the recorded kernel repairs (early VM range initialization, ascending early
  kernel-map bounds, bootstrap physical-page aliases) to this image's BootKC. The repairs are RIP-relative code
  tied to one collection layout, and kmutil gives every build a new enclosing UUID and build paths. The binder
  therefore verifies every fileset member's UUID, load commands, segment map and segment hashes against the
  reference profile, checks every recorded Find pattern and its context, and writes a per-image artifact whose
  `build.json` is kept with the build log as `kernel-repair-binding.json`.
- `src/repairs.py` installs exactly those repairs and the `IntelCommpage.kext` Kernel.Add entry, and refuses any
  BootKC whose full SHA-256 differs from the bound artifact. Startup-trace patches are dropped (VM only).
- `HaoExec.kext` comes from the `hao_runtime` payload and is stamped by the payload's `stamp_identity.py` with this
  image's BootKC identity. It is a KEC (`com.apple.kec.HaoExec`), starts before PID 1 and stays inactive on any
  other kernel. Executables with an arm64 slice and no x86_64 slice are sent to XNU's `#!` activator with
  `/usr/libexec/hao`. OS executables never reach it, because [`hao-runtime`](../hao-runtime/README.md) gives them
  an x86_64 slice. The `kern.hao.*` sysctls appear at the first such exec. hao's source is
  [hao-runtime](https://github.com/lythka/hao-runtime); this repository consumes only its built payload.
- `src/early_kexts.py` injects the Tahoe display and wired-network drivers listed in `src/early-kexts.txt` (Capri
  framebuffer, HD 4000, BCM5701 Ethernet, the same set as [`rec-display-ethernet`](../rec-display-ethernet/README.md))
  and their dependencies, in OSBundleLibraries order, only when neither the Boot nor the System collection has them.
  Drivers already in the System collection stay there: injecting a second copy makes the kernel discard the
  collection's copy while other drivers still call into it.
- The next OpenCore boot deletes the Apple boot-GUID `bootercfg` and `bootercfg-once` variables (firmware
  logging overrides independent of kernel boot-args); see the
  [OpenCore configuration reference](https://dortania.github.io/docs/latest/Configuration.html).

## Boot arguments

| Argument | Why |
|---|---|
| `-lilubetaall` | Lets Lilu-based OCLP kexts load on an unknown kernel version. |
| `-pmap_pcid_disable` | Disables PCID in the target kernel's pmap; part of the boot configuration known to work. |
| `amfi_get_out_of_my_way=1` | `hao-runtime` signs each program's x86_64 slice ad hoc with its own entitlements, including Apple-private ones (launchd: Preboot, graftdmg, trust caches). With AMFIPass alone, AMFI rejects `/sbin/launchd` and the kernel panics. |
| `hao_crash=1` | hao's crash lines on the console. |
| `hao_nx_stats=1 hao_bridge_stats=1` | Dev builds only (`IMAGE_DEV_BUILD=1`, set by golden-run when the set has a status `dev` recipe such as `dev-access`): hao's NX fault tables and bridge statistics. They cost time on every translated native call. |
| `ic_csr_internal=1` | IntelCommpage reports `CSR_ALLOW_APPLE_INTERNAL`. 27.0's x86_64 dyld scans the disk for shared-cache overrides only when `csr_check(0x10)` succeeds, and the booter drops that bit from NVRAM; without it OCLP's on-disk Metal and MetalOld never load. |

`-v`, `startup_debug=1` and `debug=0x100` are removed if the base config has them: early-boot evidence comes from
the ESP log and the unified log, and without `debug=0x100` a panic reboots and leaves `aapl,panic-info` in NVRAM
instead of waiting for a debugger.

This patch has status `experiment` because of `amfi_get_out_of_my_way=1`; it is never part of a shipped set.

## Inputs

- `hao_runtime`: the unstamped `HaoExec.kext` and `stamp_identity.py` from a hao-runtime payload (its `build.json`
  records the hao-runtime commit).
- `kernel_repair_binding`: the reference profile made by `kernel-fixes repair-binding profile`
  ([tools/kernel-fixes](../../tools/kernel-fixes/README.md)).
- `kernel_repair_efi`: the three repairs and IntelCommpage, from `tools/kernel-foundation` `make vm-esp`
  ([tools/kernel-foundation](../../tools/kernel-foundation/README.md)).

## Verify

Host tests: `python3.14 -I -m unittest discover -s patches/hao-opencore/tests`.

On the built ESP, `plutil -lint` passes and Kernel.Add has `IntelCommpage.kext` and `HaoExec.kext`. After a boot,
the ESP's OpenCore log shows each repair and both kexts applied once. On the running system:
`kmutil showloaded | grep -iE 'hao|commpage|Capri|HD4000|BCM5701'`, `sysctl kern.hao` after the first arm64-only
exec, and the boot log line "HaoExec: arm64-only executables now run through /usr/libexec/hao".

### IOSurface regression probes

IntelCommpage copies IOSurface property input into private pageable memory, because Tahoe's out-of-line property
mapping requests a copy-on-write mapping of malloc memory, which Darwin 27's hardened WebContent policy forbids.
Build the x86_64 probes (no graphics run on the build host):

```sh
python3.14 patches/hao-opencore/tests/iosurface_probe.py NEW_DIRECTORY --sdk SDK_PATH
```

Copy the binaries to the target (QEMU or the MacBookPro9,2). Run `iosurface-value` and `iosurface-value-cow` (the
second carries WebContent's hardened-process and CoW containment entitlements) with byte counts `16`, `1024`,
`4096`, `8192`, `65536` and `1048576`; each must exit 0 and print `roundtrip=1`. Run `cow-guard-control` and
`cow-guard-control-cow` with `entry 0`, `entry 1`, `copy 0` and `copy 1`: only `cow-guard-control-cow entry 1`
must still die with VM guard flavor 13 (status 137), which shows the fix leaves the VM policy in place. Each probe
has a 15-second alarm. Finally load a website in Safari and scroll, navigate and reload.

## Validation
Laptop 2026-11-11, image 4d1c48c (26B131, dev build): boots with r33; r34's HaoExec.kext and stamp_identity.py are byte-identical to r33's.
r35 writes the same config.plist as r34 in a dev build (host test and a direct comparison of the two scripts), so
the result above stands for dev builds. A non-dev build (`mbp92-26B131.json`, without the two statistics
boot-args) has not been booted yet.
Earlier: Host tests pass, and the binder verifies every fileset member and repair site against the
reference profile. The IOSurface probes and the IntelCommpage SSE and Sandbox adapters pass in QEMU; a full
Golden Gate boot to the desktop on the MacBookPro9,2 is not yet proven.

## Revisions
- r36 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 5651d79 (native-call cache, JIT vector FP and LD1/ST1, scalar FP and conversions, round-to-odd FP32 FMA; u3 units + the optional-data contract). No recipe change.
- r35 2026-11-11 `hao_nx_stats=1` and `hao_bridge_stats=1` only in dev builds (`IMAGE_DEV_BUILD=1`); `hao_crash=1` stays in every build. Dev builds get r34's config.plist unchanged.
- r34 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 790720d; HaoExec and stamp_identity.py come from it. No recipe change; boot validation pending.
- r33 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 7415a0b; HaoExec comes from it (rebuilt by that commit). No recipe change; boot validation pending.
- r32 2026-11-11 No longer copies OpenCore.efi and OpenRuntime.efi from `opencore_debug`: the base EFI build already installs every OpenCore binary from that same archive. Input row `opencore_debug` dropped from this patch (the runner still reads it for the base EFI). Same ESP bytes expected; build validation pending.
- r31 2026-11-11 Use the Sandbox operation adapter that prunes isolated unsupported roots from removed operations. The previous adapter refused apsd and left Setup Assistant waiting on its activation-record request. Full boot validation pending.
- r30 2026-11-11 Bind 26B131 kernel repairs and IntelCommpage to the freshly linked collection, and stamp HaoExec for that kernel. Boot validation pending.
- r29 2026-11-10 Initial public release.
