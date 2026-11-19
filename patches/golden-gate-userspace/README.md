# golden-gate-userspace

Makes the Golden Gate userspace (macOS 27.0 26A428 or 27.2.1 26B131, whichever build the assembly selects) run
natively on the Intel image: the combined OS cryptex with the x86_64 shared cache, and the adapters that let 27.x
clients use the Tahoe programs and drivers kept for this Mac. It is the second entry of every Golden Gate set, after
[darwin27-kernel](../darwin27-kernel/README.md); every later recipe edits what these two produce.

The base layers (`pipeline/stages/golden-image.py`, [docs/building.md](../../docs/building.md)) already installed
Golden Gate's System and Data, the Tahoe donors and OCLP's hardware overlay. This recipe does not copy or keep any
System tree.

## Inputs
- `golden_gate_userspace`: the IPSW's filesystem, SystemOS, AppOS and RosettaOS images, made by
  [build/](build/README.md).
- `display_state_shim`, `display_install_helper`: display adapters and their installer
  ([tools/display-fixes](../../tools/display-fixes)).
- Not inputs here since r84: `io80211_compat` and `wifip2p_compat`, which
  [golden-gate-wifi-compat](../golden-gate-wifi-compat/README.md) installs. Their source stays here (`src/io80211`,
  `src/wifip2p`, below).
- `corewifi_compat`: the CoreWiFi compatibility library, built and checked for one build (and its Tahoe CoreWiFi
  donor) by `src/corewifi/make`. Rows exist for 26B131 only.
- `codeid_carrier`: the code identity carrier's objects (`carrier.o` and hao-runtime's `libhao_codeid.a`), made by
  `src/codeid/make` (common row: the objects are build-independent; each cache is checked when it is edited).
- `hao_runtime`: the [hao-runtime](../hao-runtime/README.md) payload, read only (`src/read-from-share.txt`): its
  stubs for the arm64-only programs in the App and OS cryptexes.
- The native cache repairs' source is not an input row: `make.sh` copies `tools/metal-fixes` and
  `tools/audiomxd-fix` from this checkout into the staged recipe (`build/`).
- From the runner, not a row of this patch: `golden-image.py` sets `TAHOE_DRIVERKIT_ARTIFACT` (the assembly's
  `tahoe_driverkit_donors`) and `TAHOE_DONOR_TOOL` (`tools/tahoe-donors/make`), which `src/kc-hook.sh` passes on.

## How it works
`src/kc-hook.sh` runs `src/install.py` after [darwin27-kernel](../darwin27-kernel/README.md) has built the kernel
collections; the runner calls it like every other recipe. It:
- builds the combined OS cryptex from Golden Gate's native libraries and RosettaOS's x86_64 shared cache, adds
  Tahoe's generic Intel DriverKit cache, applies the native cache repairs (including the Swift conformance repair
  below) and the code identity carrier (below), re-signs the cache with 4 KiB code pages (`src/cache_pages.py`;
  the x86_64 kernel registers only 4 KiB page signatures), checks every Swift metadata reference of the x86_64
  cache (`src/swift_refs.py`, stops on any bad one) and seals the image so 27.0's launchd can graft it;
