# hao-opencore

Configures the image's OpenCore so the machine boots the Darwin 27 x86_64 kernel: the kernel repairs and
IntelCommpage that let this kernel start on Ivy Bridge hardware, `HaoExec` for arm64-only programs, and the boot
arguments below. A model's own drivers and settings are its model pack's (for example
[opencore-mbp92](../opencore-mbp92/README.md), which runs just before this recipe); the reasons for its
model-generated kexts (USB map, ECM override) and their provenance are its target's `platform_data`
(`opencore_reasons`, `kext_provenance`), so nothing here names a model. It writes the end-user configuration: no OpenCore log
and no measurement switches. Dev sets add those with `dev-diagnostics` (pack `dev`).

## How it works

`apply.sh` edits the image's OCLP ESP (`$ESP/EFI/OC`):

- OpenCore.efi, OpenRuntime.efi and every other OpenCore binary are already the official OpenCore 1.0.5 release
  golden-run chose (RELEASE unless the assembly says `"opencore": "debug"`) when this runs:
  `pipeline/stages/build-efi.py` replaces all of them in OCLP's generated EFI. `Misc.Debug.Target` is 0: no OpenCore
  log and nothing on screen (OCLP's base config logs to the screen). `dev-diagnostics` turns the ESP file log on in
  dev sets.
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
- `patch.json` `files` declares the config keys the script in `apply.sh` sets to fixed values (`Misc.Debug.Target`,
  `Misc.Debug.DisplayLevel`, `boot-args`, the Delete list) as `plist.set` edits in [edits/config.json](edits/config.json),
  after the kernel repairs and the IntelCommpage Kernel.Add entry as the external op `kc.bind` (made on the target,
  after the BootKC; `tests/test_kc_bind.py` proves the bytes), and the two kext folders as whole-folder origins. HaoExec's executable is declared on its own
  ([edits/haoexec-identity.json](edits/haoexec-identity.json)): the external op `hao.stamp` (the row's
  `stamp_identity.py` against the linked BootKC), so the materialiser defers it to the target, after the BootKC; the
  stamp is not re-signed (OpenCore does not check the kext's signature). Not declared yet: the HaoExec `Kernel.Add` entry (no append
  in `plist.set`). The `boot-args` value is the one without the dev-only statistics arguments. `tests/test_edits.py`
  proves the values against `apply.sh`.
- Kernel.Add keeps OCLP's RSRHelper, KDKlessWorkaround and CatalinaIntelI210Ethernet entries (and their bundles) but
  sets them `Enabled` false. QEMU booted the image without the first two, and this Mac's wired Ethernet is the BCM5701.
  CryptexFixup stays on: without it the modified OS cryptex does not graft and the boot stops.

## Why each setting is there

[src/config-reasons.json](src/config-reasons.json) gives a reason for every enabled element of the finished
`config.plist` (PROTOCOL S18), whichever recipe or OCLP put it there:
- every enabled kext, Kernel Patch and Block entry, ACPI and DeviceProperties entry;
- every Kernel, Booter and UEFI quirk that is on;
- every Misc.Security value;
- every NVRAM Add key (with its value for csr-active-config, revblock and revpatch) and NVRAM Delete key;
- WriteFlash.

`apply.sh` runs `src/config_reasons.py check` on the config it has just written and stops the build on an element
without a reason; `tests/test_config_reasons.py` covers it (and a real config with `HAO_OPENCORE_IMAGE_CONFIG`). A
reason that starts with `UNTRACED:` says what is not known yet and which test settles it. The list
was taken from a real image build's finished config, not from a QEMU test ESP. Those carry VirtualSMC and OVMF quirks
a real image does not have.

Every enabled kext must also come from a known source ([src/kext-provenance.json](src/kext-provenance.json)), and
`apply.sh` stops the build otherwise.
- Every third-party bundle pins all of its files' sha256. Each is byte-identical to its zip in the pinned OCLP source,
  and every one of those zips is the same git blob as upstream OCLP's `payloads/` at tag 3.0.0-rc.5 (and main).
- Lilu, CryptexFixup, AirportBrcmFixup and BlueToolFixup also match their Acidanthera release binaries.
- The USB maps and ECM-Override are OCLP-generated and codeless.
- HaoExec and IntelCommpage are hao's own, checked by kc_bind.py and repairs.py.

## Boot arguments

Every argument's reason is also in [edits/config.json](edits/config.json) (`arg_reasons`), and `patches.py check`
refuses a boot-args value with an argument that has none. `keepsyms=1` and `ipc_control_port_options=0` come
from OCLP's config (reasons there). OCLP's `-nokcmismatchpanic` (for RSRHelper) is removed, as is `-pmap_pcid_disable`
(a 2026-11-02 PCID test that changed nothing); a boot-args value carrying either loses it.

| Argument | Why |
|---|---|
| `-lilubetaall` | Lets Lilu-based OCLP kexts load on an unknown kernel version. |
| `amfi_get_out_of_my_way=1` | `hao-runtime` signs each program's x86_64 slice ad hoc with its own entitlements, including Apple-private ones (launchd: Preboot, graftdmg, trust caches). With AMFIPass alone, AMFI rejects `/sbin/launchd` and the kernel panics. |
| `amfi_force_policy=8` | AMFI's dyld policy counts an entitled (`CS_RESTRICT`) program as restricted again, as with SIP on, so dyld denies it the `DYLD_*` path variables unless it also requires library validation. Bit 0x8 (`forceDefaultDyldEnvVarsPolicy`) is read only by the dyld environment policy. See [AMFI dyld policy](#amfi-dyld-policy). |
| `auxKCAtBoot=false` | kernelmanagerd skips building an auxiliary kernel collection at boot and loads the System collection at once. 27's kmutil cannot build an x86_64 collection ("unsupported arch: x86_64 / Unsupported collection kind"), so the build of the only candidate, 27's codeless `AppleMobileDevice.kext` (from 27's Data template), failed at every boot and held the System collection, and with it the framebuffer and WindowServer, back 18.5 s (23 s on the first boot). The codeless kext's personality still loads. Details: [Aux KC gate](#aux-kc-gate). |

`ic_csr_internal=1` is no longer written, and removed if present: 27's x86_64 dyld scans the disk for shared-cache
overrides (OCLP's on-disk Metal and MetalOld) only when it counts as an internal install. It used to make IntelCommpage
report `CSR_ALLOW_APPLE_INTERNAL` to every process, so os_variant made the whole system an Apple-internal build
(mobileassetd asked Apple's internal asset server: no Spotlight, Siri or language assets). Now golden-gate-dyld-internal makes dyld
answer internalInstall() itself, and `apply.sh` refuses an image whose dyld lacks that edit.

`hao_crash=1` (hao-runtime's crash lines on the console) is not written either, and removed if present: dev images
only (owner 2026-11-17: "Dev builds only"); dev-diagnostics adds it.

`hao_nx_stats=1 hao_bridge_stats=1 hao_diag_time=1` (hao's measurement switches) are never written here: only
`dev-diagnostics` adds them, in dev sets.

`-v`, `startup_debug=1` and `debug=0x100` are removed if the base config has them: early-boot evidence comes from
the unified log (and the ESP log in dev sets), and without `debug=0x100` a panic reboots and leaves `aapl,panic-info` in NVRAM
instead of waiting for a debugger.

Every end-user set includes this recipe, so every image ships with AMFI relaxed (`amfi_get_out_of_my_way=1`). That
is the owner's decision (2026-11-17: "Ship, disclose"), stated in the install flow and the release notes. Its status
is `product`.

## Aux KC gate

Measured (laptop test 2026-11-12, image 8f5ddce (26B131), two boots, log `machTimestamp` s after the
kernel): kernelmanagerd logs "performing auxiliary activation" (43.94 / 48.04), finds no
`/Library/KernelCollections/AuxiliaryKernelExtensions.kc` and asks kernelmanager_helper to build one ("Attempting to
clean up and build from approvals"). The helper is spawned for that request, starts (10 s), loads the Boot and System
collections, finds `/Library/Apple/System/Library/Extensions/AppleMobileDevice.kext` as the only candidate and fails
("building auxiliary kext collection failed ... unsupported arch: x86_64"). Only then (67.08 / 66.49) does kernelmanagerd
log "Preparing collection load into kernel"; the kernel loads the System collection at 68.03 / 67.47 and the
framebuffer is published 0.3 s later.

27.2's `/usr/libexec/kernelmanagerd` (KernelManagement_executables-514.40.8, the one the image runs): when the
Aux collection does not load, kernelmanagerd reads the boot-arg `auxKCAtBoot` (lazy property `disableAuxKCAtBoot`,
true when the value is `false`) and, if set, logs "AuxKC building is disabled during boot due to boot-arg. Proceeding
without AuxKC." instead of "Attempting to clean up and build from approvals."; both branches continue at the same
code. Expected, not yet shown on a boot: codeless personalities (AppleMobileDevice) are still sent with the System collection, as now.

Not chosen: removing `AppleMobileDevice.kext` (kernelmanagerd sends the build request to the helper without looking at
the candidates, so the helper start and collection loads would likely remain, and iPhone USB devices would lose
`SupportsIPhoneOS`); a prebuilt Aux KC (kmutil refuses a collection of only codeless kexts).

## AMFI dyld policy

With SIP's unrestricted-filesystem bit allowed (`csr-active-config` 0x803), AMFI clears its platform hardening at
start, and with it the "hardened" fact of its dyld policy (`csproc_hardened_runtime() && hardeningEnabled()` in
`macos_dyld_policy_collect_state`). dyld then honoured `DYLD_INSERT_LIBRARIES` and the other `DYLD_*` variables in
hardened platform programs: measured on 26B131 in hao's hardened stub of `/usr/bin/sw_vers`, for a user and for root.
Nothing the image ships needs that.

Two settings close it, each limited to AMFI's dyld environment policy:

- Hardened programs: `src/amfi_dyld.py` adds one `Kernel.Patch` (`Identifier` AMFI, `MinKernel` = `MaxKernel` = the BootKC's Darwin version,
so the patch never reaches the Tahoe Recovery's kernel). It replaces only that `hardeningEnabled()` call with `mov al,1`. A program
whose own signature has the hardened runtime counts as hardened again in the dyld policy, and dyld is denied the
`DYLD_*` path variables (`DYLD_INSERT_LIBRARIES`, library and framework paths) unless it is entitled (`allow-dyld-environment-variables`, `get-task-allow`), as on a Mac with SIP on.
The hardening flags AMFI would force onto platform binaries (library validation, `CS_KILL`) stay off: turning hardening on
as a whole (`amfi_force_policy=2`) would force library validation into the Apple programs OCLP's Metal loads into.
The script refuses the build unless the site occurs exactly once in the collection, lies in AMFI and calls a
function of `hardeningEnabled()`'s shape (it reads a flag and returns 1 if set). The build checks its input and the
config it writes, not the running kernel: OpenCore skips a patch whose bytes it does not find, so only a boot shows
the patch applied (Verify).
- Entitled programs: `amfi_force_policy=8` makes AMFI count a `CS_RESTRICT` program as restricted again (with
  SIP's filesystem bit allowed it otherwise does not). A restricted program that also requires library validation keeps
  the path variables, as on a Mac with SIP on. The boot-arg is not version-scoped: a Recovery booted through this
  OpenCore gets it too (the Recovery tools that insert libraries run in the NetBoot Recovery, whose config is OCLP's).

What stays open (AMFI relaxed, owner D1): a restricted program that requires library validation keeps the path
variables while `amfi_get_out_of_my_way` relaxes library validation, and a program that is neither hardened nor
restricted still accepts an inserted library and keeps its Apple signature. Narrowing AMFI itself is v0.1.0 research.

### Load paths

The same policy also decides which load paths dyld follows (dyld-1378 `dyld/Loader.cpp`; AMFI 25G330
`macos_dyld_policy_env_vars` and `macos_dyld_policy_at_path`), so it changes what a library the image adds may link:

- With the path variables denied (a hardened program not entitled to them, or a restricted one without library
  validation), dyld refuses every file-relative path, `System/...`, `./` and `../` alike: "relative path not allowed in
  hardened program" (`isFileRelativePath`, checked on each path tried, after `@` expansion, so a relative `LC_RPATH`
  entry fails the same way).
- A restricted program (AMFI's at-path policy: `CS_RESTRICT` with `amfi_force_policy=8`, setugid, a `__RESTRICT`
  segment, unless it requires library validation) gets no `@executable_path` expansion and no `@loader_path` in the main
  executable's own `LC_RPATH`. A hardened program alone keeps both.

OCLP's 31001 `libLLVM.dylib` linked its helper by a relative path, so the hardened, restricted MTLCompilerService
could not open the 31001 compiler once r72 landed (fixed in metal-compiler-compat r11). `tests/test_load_paths.py`
holds every non-Apple Mach-O the release image installs to this rule: OCLP's overlay, every file the set declares (as
`pipeline/materialize.py` makes it), hao's payload and launch stubs, the bundled CPython. It also requires every
`macho.add-dylib` edit to name an absolute path. A library is held to the rule whatever loads it; a program only when it
can be hardened or restricted (runtime flag or entitlements). On the release set nothing else is refused: hao's stubs
and launcher list `/usr/lib/hao` as an absolute `LC_RPATH`, and every added load names an absolute path.

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
`kmutil showloaded | grep -iE 'hao|commpage'` (and the model's display and Ethernet drivers, its pack's Verify), `sysctl kern.hao` after the first arm64-only
exec, and the boot log line "HaoExec: arm64-only executables now run through /usr/libexec/hao".

AMFI dyld policy: the build log has `amfi-dyld.json` (AMFI UUID, Darwin version, site), and a dev image's OpenCore
log shows the patch applied once. On the running system, with a test library whose constructor prints a line:
`DYLD_INSERT_LIBRARIES=LIB /usr/bin/sw_vers` prints none, as user and as root, and neither does an entitled
program without the hardened runtime (`codesign -dv` flags `restrict`, no `runtime`). `sysctl security.mac.amfi.force_policy`
is 8. The same library in an ad hoc signed
copy of a program without the hardened runtime still prints it (control). OCLP's Metal still loads (`vmmap` of
WindowServer shows `MetalOld.dylib`).

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

Copy the binaries to the target (QEMU or a Mac running the image). Run `iosurface-value` and `iosurface-value-cow` (the
second carries WebContent's hardened-process and CoW containment entitlements) with byte counts `16`, `1024`,
`4096`, `8192`, `65536` and `1048576`; each must exit 0 and print `roundtrip=1`. Run `cow-guard-control` and
`cow-guard-control-cow` with `entry 0`, `entry 1`, `copy 0` and `copy 1`: only `cow-guard-control-cow entry 1`
must still die with VM guard flavor 13 (status 137), which shows the fix leaves the VM policy in place. Each probe
has a 15-second alarm. Finally load a website in Safari and scroll, navigate and reload.

## Validation

Laptop 2026-11-22, a MacBookPro9,2: the dev-beta image c3c0a5b (26B131, built from 7459d3e6 with r87; profile dev-beta-v0.0.1: the beta set plus dev-access) was pushed, booted to Setup and used (Maps, System Settings, Weather, Control Center). Its QEMU first boot passed in 490 s with the shared cache rebuilt in place and checked against the release (UUID 31DA2287, 36 files). Known issues, not this recipe's: first boot is slow (load above 400 for several minutes) and Setup's Transfer pane leaves its selection ring on "From a Mac".

## Revisions
- r88 2026-11-22 Validated r87 on hardware (Validation). Nothing it installs changes.
- r87 2026-11-21 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 497f126 (hao-runtime-26B131-20261120-497f126-stock). No recipe change.
- r86 2026-11-20 config-reasons.json records SIP 0x807 (dev-capture r4 adds task-for-pid on dev images); without it every dev-lite build stopped at the efi-bundle check. Nothing it installs changes.
- r85 2026-11-18 Names no model (S15): the reasons and provenance of the mbp92 target's OCLP-generated kexts (USB-Map, USB-Map-Tahoe, ECM-Override) moved to the mbp92 target's `platform_data`, and `config_reasons.py` reads the target's (`--target-data`, default `HAO_TARGET_DATA`). The generic image is known from its target (`opencore_generic`), not an SMBIOS model. README wording names no model or GPU. Nothing it installs changes.
- r84 2026-11-18 Reasons and tests state what each setting does and why, with no review IDs or weakness notes (owner: no vulnerability details in public text). Nothing it installs changes.
- r83 2026-11-17 HIDProxyKernelMatch.kext's reason and provenance removed with the kext (opencore-mbp92 r5). Declaration only.
- r82 2026-11-17 Test: every non-Apple Mach-O the release image installs links by paths dyld follows under the restored AMFI dyld policy (`tests/test_load_paths.py`, README "Load paths"). Nothing it installs changes.
- r81 2026-11-17 Reason and provenance for opencore-mbp92's HIDProxyKernelMatch.kext (fixes #79). Nothing it installs changes.
- r80 2026-11-17 Leftover settings removed (QEMU batch boot): `-nokcmismatchpanic` and `-pmap_pcid_disable` dropped from boot-args; RSRHelper, KDKlessWorkaround and CatalinaIntelI210Ethernet disabled; bootercfg and bootercfg-once no longer deleted. CryptexFixup stays.
- r79 2026-11-17 CryptexFixup's reason is traced: without it the modified OS cryptex does not graft and the boot stops (QEMU). It stays. Declaration only.
- r78 2026-11-17 Reasons for the generic image's run-efi-updater (Add No, and its Delete), from the QEMU reference: no firmware updater on a VM or PC. Generic-only, so a Mac image still refuses them. Declaration only.
- r77 2026-11-17 config-reasons.json has OCLP's `revblock=media`: the reasons check runs inside this recipe, before golden-gate-live-text-service turns it into `none`, and the r70 build stopped on it. Declaration only.
- r76 2026-11-17 Declares its scope (`requires`, docs/requires.md): the kernel repairs, IntelCommpage and AMFI site are bound to 26B131's kernel and the 25G342 donors; its OpenCore part is model-neutral (the model's own is its pack's). Nothing it installs changes.
- r75 2026-11-17 The edit spec's origin names the target's model generically (GEN1, S15). Declaration only.
- r74 2026-11-17 Status product (was experiment): every end-user set ships it, AMFI relaxed by the owner's decision D1. Declaration only; `validated` stays false until a boot proves the re-cut.
- r73 2026-11-17 The generic image's elements have their reasons (VirtualSMC and the non-Apple-firmware quirks, all "generic image only"), and VirtualSMC its provenance (Acidanthera 1.3.8). `config_reasons.py` refuses a generic-only element in any config that isn't the generic one (SMBIOS iMac20,1, created). Nothing a 9,2 image gets changes.
- r72 2026-11-17 Hardened and entitled programs keep AMFI's dyld environment policy: one `Kernel.Patch` bound to the BootKC's AMFI (`src/amfi_dyld.py`) and `amfi_force_policy=8`, so dyld no longer honours `DYLD_INSERT_LIBRARIES` in them (owner D1, 2026-11-17).
- r71 2026-11-17 `hao_crash=1` leaves the end-user boot-args and is removed if present (owner 2026-11-17: dev builds only); dev-diagnostics r7 adds it to dev images.
- r70 2026-11-17 Every enabled element of the finished config.plist has a recorded reason (`src/config-reasons.json`), and every enabled third-party kext is byte-identical to its pinned upstream OCLP payload (`src/kext-provenance.json`); `apply.sh` stops the build otherwise (PROTOCOL S18). Nothing it installs changes.
- r69 2026-11-17 Row `kernel_repair_efi` -> kernel-repairs-26B131-r44 (kernel-foundation r44): IntelCommpage no longer carries the `ic_csr_internal` probe, and the Sandbox adapter rewrites a shared modifier list once. config.plist is byte-identical to r42's; only IntelCommpage's executable changes. Row `kernel_repair_binding` -> kernel-binding-26B131-r44, the same inputs' profile, which pins that executable.
- r68 2026-11-17 Each boot argument in `edits/config.json` names its own reason (`arg_reasons`; edit specs refuse a boot-args value without one); `-pmap_pcid_disable` is recorded as not known to be needed (a 2026-11-02 isolation test that did not change the stall). Declaration only: nothing it installs changes.
- r67 2026-11-16 The early Tahoe kext step (`src/early_kexts.py`, `src/early-kexts.txt`, its test) moves to the new recipe opencore-mbp92 (pack mbp92), which runs just before this one; it adds no kext on any recorded build, so the config is the same.
- r66 2026-11-16 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r65 2026-11-14 Input row `hao_runtime` (26B131) now names the DEV payload built from a clean local hao-runtime c66997a (hao-runtime-26B131-20261114-c66997a2a0bb; libhao b8f0480f, the bytes proven on the test laptop): native SwiftUI.View modifiers lend guest-made self with native nils (diag#0038). Dev images only until c66997a is pushed. No recipe change.
- r64 2026-11-14 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 79bc70e tree (hao-runtime-26B131-20261114-79bc70ead081): guest dlopen resolves @rpath, @loader_path and @executable_path from the calling image (Xcode starts instead of aborting at launch). No recipe change.
- r63 2026-11-14 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime bd08b64 tree (hao-runtime-26B131-20261114-bd08b64a1563; libhao ffa4eeb6, the bytes proven on laptop 1 for Control Center): Finder (diag#0032), mds, the Control Center warm and cold-open fixes, native stack per callback level (diag#0005). No recipe change.
- r62 2026-11-14 `apply.sh` copies IntelCommpage.kext and HaoExec.kext onto the FAT ESP as data only (`ditto --norsrc --noextattr --noacl`): the stick's Recovery runs no doubleagentd, so ditto's default extended attributes and ACLs fail there ("Attribute not found", as golden-image.py's ESP copy did in bench2's QEMU install). The files written are the same.
- r61 2026-11-14 Input row `kernel_repair_binding` now hashes only the files a release can give a user's Mac (`# hash: release-files`: its published files plus the made-on-Mac ones, pipeline/lock.py scope()), so its sha256 changed. The same artifact, so what this recipe installs is unchanged.
- r60 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 399e78e tree (hao-runtime-26B131-20261113-399e78e32376, tag v0.1.0-beta.1): Optional witness nils returned to native as a callback result (diag#0001). No recipe change.
- r59 2026-11-13 Documentation only: the declared `boot-args` reason no longer says the Intel commpage reports Apple-internal CSR (dropped in r52), and the page names tests as laptop tests on an image. `apply.sh` and what it writes are unchanged.
- r58 2026-11-13 Input rows `kernel_repair_efi` and `kernel_repair_binding` (26B131) now name kernel-repairs-26B131-r42-53815151 and kernel-binding-26B131-r42-53815151, rebuilt at 53815151 so build.json names its inputs as `store/...` paths instead of the builder's home folder. `EFI/` (config.plist, IntelCommpage.kext) is byte-identical to r42's; the binding profile differs only in the producer commit and its pin of the new build.json. Installed bytes unchanged: the two recorded 2026-11-13 26B131 images' BootKCs bind to exactly their config.plist (tests/test_kc_bind.py with the new rows).
- r57 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 1de47eb tree (hao-runtime-26B131-20261113-1de47eb87a7f): the ControlCenter GridSizes and Displayable refusals fixed (2b19233) and the stub skeletons (1de47eb). No recipe change.
- r56 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 9dce413 tree (hao-runtime-26B131-20261113-9dce4138ddfa): the ControlCenter crash fix and f66403c's float-HFA stack fix. No recipe change.
- r55 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r54 2026-11-13 `apply.sh` runs Python as `$OCLP_PYTHON` (the kit's CPython for the Mac the assembly runs on, arm64 or x86_64), falling back to the builder VM's share copy; same Python version and bytes written.
- r53 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 397b267 (hao-runtime-26B131-20261113-397b2675d037). No recipe change.
- r52 2026-11-13 No `ic_csr_internal=1` (removed if present): the image's dyld counts as internal itself (golden-gate-dyld-internal's internalInstall() edit), so only dyld sees an internal install and mobileassetd uses the public asset server; `apply.sh` refuses an image whose dyld lacks the edit.
- r51 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 43af852 (hao-runtime-26B131-20261113-43af85296aa9). No recipe change.
- r50 2026-11-13 End-user configuration only: `Misc.Debug.Target` 0 (no OpenCore log, no file writes at boot) and no measurement switches in any build; `IMAGE_DEV_BUILD` is gone. Dev sets get `hao_nx_stats=1 hao_bridge_stats=1 hao_diag_time=1` and the ESP file log from the new `dev-diagnostics` recipe (pack `dev`). Edits `debug-log-to-file` (65) and `debug-log-info-lines` replaced by `no-opencore-log` (0).
- r49 2026-11-13 A normal boot shows only the Apple logo: `Misc.Debug.AppleDebug` false, and images use the OpenCore 1.0.5 RELEASE archive (row `opencore_release`; golden-run picks it unless the assembly says `"opencore": "debug"`). The DEBUG build printed its early lines and apfs.efi's on screen.
- r48 2026-11-12 Input rows `kernel_repair_efi`, `kernel_repair_binding` and `intel_commpage_abi` (26B131) now name r42: IntelCommpage reads its collection addresses from `__DATA,__ic_table` at load. kc_bind.py writes this BootKC's table into it in `binder-only` mode instead of refusing a moved layout, and in `binder` mode requires the table to equal the kext's own. config.plist is unchanged. The IntelCommpage binary is new.
- r47 2026-11-12 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 0a2c2aa (hao-runtime-26B131-20261112-0a2c2aad4755). No recipe change.
- r46 2026-11-12 Dev builds add `hao_diag_time=1` (hao-runtime 0a2c2aa): mach-clock stamps and phase lines on hao's console lines, and PID 1's lines from before the Data volume mounts, so a boot log shows launchd's own startup on the kernel's clock.
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
