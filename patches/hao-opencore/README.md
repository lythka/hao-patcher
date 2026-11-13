# hao-opencore

Configures the image's OpenCore so the MacBookPro9,2 boots the Darwin 27.0 x86_64 kernel: the kernel repairs and
IntelCommpage that let this kernel start on Ivy Bridge hardware, `HaoExec` for arm64-only programs, early Tahoe
display and Ethernet drivers, and the boot arguments below. It writes the end-user configuration: no OpenCore log
and no measurement switches. Bench images add those with `dev-diagnostics` (pack `dev`).

## How it works

`apply.sh` edits the image's OCLP ESP (`$ESP/EFI/OC`):

- OpenCore.efi, OpenRuntime.efi and every other OpenCore binary are already the official OpenCore 1.0.5 release
  golden-run chose (RELEASE unless the assembly says `"opencore": "debug"`) when this runs:
  `pipeline/stages/build-efi.py` replaces all of them in OCLP's generated EFI. `Misc.Debug.Target` is 0: no OpenCore
  log and nothing on screen (OCLP's base config logs to the screen). `dev-diagnostics` turns the ESP file log on in
  bench images.
- The kernel repairs (early VM range initialization, ascending early kernel-map bounds, bootstrap physical-page
  aliases) are RIP-relative code tied to one collection layout, and kmutil gives every build a new enclosing UUID
  and build paths. Two binders run on this image's BootKC:
  - `src/repair_binding.py` (the reference) binds the recorded repairs (`kernel_repair_efi`) unchanged: it verifies
    every fileset member's UUID, load commands, segment map and segment hashes against the reference profile, checks
    every recorded Find pattern and its context, and writes a per-image artifact whose `build.json` is kept with the
    build log as `kernel-repair-binding.json`. On a collection it was not recorded for it refuses, and the build goes on.
  - `src/kc_bind.py` runs [tools/kc-binder](../../tools/kc-binder/README.md) (staged into `build/` by `make.sh`),
    which computes the repairs from the BootKC itself. It refuses unless its `config.plist` is byte-identical to the
    recorded repairs bound above, every patch field and the capture match, and the profile's patch sites and
    IntelCommpage bindings hold, and the IntelCommpage address table kc-binder computes for this BootKC equals the
    one the kext holds (mode `binder`, every image today: the installed files are the row's). When
    `repair_binding.py` refused, it uses its own repairs and writes this BootKC's table into IntelCommpage's
    `__DATA,__ic_table` (mode `binder-only`; IntelCommpage reads its collection addresses from that table at load,
    see [tools/kernel-foundation](../../tools/kernel-foundation/README.md#intelcommpages-address-table)). It does
    this only if the header lines that are code facts rather than table entries (prologues, boot-args offsets,
    UUIDs) still hold. A kext from before the table (the 26A428 rows) still needs every collection-dependent line of
    `commpage_abi.hpp` to hold; otherwise it names the lines. A kernel no binder recipe covers (26A428) keeps the
    recorded repairs (mode `row`). Its record is kept as `kc-binder.json`; its `commpage_table.restamped` says
    whether the installed IntelCommpage binary differs from the row's. Like the HaoExec stamp, a restamped table is
    not re-signed.
- `src/repairs.py` installs exactly the repairs of `kc_bind.py`'s artifact and the `IntelCommpage.kext` Kernel.Add
  entry, and refuses any BootKC whose full SHA-256 differs from the bound artifact. Startup-trace patches are dropped
  (VM only).
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
- `patch.json` `files` declares the config keys the script in `apply.sh` sets to fixed values (`Misc.Debug.Target`,
  `Misc.Debug.DisplayLevel`, `boot-args`, the Delete list) as `plist.set` edits in [edits/config.json](edits/config.json),
  after the kernel repairs and the IntelCommpage Kernel.Add entry as the external op `kc.bind` (made on the target,
  after the BootKC; `tests/test_kc_bind.py` proves the bytes), and the two kext folders as whole-folder origins. HaoExec's executable is declared on its own
  ([edits/haoexec-identity.json](edits/haoexec-identity.json)): the external op `hao.stamp` (the row's
  `stamp_identity.py` against the linked BootKC), so the materialiser defers it to the target, after the BootKC; the
  stamp is not re-signed (OpenCore does not check the kext's signature). Not declared yet: the HaoExec `Kernel.Add` entry (no append
  in `plist.set`), and early Tahoe kexts (none on any recorded build). The `boot-args` value is the one without the dev-only statistics arguments. `tests/test_edits.py`
  proves the values against `apply.sh`.
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
| `auxKCAtBoot=false` | kernelmanagerd skips building an auxiliary kernel collection at boot and loads the System collection at once. 27's kmutil cannot build an x86_64 collection ("unsupported arch: x86_64 / Unsupported collection kind"), so the build of the only candidate, 27's codeless `AppleMobileDevice.kext` (from 27's Data template), failed at every boot and held the System collection, and with it the Capri framebuffer and WindowServer, back 18.5 s (23 s on the first boot). The codeless kext's personality still loads. Details: [Aux KC gate](#aux-kc-gate). |

`ic_csr_internal=1` is no longer written, and removed if present: 27's x86_64 dyld scans the disk for shared-cache
overrides (OCLP's on-disk Metal and MetalOld) only when it counts as an internal install. It used to make IntelCommpage
report `CSR_ALLOW_APPLE_INTERNAL` to every process, so os_variant made the whole system an Apple-internal build
(mobileassetd asked Apple's internal asset server: no Spotlight, Siri or language assets). Now golden-gate-dyld-internal makes dyld
answer internalInstall() itself, and `apply.sh` refuses an image whose dyld lacks that edit.

`hao_nx_stats=1 hao_bridge_stats=1 hao_diag_time=1` (hao's measurement switches) are never written here: only
`dev-diagnostics` adds them, in bench images.

`-v`, `startup_debug=1` and `debug=0x100` are removed if the base config has them: early-boot evidence comes from
the unified log (and the ESP log in bench images), and without `debug=0x100` a panic reboots and leaves `aapl,panic-info` in NVRAM
instead of waiting for a debugger.

This patch has status `experiment` because of `amfi_get_out_of_my_way=1`; it is never part of a shipped set.

## Aux KC gate

FACT (bench `bench-mbp92-26B131-tested-20261112-204015`, image 8f5ddce, both boots, log `machTimestamp` s after the
kernel): kernelmanagerd logs "performing auxiliary activation" (43.94 / 48.04), finds no
`/Library/KernelCollections/AuxiliaryKernelExtensions.kc` and asks kernelmanager_helper to build one ("Attempting to
clean up and build from approvals"). The helper is spawned for that request, starts (10 s), loads the Boot and System
collections, finds `/Library/Apple/System/Library/Extensions/AppleMobileDevice.kext` as the only candidate and fails
("building auxiliary kext collection failed ... unsupported arch: x86_64"). Only then (67.08 / 66.49) does kernelmanagerd
log "Preparing collection load into kernel"; the kernel loads the System collection at 68.03 / 67.47 and the Capri
framebuffer is published 0.3 s later.

FACT (27.2's `/usr/libexec/kernelmanagerd`, KernelManagement_executables-514.40.8, the one the image runs): when the
Aux collection does not load, kernelmanagerd reads the boot-arg `auxKCAtBoot` (lazy property `disableAuxKCAtBoot`,
true when the value is `false`) and, if set, logs "AuxKC building is disabled during boot due to boot-arg. Proceeding
without AuxKC." instead of "Attempting to clean up and build from approvals."; both branches continue at the same
code. INFERRED: codeless personalities (AppleMobileDevice) are still sent with the System collection, as now.

Not chosen: removing `AppleMobileDevice.kext` (kernelmanagerd sends the build request to the helper without looking at
the candidates, so the helper start and collection loads would likely remain, and iPhone USB devices would lose
`SupportsIPhoneOS`); a prebuilt Aux KC (kmutil refuses a collection of only codeless kexts).

## Inputs

- `hao_runtime`: the unstamped `HaoExec.kext` and `stamp_identity.py` from a hao-runtime payload (its `build.json`
  records the hao-runtime commit).
- `kernel_repair_binding`: the reference profile made by `kernel-fixes repair-binding profile`
  ([tools/kernel-fixes](../../tools/kernel-fixes/README.md)).
- `kernel_repair_efi`: the repairs (three on 26A428, four on 26B131) and IntelCommpage, from `tools/kernel-foundation`
  `make vm-esp` ([tools/kernel-foundation](../../tools/kernel-foundation/README.md)): the reference kc-binder must
  equal, and the IntelCommpage.kext installed.
- `intel_commpage_abi`: the `commpage_abi.hpp` that IntelCommpage.kext was compiled with (the binding profile pins its
  SHA-256), from the same `tools/kernel-foundation` build. On 26B131 it is r42's: every r41 line is kept, plus the
  table definitions.

`make.sh` (on the host, when the build stages this patch) copies tools/kc-binder's run-time files, its recipes and
their pins, and `tools/kernel-foundation/commpage_build.py` into `build/`, with `build/stage.json`, which `kc_bind.py`
checks before it runs them.

## Verify

Host tests: `python3.14 -I -B -m unittest discover -s patches/hao-opencore/tests`. The `HAO_OPENCORE_*` variables named
in `tests/test_edits.py` and `tests/test_kc_bind.py` add the real-input checks. `test_kc_bind.py` with
`HAO_OPENCORE_BOOT_KC` (Boot KCs) or `HAO_OPENCORE_RUNS` (recorded golden-run folders), and the
`kernel_repair_efi`, `kernel_repair_binding` and `intel_commpage_abi` rows, runs both binders as apply.sh does and
requires the same config.plist bytes; `HAO_OPENCORE_GUEST_PYTHON` runs them with the builder VM's Python.

In the build log, `kc-binder.json` (next to `kernel-repair-binding.json`) has `"mode": "binder"` and
`"restamped": false`.

After a boot with the r42 IntelCommpage (26B131), the kernel log has "IntelCommpage: macOS 26.5 source restoration
installed". It must not have "address table is not version", "address table is for another kernel" or "target
entry differs from the trampoline".

On the built ESP, `plutil -lint` passes and Kernel.Add has `IntelCommpage.kext` and `HaoExec.kext`. After a boot,
the ESP's OpenCore log shows each repair and both kexts applied once. On the running system:
`kmutil showloaded | grep -iE 'hao|commpage|Capri|HD4000|BCM5701'`, `sysctl kern.hao` after the first arm64-only
exec, and the boot log line "HaoExec: arm64-only executables now run through /usr/libexec/hao".

Aux KC gate (`auxKCAtBoot=false`): `sysctl kern.bootargs` has it; on every boot kernelmanagerd logs "AuxKC building is
disabled during boot due to boot-arg" right after "performing auxiliary activation", with no "Attempting to clean up
and build from approvals", no "couldn't construct deferred auxiliary collection" and no helper "beginning auxiliary
kext collection build"; "Preparing collection load into kernel" follows within about a second (was 18.5 s, 23 s on
the first boot), still with "Added codeless kext (force loaded) ... AppleMobileDevice.kext". Then check the next link:
kernelmanager_helper is now spawned on demand for the OpenCore kexts' load reports (Lilu first), and its start
(10 s in 8f5ddce) may hold IOKit quiet (bluetoothd "Done waiting for IOKitWaitQuiet()") and WindowServer.

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
NEEDS VALIDATION: r52 drops `ic_csr_internal=1` (host tests); needs a laptop boot proving OCLP's Metal still loads
(dyld edit) and mobileassetd uses the public asset server. r50 writes `Misc.Debug.Target` 0 and never the measurement switches (host tests); not built or
booted yet. With dev-diagnostics after it, a bench image's config.plist is unchanged (the real-config test on the
2026-11-13 bench image passes). r48 installs the r42 IntelCommpage on 26B131. It reads its collection addresses from its own table
at load, so the kext's bytes are new. The table equals r41's compiled values on all 12 recorded 26B131 KCs (host
tests), but the kext has not been booted (QEMU, then the laptop). config.plist is byte-identical to r47's. r45 binds the repairs with tools/kc-binder (host tests: byte-identical config.plist on the 26B131
bootstrap KC and nine recorded image KCs; no VM build yet). r44 adds `auxKCAtBoot=false` (not booted yet; see Verify). the hao_runtime row moved to payload 273ba98 (units u4.1) on 2026-11-12; r40 was proven on the earlier payload. The baseline image and its laptop boot re-validate it. Earlier evidence:

Laptop 2026-11-12, image d131d5b (26B131, dev build): boots with r40 (hao d9ea9bd's HaoExec and stamp_identity.py); image 7d0b612 booted with r39 on 2026-11-11.
Laptop 2026-11-11, image 4d1c48c (26B131, dev build): boots with r33; r34's HaoExec.kext and stamp_identity.py are byte-identical to r33's.
r35 writes the same config.plist as r34 in a dev build (host test and a direct comparison of the two scripts), so
the result above stands for dev builds. A non-dev build (`mbp92-26B131.json`, without the two statistics
boot-args) has not been booted yet.
Earlier: Host tests pass, and the binder verifies every fileset member and repair site against the
reference profile. The IOSurface probes and the IntelCommpage SSE and Sandbox adapters pass in QEMU; a full
Golden Gate boot to the desktop on the MacBookPro9,2 is not yet proven.

## Revisions
- r54 2026-11-13 `apply.sh` runs Python as `$OCLP_PYTHON` (the kit's CPython for the Mac the assembly runs on, arm64 or x86_64), falling back to the builder VM's share copy; same Python version and bytes written.
- r53 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 397b267 (hao-runtime-26B131-20261113-397b2675d037). No recipe change.
- r52 2026-11-13 No `ic_csr_internal=1` (removed if present): the image's dyld counts as internal itself (golden-gate-dyld-internal's internalInstall() edit), so only dyld sees an internal install and mobileassetd uses the public asset server; `apply.sh` refuses an image whose dyld lacks the edit.
- r51 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 43af852 (hao-runtime-26B131-20261113-43af85296aa9). No recipe change.
- r50 2026-11-13 End-user configuration only: `Misc.Debug.Target` 0 (no OpenCore log, no file writes at boot) and no measurement switches in any build; `IMAGE_DEV_BUILD` is gone. Bench images get `hao_nx_stats=1 hao_bridge_stats=1 hao_diag_time=1` and the ESP file log from the new `dev-diagnostics` recipe (pack `dev`). Edits `debug-log-to-file` (65) and `debug-log-info-lines` replaced by `no-opencore-log` (0).
- r49 2026-11-13 A normal boot shows only the Apple logo: `Misc.Debug.AppleDebug` false, and images use the OpenCore 1.0.5 RELEASE archive (row `opencore_release`; golden-run picks it unless the assembly says `"opencore": "debug"`). The DEBUG build printed its early lines and apfs.efi's on screen.
- r48 2026-11-12 Input rows `kernel_repair_efi`, `kernel_repair_binding` and `intel_commpage_abi` (26B131) now name r42: IntelCommpage reads its collection addresses from `__DATA,__ic_table` at load. kc_bind.py writes this BootKC's table into it in `binder-only` mode instead of refusing a moved layout, and in `binder` mode requires the table to equal the kext's own. config.plist is unchanged. The IntelCommpage binary is new.
- r47 2026-11-12 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 0a2c2aa (hao-runtime-26B131-20261112-0a2c2aad4755). No recipe change.
- r46 2026-11-12 Dev builds add `hao_diag_time=1` (hao-runtime 0a2c2aa): mach-clock stamps and phase lines on hao's console lines, and PID 1's lines from before the Data volume mounts, so a bench boot shows launchd's own startup on the Gantt's clock.
- r45 2026-11-12 Bind the kernel repairs to the freshly linked BootKC with tools/kc-binder (src/kc_bind.py, staged by make.sh): refuses unless they equal the recorded repairs repair_binding.py binds, and binds a collection those refuse when IntelCommpage's compiled addresses still hold for it. New input row `intel_commpage_abi`; the repairs are declared as `kc.bind` in edits/config.json. Same config.plist bytes on every recorded 26B131 KC.
- r44 2026-11-12 Boot-arg `auxKCAtBoot=false`: kernelmanagerd no longer tries (and fails) to build an Aux KC from the codeless AppleMobileDevice kext before loading the System KC, which cost 18.5 s per boot (23 s first boot).
- r43 2026-11-12 Declare the HaoExec identity stamp: the executable is its own `files` entry with `edits/haoexec-identity.json` (external op `hao.stamp`, made on the target after the BootKC is linked, not re-signed), so an overlay never ships the unstamped copy; apply.sh is unchanged.
- r42 2026-11-12 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 273ba98 (hao-runtime-26B131-20261112-273ba980b829). No recipe change.
- r41 2026-11-12 Declare the config keys with fixed values (edits/config.json) and the IntelCommpage and HaoExec kext folders in `patch.json` `files`; the recipe is unchanged.
- r40 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime d9ea9bd (4e68804 + the Swift value walk passes values under 8 bytes, which aborted Weather, StocksWidget, NewsTag and NewsToday2 on 7d0b612). No recipe change.
- r39 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 4e68804 (746f1ec + JIT TBL/EXT/integer and FRINT forms, native-call cache ownership fix, measured Swift value rules for the Stocks/News widgets and Weather, five review fixes); same async provider, units and AOT recipe. No recipe change.
- r38 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 746f1ec (12685dd + the native-call cache signal/fork fix) with the 26B131 async contract provider. No recipe change.
- r37 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 12685dd (5651d79 + x86-64-v2 build and a startup CPU-feature check). No recipe change.
- r36 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 5651d79 (native-call cache, JIT vector FP and LD1/ST1, scalar FP and conversions, round-to-odd FP32 FMA; u3 units + the optional-data contract). No recipe change.
- r35 2026-11-11 `hao_nx_stats=1` and `hao_bridge_stats=1` only in dev builds (`IMAGE_DEV_BUILD=1`); `hao_crash=1` stays in every build. Dev builds get r34's config.plist unchanged.
- r34 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 790720d; HaoExec and stamp_identity.py come from it. No recipe change; boot validation pending.
- r33 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 7415a0b; HaoExec comes from it (rebuilt by that commit). No recipe change; boot validation pending.
- r32 2026-11-11 No longer copies OpenCore.efi and OpenRuntime.efi from `opencore_debug`: the base EFI build already installs every OpenCore binary from that same archive. Input row `opencore_debug` dropped from this patch (the runner still reads it for the base EFI). Same ESP bytes expected; build validation pending.
- r31 2026-11-11 Use the Sandbox operation adapter that prunes isolated unsupported roots from removed operations. The previous adapter refused apsd and left Setup Assistant waiting on its activation-record request. Full boot validation pending.
- r30 2026-11-11 Bind 26B131 kernel repairs and IntelCommpage to the freshly linked collection, and stamp HaoExec for that kernel. Boot validation pending.
- r29 2026-11-10 Initial public release.