- embeds hao's stubs into the cryptexes' arm64-only programs before sealing (`src/cryptex_stubs.py`, below), and
  for that rebuilds the App cryptex the same way (fresh raw image, AppOS's files, stubs, sealed); with no App stub in
  the payload it installs Apple's `app.dmg` unchanged;
- links Rosetta's cache directory to the combined cache (`src/cryptex-cache-alias.sh`), because native dyld still
  looks there;
- gives Tahoe programs that serve 27.0 clients (WindowServer) the 27.0 program's Info.plist and entitlements;
- installs the CoreWiFi compatibility library in front of Tahoe's CoreWiFi, which the native Tahoe donors put at
  CoreWiFi's path, so Tahoe's airportd and every 27.x client share the CoreWiFi airportd was built with;
- disables jobs that cannot run on an Intel Mac (`hybridsearchd` exits on non-Apple silicon and would restart in a
  loop);
- restores setuid/setgid modes after copying.

The IO80211 and WiFiPeerToPeer compatibility libraries over OCLP's Modern Wireless wrappers are
[golden-gate-wifi-compat](../golden-gate-wifi-compat/README.md)'s (pack wifi-broadcom-modern), which runs right after
this recipe.

Which framework sits beneath OCLP's replacements is decided by the base layers, not here. With the 26B131 donor
artifact (`minimal-native-25G342-26B131-corewifi-r3`) Metal, OpenCL, AppleGVA, AppleGVACore, GPUCompiler, MTLCompiler, IO80211
and WiFiPeerToPeer are the 27.x build's own, with OCLP's overlay on top. The older 26A428 artifact still installs
Tahoe's copies of those frameworks first.

### Declared files
`patch.json` `files` lists everything this recipe adds or changes on the installed system, and `edits/*.json` holds the
edit specs ([tools/edit-engine](../../tools/edit-engine/README.md)); `patches.py explain golden-gate-userspace` prints
them. The in-cache repairs are `matcher` specs with their sites resolved for the 26B131 cache (`cache-*.json`, in the
OS cryptex where the running system grafts it: trackpad V3, EndpointSecurity selectors, CoreRoutine, the MPSMatrix
initializer, audiomxd, the model gates, the Swift conformance references, the code identity carrier and the Swift prespecialization switch), followed by the 4 KiB
re-signing; the two cryptex images are `cryptex.build` specs (`cryptex-os.json`, `cryptex-app.json`) and each of the 26
stubs is a `hao.embed-stub` spec (`embed-*.json`). `hybridsearchd-job.json` and `launchd-job-cache.json` are
`plist.set`; the renamed CoreWiFi (`corewifi-tahoe.json`) and
`windowserver-27-identity.json` are declared with their signing; WindowServer's signature takes the 27.x program's
entitlements (`sign.entitlements_from`, as `resign_with_entitlements` reads them). Files the hook leaves
`restricted` say so in their `install` flags. The engine applies none of the external ops (cache re-signing, image
builds, stub embedding), so `tests/test_edits.py` proves those through the recipe's and the payload's own code. The Preboot copies of
SystemVersion.plist (under the build's group folder) are not declared.

### Model gates
27.x keeps unsupported-model lists for three features, and older Intel Macs are on them (the x86_64 cache, image:
section): Sidecar (SidecarCore `__TEXT,__const`), AirPlay to Mac, i.e. this Mac as an AirPlay receiver
(AirPlaySupport `__TEXT,__const`) and Continuity Camera (CMCapture `__TEXT,__cstring`). Each list is consecutive
NUL-terminated model names. `src/model_gates.py` renames the target Mac's entry in each (one byte, same length; the
model and its new name are the target's `platform_data` `model_gates`, read from `HAO_TARGET_MODEL` and
`HAO_TARGET_DATA`; the images are `src/model-gates.json`), so the model check no longer finds it. A target without
`model_gates` gets no edit; for each Mac with it, a recipe of its model pack declares the sites
([golden-gate-model-gates-mbp92](../golden-gate-model-gates-mbp92/README.md)). It refuses unless the
model is there exactly once, between other model names. Other images that name the model (CoreFoundation, ProductKit,
ProductKitCore, ServerInformation) are name tables and stay. This is the same string edit acidanthera's FeatureUnlock
makes, made once in the cache instead of by a kernel extension that searches every validated page at run time (the
Tahoe images ran without FeatureUnlock). AirPlay *sending* and Universal Control are not model-gated on 27.x for this
Mac; Universal Control needs AWDL (diag#0044).

### Swift conformance type references
Apple's x86_64 cache (RosettaOS, untouched) has direct conformance type references that point at unrelated bytes
instead of the type's descriptor: 21 in 26A428 and 47 in 26B131, in PaperKit (macOS and iOSSupport),
PhoneAppIntents (26B131) and `_MusicKitInternal_AppIntents`, all for nested enums (`SharedPaperPage.Tag`,
`MusicItemSiriUnion.MusicItemSiriUnionCases`, ...). Every record of one type points to the same spot just before
its records. Any native Swift lookup by mangled name that scans those images faults: on 26B131 Cmd-Space's
Siri AI.app took SIGBUS in `ParsedTypeIdentity::parse` reading the "descriptor"'s name pointer, which lands in the
gap between `.06`'s data and linkedit mappings. The arm64e cache of the same build has no such reference.
`src/swift_refs.py` takes, for each broken record, the type the same arm64e record names (same image, same index,
same protocol), finds the one x86_64 descriptor of that image with the same context path and rewrites the 4-byte
reference; it refuses a record it cannot match exactly once. After the re-sign it walks every image's
`__swift5_types`, `__swift5_proto` and `__swift5_protos` (targets, indirect slots, descriptor names, witness
tables) and the install stops on any bad reference.

```sh
python3.14 -I patches/golden-gate-userspace/src/swift_refs.py X86_64_CACHE_DIR            # verify
python3.14 -I patches/golden-gate-userspace/src/swift_refs.py X86_64_CACHE_DIR --plan --reference ARM64E_CACHE_DIR
```

### Swift prespecialized metadata
libswiftCore turns its prespecialized metadata off for the whole process when
`_dyld_shared_cache_some_image_overridden()` is true, and on this image it always is. dyld scans the disk for roots
(golden-gate-dyld-internal), and files sit at cache install paths:
- OCLP's Metal, AppleGVA, AppleGVACore, IO80211, WiFiPeerToPeer and MultitouchHID;
- this recipe's CoreWiFi compat library;
- Apple's own `libsystem_{kernel,platform,pthread}`.

It was measured on the test laptop: "Disabling prespecialized metadata, dyld_shared_cache_some_image_overridden = 1".
The edits in `src/prespecialization-26B131.json` keep the metadata on. The shared-cache generator made them
(`make prespecialization-overrides`) for the pristine 26B131 cache (UUID 262FA161...).
`src/prespecialization.py` plans them, checking the UUID and every site's bytes, and `cryptex.py` applies them with
the other cache repairs before the 4 KiB re-sign. There are two kinds:
- **main:** the two calls that read the switch (libPrespecializedImageLoaded, the LibPrespecializedState setup) become
  `xor eax, eax; nop`;
- **.06:** the 113 descriptor-map slots of LibPrespecializedData whose descriptor lies in an overridden image (111 in
  CoreWiFi, 2 in WiFiPeerToPeer) are nulled, with their chain delta kept. An empty slot sends the name down the
  runtime's ordinary path, so a lookup never returns the cache copy's descriptor for a library that is loaded from
  disk.

The third reader, the conformance-block callback, is left alone. dyld's conformance table is not valid for an
overridden image with `__swift5_proto`, and 452 of its rows reference CoreWiFi.

**Gate.** The edits are right only for the set of overridden images they were made for. For that set of 10 images,
The generator's check found that 0 of 2,762 Apple entries and 0 of 1,282 catalog keys have a descriptor, argument or value
in one of them. `install.py` (`prespecialization.gate`) lists every image of the x86_64 cache family whose file
exists on the built System (dyld itself does not count) and stops the build unless the list equals the recorded one.
A changed list means the edits have to be made again with the tool. The data file is part of the cryptex cache key.

```sh
python3.14 -I patches/golden-gate-userspace/src/prespecialization.py plan X86_64_CACHE_DIR
python3.14 -I patches/golden-gate-userspace/src/prespecialization.py gate SYSTEM_ROOT X86_64_CACHE_DIR
```

### IO80211
OCLP's Modern Wireless wrapper replaces IO80211 and re-exports IO80211Old.dylib. Once a file overrides the cached
IO80211, dyld repoints every cached client's binding to it, so the wrapper must answer every symbol 27.x binds. It does
not: seven functions it exports as data (a CFString pointer), and `Apple80211Get20MhzChannelIndexBitmap`, which
WiFiAnalytics binds, it lacks. `src/io80211/io80211_compat.c` defines those eight as the build's own x86_64 IO80211
does (a full 32-bit sweep of 26A428 and 26B131, `tests/io80211-expected-BUILD-x86_64.txt`; they differ in one code)
and re-exports the wrapper, renamed IO80211OCLP.dylib.

```sh
python3.14 -I patches/golden-gate-userspace/src/io80211/make ARTIFACTS \
  --system SYSTEM_ROOT --cache X86_64_DYLD_CACHE --oclp OCLP_IO80211_DIR
```

`make` builds the library and runs `coverage.py` for the build named by SYSTEM_ROOT: every symbol the x86_64 cache
binds (its dyld patch table) and every on-disk program linking IO80211 imports must resolve, in order, to the
library, the wrapper or IO80211Old.dylib, and a symbol the build's IO80211 exports as code must not resolve to data.
Otherwise it stops with `MISSING/unsupported` and makes no artifact. 26A428 and 26B131 bind the same 34 symbols in
the cache and 57 from disk. `Apple80211GetBeaconCache`, also absent from the wrapper, is bound by no client of either. The wrapper's `Apple80211StartMonitoringEvent` is a forwarder (`dlsym(RTLD_NEXT)`, called unchecked) that finds nothing in translated processes, so the compat library forwards it to IO80211Old.dylib by handle (r44).

### WiFiPeerToPeer
OCLP's Modern Wireless wrapper also replaces WiFiPeerToPeer (re-exporting WiFiPeerToPeerOld.dylib) and lacks 36
Wi-Fi Aware classes that 26A428's and 26B131's x86_64 caches bind identically. dyld sets those class references to
`0xbad4007`; libobjc then aborts every process that loads APTransport's category on `WiFiAwareDatapathInfo`. That
covers coreaudiod (through the AirPlay HAL plug-in, so there is no audio server), AirPlayXPCHelper, ControlCenter and
sharingd. `src/wifip2p/wifip2p_compat.m` defines the 36 as empty `NSObject` subclasses, which is their superclass in
both builds; this Mac has no Wi-Fi Aware hardware. The library links only libobjc and re-exports the wrapper,
renamed WiFiPeerToPeerOCLP.dylib.

```sh
python3.14 -I patches/golden-gate-userspace/src/wifip2p/make ARTIFACTS \
  --system SYSTEM_ROOT --cache X86_64_DYLD_CACHE --oclp OCLP_WIFIP2P_DIR
```

`make` runs the same coverage check as IO80211 (`src/io80211/coverage.py`, given the library name). It skips the
build's own `usr/libexec/wifip2pd`, which OCLP overwrites with its x86_64 program, and records that it did so.

A class that exists but lacks a method still fails at run time: 27.x's WiFiAgent sends `-activate` to a new
`WiFiP2PKeychainAgent` in every user session, before it opens its `com.apple.wifi.WiFiAgent` listener, so with the
empty r43 stand-in the exception stopped it and Setup's own Wi-Fi join timed out. The stand-ins now answer what
27.x's on-disk programs name on them, as no-ops: `WiFiP2PKeychainAgent` `-activate`/`-invalidate` (WiFiAgent) and
`WiFiAwareStateMonitor` `-beginMonitoring`/`-endMonitoring`/`-setStateUpdatedHandler:`/`-setRealtimeModeUpdatedHandler:`
(27.x's airportd, which this image replaces with Tahoe's; Tahoe's binds none of the stand-ins).

The method pass (`src/io80211/methods.py`, run by `coverage.py` for both libraries) lists, for every class of the
build's own library a client binds, the selectors the build's class implements, a binding client names in its
selector references, and neither the provided class (exporting library plus categories, superclasses included) nor
NSObject answers. Selector references are untyped, so these are candidates. A gap on a stand-in named by an on-disk
program stops `make` (`MISSING method`). The rest is recorded in `build.json` under `coverage.methods`: stand-in gaps
named only by the x86_64 cache (its importers bind through shared GOTs, so their selectors are pooled; on 26B131 these
are the Wi-Fi Aware pairing, datapath and bootstrapping classes), gaps on OCLP's wrapper classes, and `unverified`
OCLP classes: WiFiPeerToPeerOld.dylib is cut from a 13.7.2 shared cache and its method lists name selectors relative
to that cache, so they cannot be read offline. 27.x's x86_64 IO80211 defines no classes, so its pass records none.

### CoreWiFi
The image's airportd is Tahoe's (27.x has no Intel one), and airportd hosts CoreWiFi's XPC server and auto-join
(CWFXPCManager, CWFAutoJoinManager live in CoreWiFi). With 27.x's CoreWiFi underneath it crashed on auto-join: 27.x
changed three callout reply blocks (an added `reason` argument lands where Tahoe's code expects the `NSError`, so
`objc_retain(0x2)`) and renumbered 213 of its 302 XPC request types (30 of the 48 airportd answers). So the image goes
back to Tahoe 25G342's CoreWiFi for every process: airportd and its clients then agree by construction.

Tahoe has CoreWiFi only inside its x86_64h OS cache. The native donors cut it out (`tools/tahoe-donors`,
recipe `cache_images`, with the `cache-dylib` tool) and install it at `CoreWiFi.framework/Versions/A/CoreWiFi` with its
own install name. `install.py` then renames it `CoreWiFiTahoe.dylib` (install name and file, re-signed) and installs
the compat library as `CoreWiFi`, re-exporting it, the same way as IO80211 and WiFiPeerToPeer. dyld loads the file
instead of the cached 27.x CoreWiFi and repoints the cached clients' bindings to it. airportd is not edited.

`src/corewifi/corewifi_compat.m` adds what 27.x clients need beyond Tahoe's CoreWiFi:
- `CWFEventLinkQualityAssessmentKey` (SymptomEvaluator) and the class `CWFColocatedConsentResult` (SpeakerGroupSetupKit),
  both strong imports;
- 16 methods 27.x clients name on Tahoe's classes (its `kMethods` table), added at load with `class_addMethod`, which
  never replaces a method: no-op getters/setters, `ENOTSUP` from `setBTCoexActiveProfileName:error:` and
  `performColocatedNetworkScanWithConsentStatus:`, and `-[CWFConfigurationProfileManager activateWithCompletion:]`,
  which runs Tahoe's `-activate` and then the completion block (threadradiod, nearbyd, sharingd).

```sh
python3.14 -I patches/golden-gate-userspace/src/corewifi/make ARTIFACTS --system SYSTEM_ROOT \
  --cache X86_64_DYLD_CACHE --donors TAHOE_NATIVE_DONORS --oclp OCLP_ROOT \
  --io80211-compat IO80211_COMPAT --wifip2p-compat WIFIP2P_COMPAT
```

`make` stops with `MISSING/unsupported` or `MISSING method` and makes no artifact unless:
- every symbol Tahoe's CoreWiFi binds (the donor's `cache-images.json`) is exported by the build's x86_64 cache or,
  for IO80211 and WiFiPeerToPeer, by the image's own compat, wrapper and Old files;
- every CoreWiFi symbol a client binds resolves to the compat library or Tahoe's CoreWiFi (`src/io80211/coverage.py`;
  cache importers whose only uses are CoreWiFi's own need no answer). The build's own airportd, wifip2pd and
  WiFiMigratorPlugin are skipped: the donors and OCLP replace them, and their replacements' imports are checked instead;
- the method pass (`src/io80211/methods.py`) finds no gap on a class Tahoe's CoreWiFi or the compat library provides,
  checking every class of the build's CoreWiFi (instances reach clients through CoreWiFi's API without a binding),
  whoever names the selector, with the `kMethods` table counted. Classes only 27.x has (link-quality assessment,
  direct-to-cloud stats, network path monitor) are recorded: Tahoe's CoreWiFi never creates them, so those features
  are off on this Mac.

On 26B131 Tahoe's CoreWiFi binds nothing the build lacks, 57 client symbols resolve (55 to Tahoe's CoreWiFi, 2 to the
compat library) and the method pass finds no gap over 172 classes and 50 clients.

