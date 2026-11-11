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
- `io80211_compat`: the IO80211 compatibility library, built and checked for one build by `src/io80211/make`.
- `wifip2p_compat`: the WiFiPeerToPeer compatibility library, built and checked for one build by `src/wifip2p/make`.
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
- installs the IO80211 compatibility library over OCLP's Modern Wireless wrapper, whose data exports 27.0's callers
  would otherwise call as code;
- installs the WiFiPeerToPeer compatibility library over OCLP's WiFiPeerToPeer wrapper, which lacks the Wi-Fi Aware
  classes 27.x's AirPlay stack extends;
- installs the CoreWiFi compatibility library in front of Tahoe's CoreWiFi, which the native Tahoe donors put at
  CoreWiFi's path, so Tahoe's airportd and every 27.x client share the CoreWiFi airportd was built with;
- disables jobs that cannot run on an Intel Mac (`hybridsearchd` exits on non-Apple silicon and would restart in a
  loop);
- restores setuid/setgid modes after copying.

Which framework sits beneath OCLP's replacements is decided by the base layers, not here. With the 26B131 donor
artifact (`minimal-native-25G342-26B131-corewifi-r3`) Metal, OpenCL, AppleGVA, AppleGVACore, GPUCompiler, MTLCompiler, IO80211
and WiFiPeerToPeer are the 27.x build's own, with OCLP's overlay on top. The older 26A428 artifact still installs
Tahoe's copies of those frameworks first.

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
recipe `cache_images`, with `tools/cache-dylib`) and install it at `CoreWiFi.framework/Versions/A/CoreWiFi` with its
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

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests
# optional: WIFIP2P_OCLP_DIR / IO80211_OCLP_DIR = OCLP's 13.7.2-25 framework folders
# optional on a macOS 27 host: COREWIFI_DONORS = tahoe_native_donors artifact, COREWIFI_COMPAT = corewifi_compat.dylib
# optional: HAO_RUNTIME_PAYLOAD (hao payload) + GOLDEN_APP_CRYPTEX (mounted app.dmg): real App cryptex embedding
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/build/tests
# code identity carrier: HAO_RUNTIME = hao-runtime checkout (runs it); CODEID_CACHE = an image cache folder (plans it)
HAO_RUNTIME=... CODEID_CACHE=.../System/Library/dyld python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -k codeid
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
Laptop 2026-11-11, image 4d1c48c (26B131): r46 Setup's own Wi-Fi join, r47 Spotlight without the Swift SIGBUS, r48 automatic login, r49 Safari; r51's Tahoe CoreWiFi auto-joins live and at boot; r52's hao 790720d fixes rapportd and Control Center live. Not yet exercised: the u3 units' Weather/Photos cases, SearchHelper's own identity.
Earlier: host tests cover the current recipe (r48's carrier runs against this Mac's x86_64 cache; no image
with it has been built or booted), and a Golden Gate-first image assembled with it reached SSH;
loading of HID and serial DriverKit extensions with the added DriverKit cache, and a full application pass, are open.
The WiFiPeerToPeer library (r43) was swapped live onto a running 26B131 image on the laptop: AirPlayXPCHelper then
started without the libobjc abort. An image built with r43, and coreaudiod starting on it, are not yet tested.
Laptop, image 4d1c48c (26B131, built from adfd15a0 = r49), 2026-11-11:
- r46: Setup's own Wi-Fi join shows WiFiAgent's password sheet; airportd receives `BEGIN REQ [ASSOC]` from WiFiAgent
  and joins (bf10de4 hung at "Connecting..."). Confirmed.
- r47: Cmd-Space opens the Spotlight panel in Siri AI.app without the SIGBUS (bf10de4 crashed every launch).
  Confirmed. (Its local app results are missing for an unrelated reason: Spotlight's ranking assets never download
  because ic_csr_internal makes the build look internal.)
- r48: after Setup creates the account, authd authorizes `system.login.console` with the auto-login mechanism in 16 s
  and Setup continues in the user session (bf10de4 hung: "client is not allowed to supply FUSAutologinDataKey").
  Confirmed.
- r49: Safari opens and loads pages; SearchHelper and the WebKit services' own identity not separately checked.
r50's live swap on the laptop crashed airportd at start-up (Tahoe CoreWiFi's categories not attached; fixed in r51).
r51 (Tahoe CoreWiFi + compat) on the laptop (4d1c48c, files swapped live, then persisted into the System snapshot):
airportd starts and maps CoreWiFi + CoreWiFiTahoe.dylib; Wi-Fi off/on auto-joins the remembered network in 14-27 s
with no airportd `.ips`; control on the same laptop with the cached 27.x CoreWiFi never auto-joins ("All scan results
are filtered out ... Known network is found nearby but not joined"); after a reboot with the files persisted, Wi-Fi
auto-joined at boot (airportd runs = 1). Not yet: an image built with r51.

## Revisions
- r52 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 790720d (CoreUtils va_list rows for rapportd, Optional-witness fix for Control Center, u3 interface units for Weather and Photos); its stubs, stub-identities and AOT come from it. No recipe change. The va_list and Control Center fixes were confirmed by a live swap on image 4d1c48c; image build and boot pending.
- r51 2026-11-11 The live test of r50 crashed airportd at start-up: `-[NSString redactedForWiFi]` (a Tahoe CoreWiFi category) unrecognized, because the cut-out library still claimed dyld-attached categories and libobjc skips those at launch (tools/cache-dylib r3 clears the flag; check.py refuses it). New Artifact test links CoreWiFi at launch and checks its categories attach. Row tahoe_native_donors re-cut; corewifi_compat unchanged.
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
