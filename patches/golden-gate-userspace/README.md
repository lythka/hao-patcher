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
- The native cache repairs' source is not an input row: `make.sh` copies `tools/metal-fixes` and
  `tools/audiomxd-fix` from this checkout into the staged recipe (`build/`).
- From the runner, not a row of this patch: `golden-image.py` sets `TAHOE_DRIVERKIT_ARTIFACT` (the assembly's
  `tahoe_driverkit_donors`) and `TAHOE_DONOR_TOOL` (`tools/tahoe-donors/make`), which `src/kc-hook.sh` passes on.

## How it works
`src/kc-hook.sh` runs `src/install.py` after [darwin27-kernel](../darwin27-kernel/README.md) has built the kernel
collections; the runner calls it like every other recipe. It:
- builds the combined OS cryptex from Golden Gate's native libraries and RosettaOS's x86_64 shared cache, adds
  Tahoe's generic Intel DriverKit cache, applies the native cache repairs, re-signs the cache with 4 KiB code pages
  (`src/cache_pages.py`; the x86_64 kernel registers only 4 KiB page signatures) and seals the image so 27.0's
  launchd can graft it;
- links Rosetta's cache directory to the combined cache (`src/cryptex-cache-alias.sh`), because native dyld still
  looks there;
- gives Tahoe programs that serve 27.0 clients (WindowServer) the 27.0 program's Info.plist and entitlements;
- installs the IO80211 compatibility library over OCLP's Modern Wireless wrapper, whose data exports 27.0's callers
  would otherwise call as code;
- installs the WiFiPeerToPeer compatibility library over OCLP's WiFiPeerToPeer wrapper, which lacks the Wi-Fi Aware
  classes 27.x's AirPlay stack extends;
- disables jobs that cannot run on an Intel Mac (`hybridsearchd` exits on non-Apple silicon and would restart in a
  loop);
- restores setuid/setgid modes after copying.

Which framework sits beneath OCLP's replacements is decided by the base layers, not here. With the 26B131 donor
artifact (`minimal-native-25G342-26B131`) Metal, OpenCL, AppleGVA, AppleGVACore, GPUCompiler, MTLCompiler, IO80211
and WiFiPeerToPeer are the 27.x build's own, with OCLP's overlay on top. The older 26A428 artifact still installs
Tahoe's copies of those frameworks first.

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

It requires the explicit DriverKit donor and never takes DriverKit from an earlier installed image. It installs OS libraries only; it does not generate
Swift metadata, shader warm-up caches, icon indexes or account state.

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests
# optional: WIFIP2P_OCLP_DIR / IO80211_OCLP_DIR = OCLP's 13.7.2-25 framework folders
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/build/tests
```
The fixtures cover the DriverKit requirement, the absence of any System-tree replacement, special modes, native cache repair plans
and adapter ordering. A built image must still pass filesystem, boot, native-service and application checks.

## Validation
NEEDS VALIDATION: host tests cover the current recipe, and a Golden Gate-first image assembled with it reached SSH;
loading of HID and serial DriverKit extensions with the added DriverKit cache, and a full application pass, are open.
The WiFiPeerToPeer library (r43) was swapped live onto a running 26B131 image on the laptop: AirPlayXPCHelper then
started without the libobjc abort. An image built with r43, and coreaudiod starting on it, are not yet tested.
r46 (stand-in methods) is host-tested only: an x86_64 probe sends WiFiAgent's `-activate`/`-invalidate` under
Rosetta. Not yet shown on an image: WiFiAgent logging "XPCListener started." in a user session, its
`com.apple.wifi.WiFiAgent` endpoint `active = 1`, and Setup's own join reaching the password sheet.

## Revisions
- r46 2026-11-11 WiFiPeerToPeer compat: the `WiFiP2PKeychainAgent` stand-in answers `-activate`/`-invalidate` (27.x's WiFiAgent threw on them in every user session before opening its listener, so Setup's own Wi-Fi join timed out after 80 s), and `WiFiAwareStateMonitor` answers 27.x airportd's monitoring calls; all no-ops. New method-level pass (`src/io80211/methods.py`) in both libraries' coverage check: fails on a stand-in method an on-disk 27.x client names, records the rest. New 26B131 `wifip2p_compat` artifact; 26A428 row unchanged (still r43).
- r45 2026-11-11 `src/kc-hook.sh` is the real entry point (the runner no longer calls `install.py` itself); it takes the DriverKit donor and donor tool from `TAHOE_DRIVERKIT_ARTIFACT`/`TAHOE_DONOR_TOOL`. Removed the unused Tahoe-first mode (`--prepared-system`, the System/Data base-tree replacement and its keep list, the DriverKit fallbacks). README: 26B131, no `*_fix_source` rows, which frameworks come from 27.x. Same files installed (`golden-gate-userspace.json` drops the empty keep-list fields); boot validation pending.
- r44 2026-11-11 IO80211 compat also exports `Apple80211StartMonitoringEvent`: OCLP's wrapper forwards it through `dlsym(RTLD_NEXT)` and calls the result unchecked; in 27.x's translated processes that lookup is NULL (sharingd SIGSEGV loop on the 26B131 laptop, also on the earlier image). The compat function looks it up in IO80211Old.dylib by handle and keeps the wrapper's return value (0). Same library for 26A428 and 26B131 (coverage-checked on both).
- r43 2026-11-11 WiFiPeerToPeer compat: defines the 36 Wi-Fi Aware classes OCLP's 13.7.2 wrapper lacks (26A428 and 26B131 bind the same set), which made coreaudiod, AirPlayXPCHelper, ControlCenter and sharingd abort in libobjc; new row `wifip2p_compat`. The IO80211 coverage check takes the library name and skips clients OCLP overwrites.
- r42 2026-11-11 Locate CoreRoutine, EndpointSecurity and trackpad repair sites by unique instruction shapes in the supplied cache; preserve relocated branch targets and refuse missing or ambiguous sites. Audio and Metal repair source now comes directly from this checkout. Host fixtures and both 26A428/26B131 caches checked; boot validation pending.
- r41 2026-11-11 IO80211 compat for 26B131: adds `Apple80211Get20MhzChannelIndexBitmap` (bound by WiFiAnalytics in 26A428 and 26B131, absent from OCLP's wrapper) and 26B131's code 0xe0822410 -> -3998; `make` checks a build's clients against the library and OCLP's IO80211 and names the artifact after the build.
- r40 2026-11-10 Initial public release.