### Code identity
macOS 27 programs run through hao's stubs, which are signed ad hoc, and several native Apple programs are re-signed ad
hoc by later recipes; every check of a code requirement (`anchor apple`, `identifier ... and anchor apple`) then
fails against them. Daemons that trust a peer by its Apple signature refuse it: tccd and backgroundtaskmanagementd
(Find My's Bluetooth prompt, Login Items), airportd (no Wi-Fi join), authd (Setup's automatic login after creating
the account). hao-runtime's `libhao_codeid` decides such a failure once for every process: when the code is listed in
`/usr/lib/hao/stub-identities` (hao writes it) or `/usr/lib/hao/native-identities`
([golden-gate-native-identities](../golden-gate-native-identities/README.md)), the caller's own requirement is
evaluated against the listed Apple-signed reference and that verdict returned; anything else is unchanged
(hao-runtime `include/hao/codeid.h`, `docs/security-model.md`).

`src/codeid_carrier.py` carries that decision in the image's x86_64 shared cache, inside Security.framework, so every
native process and every hao process (hao calls native Security through its bridge) gets it, whichever API path
reaches the check:

- It links `carrier.o` (`src/codeid/carrier.c`) and the members of `libhao_codeid.a` into the zero-filled free tail
  of the main subcache's `__TEXT` mapping (code, constants) and `__DATA` mapping (about 1 KiB of writable data), after
  the last image. Calls resolve to Security's own symbols, to Security's symbol stubs (which jump through the
  subcache's bound GOT, so they follow dyld binding and root patching like Security's own calls), else to the regular
  export of a system library in the same subcache; data references become `leaq` of the symbol. Anything that would
  need a rebase (an absolute pointer), a common symbol, or an unresolved or ambiguous symbol stops the build.
- `SecCodeCheckValidityWithErrors` and `SecStaticCodeCheckValidityWithErrors` begin with a jump to the carrier's
  hook, which runs the original (its 6-byte prologue `push rbp; mov rbp, rsp; push r15`, relocated, then a jump back
  into Apple's body) and returns `hao_codeid_decide(code, requirement, status, errors)`.
- `SecCodeCheckValidity` and `SecStaticCodeCheckValidity` must be the cache's forwarders into those bodies
  (`xor ecx, ecx` and a jump), so they, `SecTaskValidateForRequirement` and libxpc's dlsym'd checks
  (`xpc_connection_set_peer_code_signing_requirement`) all reach the hook. authd's
  `SecCodeCopyGuestWithAttributes` + `SecCodeCheckValidity(code, 0, "anchor apple")` and CoreWiFi's audit-token check
  are this path.
- Every site is found by shape and checked: one Security image, the hooked prologues, no branch into them, the
  forwarders, zero-filled and unreferenced free space (not named by the cache header; no rebase in the data tail).
  A cache that does not match stops the build (`MISSING/unsupported`). The edited main cache file is re-signed with
  the other repairs. On 26B131 the carrier takes 0xd3b bytes of `__TEXT` (of 0x2000 free) and 0x420 of `__DATA`
  (of 0x2ec0); 26A428's cache plans the same way.

What is not covered: checks that do not evaluate a requirement through these two bodies, such as cdhash comparisons
through `csops` (kernel CS flags, a pinned cdhash, trust caches), lightweight code requirements and Swift
`XPCPeerRequirement` if they are evaluated outside Security, and `SecCodeCopySigningInformation`, which still
describes the stub or the re-signed program. Unwinding through a carrier frame (only inside `hao_codeid_decide`) finds
no unwind information; no C++ exception crosses it.

```sh
python3.14 -I patches/golden-gate-userspace/src/codeid/make ARTIFACTS --hao-runtime HAO_RUNTIME_CHECKOUT \
  --cache ROSETTA_OS/System/Library/dyld
```

`make` builds `libhao_codeid.a` with hao-runtime's own Makefile from a committed tree, compiles `carrier.c`, plans the
edit of the given cache read-only and writes `codeid-carrier-DATE-HASH12/` with `build.json` (hao-runtime commit and
source hashes, object hashes, planned layout). Point row `codeid_carrier` at it.

It requires the explicit DriverKit donor and never takes DriverKit from an earlier installed image. It installs OS libraries only; it does not generate
Swift metadata, shader warm-up caches, icon indexes or account state.

### hao stubs in the cryptexes
Safari's agents (`/System/Cryptexes/App/usr/libexec`) and the XPC services of Safari's and WebKit's frameworks
(SystemOS, e.g. `SafariShared.framework/.../com.apple.Safari.SearchHelper.xpc`) are arm64-only programs inside the
App and OS cryptexes, which the running system grafts at `/System/Volumes/Preboot/Cryptexes/{App,OS}`. Without a stub
launchd runs them through `/usr/libexec/hao` with hao's code identity instead of their own, so a container or
entitlement lookup fails: SearchHelper asked containermanager with an empty identifier and aborted in
`+[NSString stringWithUTF8String:]`. The 26B131 hao payload is built from a root that has both cryptexes at those
paths, so its manifest names them there (2 App and 24 OS programs) and `stub-identities` lists them. hao-runtime's own
installer runs later on the System volume, where those paths are empty graft points, and records them as missing.
`src/cryptex_stubs.py` embeds them into the image being built, with the payload's `hao-install.py` checks (source
hash, bound Info.plist and seal, merged hash) and fails if any entry of that cryptex is not embedded. A payload built
without the cryptexes (the 26A428 one) has no such entries: nothing changes.

### Cryptex cache
Building and sealing the two cryptex images is most of this recipe's 5.7 minutes on 26B131. The
image code is `src/cryptex.py` (`build_os_image`, `build_app_image`); `install.py` does the rest (compat libraries,
WindowServer, disabled jobs, the record) and installs the images. `src/cryptex_cache.py` keys an image pair on
everything that decides its bytes:
- the rows `cryptex.py` reads (`golden_gate_userspace`, `codeid_carrier`, `hao_runtime`) and the assembly's DriverKit
  donor row, each by its row identity (`pipeline/lock.py` `key_line`) plus the content of the folder's small
  top-level files (manifests, build records);
- the code: `cryptex.py`, every module of this folder it imports (found from its imports; `install.py` is not one),
  `cryptex_cache.py`, everything under `build/` (the native cache repairs) and `tools/tahoe-donors`;
- the builder: its macOS build, `newfs_apfs`, `apfs_prepare_cryptex` and Python.

`pipeline/golden-run` writes the first two parts (`host_lines`) to the share's `cryptex-cache/key.json` and clones each
`store/cache/cryptex/KEY/` entry with the same host lines into `cryptex-cache/in/`; the guest adds its builder lines.
With `--cryptex-cache` (the runner's `GOLDEN_CRYPTEX_CACHE`) `install.py` copies a matching entry's images to Preboot,
keeping their holes, and uses them only if their sha256 and size match the entry; otherwise it builds them and writes
the pair to `cryptex-cache/out/KEY/`, which golden-run checks again (sha256) and keeps (the two newest entries, about 13
GB each). A reused pair is byte for byte the stored one, and `golden-gate-userspace.json` records the same
`cryptex` counts (including the stored image's seal line) as the build that made it. The outcome (`hit`, `miss`,
`stored`, `corrupt`) is in the build record (`build.json` `cryptex_cache`), not in the image. Without the option, or
with `golden-run --no-cryptex-cache`, the images are built every time as before. A sealed image is not reproducible
(each build has its own volume UUID and seal), so images built from the same key in two builds differ; images reused
from the cache are identical.

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests
# optional: WIFIP2P_OCLP_DIR / IO80211_OCLP_DIR = OCLP's 13.7.2-25 framework folders
# optional on a macOS 27 host: COREWIFI_DONORS = tahoe_native_donors artifact, COREWIFI_COMPAT = corewifi_compat.dylib
# optional: HAO_RUNTIME_PAYLOAD (hao payload) + GOLDEN_APP_CRYPTEX (mounted app.dmg): real App cryptex embedding
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/build/tests
# code identity carrier: HAO_RUNTIME = hao-runtime checkout (runs it); CODEID_CACHE = an image cache folder (plans it)
HAO_RUNTIME=... CODEID_CACHE=.../System/Library/dyld python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -k codeid
# Swift prespecialization edits on the pristine cache (and all cache declarations with the arm64e cache and carrier)
USERSPACE_X86_CACHE=.../dyld python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -k prespecialization
# cryptex cache: CRYPTEX_CACHE_IMAGE = a built sealed os.dmg (read-only), CRYPTEX_CACHE_SCRATCH = a folder with room
CRYPTEX_CACHE_IMAGE=.../cryptex1/current/os.dmg python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -k cryptex_cache
python3.14 -I patches/golden-gate-userspace/src/cryptex_cache.py key patches/golden-gate-userspace/src   # the code lines
# declarations (tests/test_edits.py): USERSPACE_X86_CACHE, USERSPACE_ARM_CACHE, USERSPACE_CODEID_CARRIER,
#   USERSPACE_SYSTEM_ROOT, USERSPACE_OCLP_IO80211, USERSPACE_OCLP_WIFIP2P, USERSPACE_DONOR_COREWIFI,
#   USERSPACE_DONOR_WINDOWSERVER, HAO_RUNTIME_PAYLOAD, GOLDEN_OS_CRYPTEX, GOLDEN_APP_CRYPTEX (each described in the file)
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -k test_edits
```
The fixtures cover the DriverKit requirement, the absence of any System-tree replacement, special modes, native cache repair plans (the Swift conformance repair and verifier on an authored two-architecture cache)
and adapter ordering. A built image must still pass filesystem, boot, native-service and application checks.

The carrier tests link it against this Mac's own x86_64 cache (Rosetta's) into memory beside the mapped cache and
call the hooks in an x86_64 process, so the relocated prologues run Apple's real Security bodies: an ad hoc re-signed
copy of `/usr/bin/true` listed with its original passes `anchor apple` and `identifier "com.apple.true" and anchor
apple`, and a running fat guest (ad hoc x86_64 beside Apple's arm64e `true`) passes by audit token; a requirement the
original does not meet, unlisted code, a modified and re-signed copy and an unlisted guest keep -67050; Apple's own
program is unchanged. Objects with an absolute pointer, a common symbol or an unknown symbol are refused. The entry
jumps themselves are checked statically against the image cache (PlanTests).

On a built image the main cache file holds `hao-codeid-carrier` at the start of its `__TEXT` tail, and
`golden-gate-userspace.json` names the hook addresses (`code_identity_carrier`). Then: Setup Assistant's account
creation logs in automatically (authd accepts loginwindow's `FUSAutologinDataKey`); `networksetup
-setairportnetwork` joins; `codesign --verify -R='anchor apple'` passes for a hao-run Apple program and fails for an
unlisted ad hoc one.

## Validation
NEEDS VALIDATION: r95 (the fitted OS image) has host tests with recorded image steps (copy into the fitted size before sealing; the 26B131 numbers); an image build (os.dmg size, seal, no holes) is open. r94 (#90, fully allocated cryptex images) has host tests (copy and allocate leave no hole and keep the bytes; the copy test fails on r93); an image built with it (os.dmg hole map empty) and the graft read of the x86_64 caches on a booted image are open. r92 was proven on hardware:

Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r92): automatic login to the desktop; Spotlight finds apps, Safari reaches the network, Bluetooth, sound and the camera are up, and 40 of 48 apps open and run 20 s. Open on that image and tracked on their own: Weather and News crash (#9, #62), Books (#15), gamed (#18).

## Revisions
- r95 2026-11-19 The OS cryptex image is sized to what it holds (`compact_image`): built as before at its files' logical size, then copied into a new image of the volume's used bytes plus a margin (`fitted_size`: 1 GiB or 3%) and sealed there. Most files are stored compressed, so on 26B131 the image was 26.2 GB with 13.25 GB used; r94 allocates the whole image (#90), and the fitted one keeps Preboot about 12 GB smaller than r94 (about 1 GB more than r93's sparse image).
- r94 2026-11-19 The cryptex images go to Preboot fully allocated (#90). The boot's APFS graft cannot map a hole of os.dmg ("_graft_dev_read: blockmap lut lookup failed", ENOENT; Books, Stocks, News and Podcasts SIGBUS in the x86_64 shared cache on dev 8305fdd and release c1eb676), and os.dmg had holes from two places: the sparse build (new_image truncates) and the cache copy, which also skipped all-zero 1 MiB pieces. install_cryptex now writes zeros over every hole of os.dmg and app.dmg, built or reused, and stops if one is left (`cryptex_cache.allocate`); the cache copy writes every byte. After the last post, the pipeline (pipeline/stages/golden-image.py) fills any hole a later writer left (golden-gate-shared-cache's fold makes a new sparse os.dmg), clones os.clone.dmg again and refuses a remaining hole; both check the free space first. Contents, sha256 and root hash are unchanged; Preboot holds about 13 GB more. The cache key changes (cryptex_cache.py is a key source).
- r93 2026-11-19 Validated r92 on hardware (Validation). Nothing it installs changes.
- r92 2026-11-18 The 26 cryptex embed specs carry the stub and result hashes of hao-runtime 256dd30 (row hao_runtime: C5 plus the MenuBarAgent, sandboxd and Control Center fixes).
- r91 2026-11-18 The cryptex cache key takes the target's model and its `model_gates`, not its whole `platform_data`: the model's OpenCore reasons there do not change the images, so they no longer rebuild them. Nothing it installs changes.
- r90 2026-11-17 The prespecialization gate refuses only a Swift-bearing image the overridden set gains. One it loses (a generic image has no Wi-Fi pack, so WiFiPeerToPeer is not overridden) keeps its descriptor slots nulled while the cache copy loads: the ordinary path, correct and slower. It is recorded (`gone_null_slots`). The cache edits are unchanged, so the 9,2 image is byte-identical.
- r89 2026-11-17 Row `codeid_carrier` -> codeid-carrier-20261117-002baf5f2e03-ae09c83d: the same objects (same row hash), with build.json naming the commit that produced it (ae09c83d), which release-cut's provenance check requires. Nothing installed changes.
- r88 2026-11-17 Model-neutral (GEN1, S15): the model gates rename the target's model, read from HAO_TARGET_MODEL and HAO_TARGET_DATA's `model_gates` (none for a target without it, such as the generic image); `src/model-gates.json` names no Mac, the 9,2's edit is declared by golden-gate-model-gates-mbp92, and the target is part of the cryptex cache key. The MPSMatrix reason names the GPU family, not a product. The 9,2's bytes are unchanged.
- r87 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r86 2026-11-17 Row `codeid_carrier` -> codeid-carrier-20261117-002baf5f2e03: the same carrier.o and libhao_codeid.a (same row hash), with a build.json that `src/codeid/make` now writes without a date or cache plan, so `tools/asset-build codeid_carrier` rebuilds the asset byte for byte. Nothing installed changes.
- r85 2026-11-17 Declares its scope (`requires`, docs/requires.md): userspace rows for both builds. Nothing it installs changes.
- r84 2026-11-16 The IO80211 and WiFiPeerToPeer compat installs, their two inputs, files and edit specs move to the new recipe golden-gate-wifi-compat (pack wifi-broadcom-modern), which runs right after this one; the same steps and bytes. The display-state shim and CoreWiFi compat stay: both serve every Intel Mac.
- r83 2026-11-16 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r82 2026-11-16 src/corewifi/make takes cache-dylib from this repository's tools/cache-dylib (was a separate checkout next to it; dsc.py is the same bytes) and its build record's commit from pipeline/source_commit.py. corewifi_compat unchanged.
- r81 2026-11-16 Merges r80 (this branch) with main's r79: both row changes below, no other change.
- r80 2026-11-16 Input row `golden_gate_userspace` (26B131) leaves the producer's record `userspace.json` out of its sha256, so a remake from the same IPSW matches (the five images are byte-identical); `build/make` runs the pinned ipsw 3.1.737 (input row `ipsw_tool`, or `--ipsw-tool`, or ipsw on PATH at that version) and stops with what to install when there is none. Installed bytes unchanged but the record's producer commit.
- r79 2026-11-16 Row `codeid_carrier`: its sha256 leaves out build.json (16120d97), so a source build of the same carrier.o and libhao_codeid.a (tools/asset-build) matches. Same artifact; installed bytes unchanged.
- r79 (branch) 2026-11-14 Input row `hao_runtime` (26B131) now names the DEV payload built from a clean local hao-runtime c66997a (hao-runtime-26B131-20261114-c66997a2a0bb; libhao b8f0480f, the bytes proven on the test laptop): native SwiftUI.View modifiers lend guest-made self with native nils (diag#0038). Dev images only until c66997a is pushed. No recipe change.
- r78 2026-11-14 The prespecialization gate counts an image the overridden set gains or loses only if it carries Swift metadata: the 14 Apple-silicon `AGXMetal*.bundle` drivers 27.x keeps on disk have no `__swift5` section (no process loads them on this Mac), so they change none of the edits; the record lists them (`ignored_without_swift`). A Swift-bearing difference still stops the build. The cache edits are unchanged.
- r77 2026-11-14 Model gates: this Mac's entry in 27.x's unsupported-model lists for Sidecar, AirPlay to Mac and Continuity Camera is renamed in the x86_64 cache (`src/model_gates.py`, three one-byte sites in `edits/cache-01.json`), in place of a FeatureUnlock kext. The OS cryptex's bytes change.
- r76 2026-11-14 Merges the two r75s: the Swift prespecialization edits of the r75 below (the OS cryptex's bytes change) with input row `hao_runtime` (26B131) at hao-runtime-26B131-20261114-79bc70ead081 (the other r75: guest dlopen resolves @rpath, @loader_path and @executable_path; Xcode starts). Nothing else changes.
- r75 2026-11-14 (main) Swift prespecialized metadata stays on in every process: in the x86_64 cache the two reads of `_dyld_shared_cache_some_image_overridden()` in libswiftCore answer false, and the 113 descriptor-map slots of overridden images (CoreWiFi, WiFiPeerToPeer) are emptied (`src/prespecialization.py`, data from the shared-cache generator). The build stops unless the image overrides exactly the 10 cache images the edits were made for. The OS cryptex's bytes change.
- r75 2026-11-14 (row update, made in parallel) Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 79bc70e tree (hao-runtime-26B131-20261114-79bc70ead081): guest dlopen resolves @rpath, @loader_path and @executable_path from the calling image (Xcode starts instead of aborting at launch). No recipe change.
- r74 2026-11-14 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime bd08b64 tree (hao-runtime-26B131-20261114-bd08b64a1563; libhao ffa4eeb6, the bytes proven on laptop 1 for Control Center): Finder (diag#0032), mds, the Control Center warm and cold-open fixes, native stack per callback level (diag#0005). No recipe change.
- r73 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 399e78e tree (hao-runtime-26B131-20261113-399e78e32376, tag v0.1.0-beta.1): Optional witness nils returned to native as a callback result (diag#0001). No recipe change.
- r72 2026-11-13 Documentation only: tests are cited as laptop tests on an image, and the cache-dylib tool is named without its repository. The hook and what it installs are unchanged.
- r71 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 1de47eb tree (hao-runtime-26B131-20261113-1de47eb87a7f): the ControlCenter GridSizes and Displayable refusals fixed (2b19233) and the stub skeletons (1de47eb). No recipe change.
- r70 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 9dce413 tree (hao-runtime-26B131-20261113-9dce4138ddfa): the ControlCenter crash fix and f66403c's float-HFA stack fix. No recipe change.
- r69 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r68 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 397b267 (hao-runtime-26B131-20261113-397b2675d037). No recipe change.
- r67 2026-11-13 wifip2p_compat (20822cf11958): add/removeAWDLDiscoveryClient succeed as no-ops instead of ENOTSUP. The call is new in 27.x and advisory; Tahoe's mDNSResponder never makes it and OCLP's wifip2pd brings AWDL up on its own, so mDNSResponder now behaves as Tahoe's does for AirDrop.
- r66 2026-11-13 wifip2p_compat (7eaffe614b3b): adds -[AWDLServiceDiscoveryManager add/removeAWDLDiscoveryClient:forService:error:] to OCLP's class (ENOTSUP); 27.x's mDNSResponder sent them at every AWDL service registration (AirDrop) and crashed on the unrecognized selector, 16 times in one session, taking DNS and Bonjour down. Plus 10 Wi-Fi Aware / WiFiP2PUIAgent stand-ins. make now checks OCLP's classes against their method lists read on the laptop (oclp-methods.txt), so such a gap fails the build instead of being "unverified". AirDrop over AWDL still needs a bridge to OCLP's discovery calls.
- r65 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 43af852 (hao-runtime-26B131-20261113-43af85296aa9). No recipe change.
- r64 2026-11-13 Split the cryptex image build into `src/cryptex.py` and reuse a sealed OS/App image pair built earlier from the same inputs, code and builder tools (`src/cryptex_cache.py`, `--cryptex-cache` from golden-run's `GOLDEN_CRYPTEX_CACHE`); without it the hook builds the images as before. The log says how long the build took.
- r63 2026-11-12 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 0a2c2aa (hao-runtime-26B131-20261112-0a2c2aad4755). No recipe change.
- r62 2026-11-12 Declare install attributes in patch.json `files` (root:wheel and `restricted` on the three wrappers, their renamed originals, CoreWiFi and the two job plists, as `src/install.py` leaves them) and where WindowServer's entitlements come from (`edits/windowserver-27-identity.json` `sign.entitlements_from`: the 27.x WindowServer, as `resign_with_entitlements` signs it); the hook is unchanged.
- r61 2026-11-12 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 273ba98 (hao-runtime-26B131-20261112-273ba980b829). No recipe change.
- r60 2026-11-12 Declare the original file each renamed wrapper starts from ("input") for pipeline/materialize.py; the hook is unchanged.
- r59 2026-11-12 Declare the files and edits in patch.json and edits/*.json (cache repairs, renamed wrappers, WindowServer, disabled job, cryptex builds, hao stubs); the hook and what it installs are unchanged.
- r58 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime d9ea9bd (4e68804 + the Swift value walk passes values under 8 bytes, which aborted Weather, StocksWidget, NewsTag and NewsToday2 on 7d0b612). No recipe change.
- r57 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 4e68804 (746f1ec + JIT TBL/EXT/integer and FRINT forms, native-call cache ownership fix, measured Swift value rules for the Stocks/News widgets and Weather, five review fixes); same async provider, units and AOT recipe. No recipe change.
- r56 2026-11-11 `src/corewifi/make` takes `dsc.py` from the `cache-dylib` tool (next to this repository or `CACHE_DYLIB`) and records that checkout's commit and dirty state in `build.json`. No recipe or output change.
- r55 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 746f1ec (12685dd + the native-call cache signal/fork fix) with the 26B131 async contract provider. No recipe change.
- r54 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 12685dd (5651d79 + x86-64-v2 build and a startup CPU-feature check). No recipe change.
- r53 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 5651d79 (native-call cache, JIT vector FP and LD1/ST1, scalar FP and conversions, round-to-odd FP32 FMA; u3 units + the optional-data contract). No recipe change.
- r52 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 790720d (CoreUtils va_list rows for rapportd, Optional-witness fix for Control Center, u3 interface units for Weather and Photos); its stubs, stub-identities and AOT come from it. No recipe change. The va_list and Control Center fixes were confirmed on the laptop on image 4d1c48c; image build and boot pending.
- r51 2026-11-11 The laptop test of r50 crashed airportd at start-up: `-[NSString redactedForWiFi]` (a Tahoe CoreWiFi category) unrecognized, because the cut-out library still claimed dyld-attached categories and libobjc skips those at launch (tools/cache-dylib r3 clears the flag; check.py refuses it). New Artifact test links CoreWiFi at launch and checks its categories attach. Row tahoe_native_donors re-cut; corewifi_compat unchanged.
- r50 2026-11-11 CoreWiFi: Tahoe 25G342's CoreWiFi (a native donor, cut from Tahoe's cache by tools/tahoe-donors r11) under a compat library (new input `corewifi_compat`, `src/corewifi`) that adds 2 symbols and 16 methods 27.x clients need; installed by the same wrapper-compat installer as IO80211/WiFiPeerToPeer (renamed `CoreWiFiTahoe.dylib`). Fixes airportd's crash on auto-join (27.x CoreWiFi's callouts and request numbers). The method pass gains every-class, run-time-added and strict modes for it; patch-table symbols only the replaced library uses need no answer. Replaces the separate golden-gate-corewifi recipe. Host tests only.
- r49 2026-11-11 Embed hao's stubs into the arm64-only programs of the App and OS cryptexes (Safari's agents, Safari's and WebKit's XPC services) before sealing; the App cryptex is rebuilt as a fresh sealed image when the payload has App stubs. New input `hao_runtime` (read from the share). Without them those programs ran through `/usr/libexec/hao` under hao's identity and Safari's SearchHelper aborted on an empty container identifier. The payload (hao-runtime 7415a0b) also ships `usr/lib/hao/stub-identities` (961 stubs, the 26 cryptex stubs included) that r48's carrier and golden-gate-native-identities read. Host tests only; image build and boot pending.
- r48 2026-11-11 Code identity carrier: hao-runtime's `hao_codeid_decide` (row `codeid_carrier`, hao-runtime 53d26c3 sources) linked into the free tails of the main x86_64 subcache and called after `SecCodeCheckValidityWithErrors` and `SecStaticCodeCheckValidityWithErrors` (their forwarders and SecTaskValidateForRequirement included), for every native and hao process; replaces golden-gate-code-identity's per-daemon interposer. Host-run against this Mac's x86_64 cache; planned on 26A428 and 26B131; boot validation pending.
- r47 2026-11-11 Repair the x86_64 cache's broken Swift conformance type references (47 in 26B131, 21 in 26A428; Apple's own RosettaOS cache) from the arm64e cache's records, and refuse a combined cryptex whose x86_64 cache still has a bad Swift metadata reference (`src/swift_refs.py`). Fixes the SIGBUS of Siri AI.app (Cmd-Space) in `swift_getTypeByMangledName`. Host: plan, apply, 4 KiB re-sign and verify on a copy of 26B131's cache (47 -> 0); image and boot validation pending.
- r46 2026-11-11 WiFiPeerToPeer compat: the `WiFiP2PKeychainAgent` stand-in answers `-activate`/`-invalidate` (27.x's WiFiAgent threw on them in every user session before opening its listener, so Setup's own Wi-Fi join timed out after 80 s), and `WiFiAwareStateMonitor` answers 27.x airportd's monitoring calls; all no-ops. New method-level pass (`src/io80211/methods.py`) in both libraries' coverage check: fails on a stand-in method an on-disk 27.x client names, records the rest. New `wifip2p_compat` artifacts for 26B131 and 26A428 (same library, checked against each build).
- r45 2026-11-11 `src/kc-hook.sh` is the real entry point (the runner no longer calls `install.py` itself); it takes the DriverKit donor and donor tool from `TAHOE_DRIVERKIT_ARTIFACT`/`TAHOE_DONOR_TOOL`. Removed the unused Tahoe-first mode (`--prepared-system`, the System/Data base-tree replacement and its keep list, the DriverKit fallbacks). README: 26B131, no `*_fix_source` rows, which frameworks come from 27.x. Same files installed (`golden-gate-userspace.json` drops the empty keep-list fields); boot validation pending.
- r44 2026-11-11 IO80211 compat also exports `Apple80211StartMonitoringEvent`: OCLP's wrapper forwards it through `dlsym(RTLD_NEXT)` and calls the result unchecked; in 27.x's translated processes that lookup is NULL (sharingd SIGSEGV loop on the 26B131 laptop, also on the earlier image). The compat function looks it up in IO80211Old.dylib by handle and keeps the wrapper's return value (0). Same library for 26A428 and 26B131 (coverage-checked on both).
- r43 2026-11-11 WiFiPeerToPeer compat: defines the 36 Wi-Fi Aware classes OCLP's 13.7.2 wrapper lacks (26A428 and 26B131 bind the same set), which made coreaudiod, AirPlayXPCHelper, ControlCenter and sharingd abort in libobjc; new row `wifip2p_compat`. The IO80211 coverage check takes the library name and skips clients OCLP overwrites.
- r42 2026-11-11 Locate CoreRoutine, EndpointSecurity and trackpad repair sites by unique instruction shapes in the supplied cache; preserve relocated branch targets and refuse missing or ambiguous sites. Audio and Metal repair source now comes directly from this checkout. Host fixtures and both 26A428/26B131 caches checked; boot validation pending.
- r41 2026-11-11 IO80211 compat for 26B131: adds `Apple80211Get20MhzChannelIndexBitmap` (bound by WiFiAnalytics in 26A428 and 26B131, absent from OCLP's wrapper) and 26B131's code 0xe0822410 -> -3998; `make` checks a build's clients against the library and OCLP's IO80211 and names the artifact after the build.
- r40 2026-11-10 Initial public release.
