# golden-gate-userspace

Installs Golden Gate (macOS 27.0, 26A428) userspace into the Intel image: its system libraries, the combined OS
cryptex with the x86_64 shared cache, and the adapters that let 27.0 clients use the Tahoe programs and drivers kept
for this Mac. In the Golden Gate-first build, System, Data and the Tahoe donors are already in place, and
`--prepared-system` keeps those trees.

## Inputs
- `golden_gate_userspace`: the IPSW's filesystem, SystemOS, AppOS and RosettaOS images, made by
  [build/](build/README.md).
- `display_state_shim`, `display_install_helper`: display adapters and their installer
  ([tools/display-fixes](../../tools/display-fixes)).
- `io80211_compat`: the IO80211 compatibility library, built and checked for one build by `src/io80211/make`.
- `metal_fix_source`, `audio_fix_source`: source of the native cache repairs.
- The Golden Gate-first runner also passes a Tahoe DriverKit artifact.

## How it works
`src/kc-hook.sh` runs `src/install.py` after [darwin27-kernel](../darwin27-kernel/README.md) has built the kernel
collections. It:
- builds the combined OS cryptex from Golden Gate's native libraries and RosettaOS's x86_64 shared cache, adds
  Tahoe's generic Intel DriverKit cache, applies the native cache repairs, re-signs the cache with 4 KiB code pages
  (`src/cache_pages.py`; the x86_64 kernel registers only 4 KiB page signatures) and seals the image so 27.0's
  launchd can graft it;
- links Rosetta's cache directory to the combined cache (`src/cryptex-cache-alias.sh`), because native dyld still
  looks there;
- gives Tahoe programs that serve 27.0 clients (WindowServer) the 27.0 program's Info.plist and entitlements;
- installs the IO80211 compatibility library over OCLP's Modern Wireless wrapper, whose data exports 27.0's callers
  would otherwise call as code;
- disables jobs that cannot run on an Intel Mac (`hybridsearchd` exits on non-Apple silicon and would restart in a
  loop);
- restores setuid/setgid modes after copying, and keeps the Tahoe framework bases beneath OCLP's replacements.

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
the cache and 57 from disk. `Apple80211GetBeaconCache`, also absent from the wrapper, is bound by no client of either.

It refuses to inherit DriverKit from an earlier installed image. It installs OS libraries only; it does not generate
Swift metadata, shader warm-up caches, icon indexes or account state.

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/build/tests
```
The fixtures cover prepared-System preservation, the DriverKit requirement, special modes, native cache repair plans
and adapter ordering. A built image must still pass filesystem, boot, native-service and application checks.

## Validation
NEEDS VALIDATION: host tests cover the current recipe, and a Golden Gate-first image assembled with it reached SSH;
loading of HID and serial DriverKit extensions with the added DriverKit cache, and a full application pass, are open.

## Revisions
- r42 2026-11-11 Locate CoreRoutine, EndpointSecurity and trackpad repair sites by unique instruction shapes in the supplied cache; preserve relocated branch targets and refuse missing or ambiguous sites. Audio and Metal repair source now comes directly from this checkout. Host fixtures and both 26A428/26B131 caches checked; boot validation pending.
- r41 2026-11-11 IO80211 compat for 26B131: adds `Apple80211Get20MhzChannelIndexBitmap` (bound by WiFiAnalytics in 26A428 and 26B131, absent from OCLP's wrapper) and 26B131's code 0xe0822410 -> -3998; `make` checks a build's clients against the library and OCLP's IO80211 and names the artifact after the build.
- r40 2026-11-10 Initial public release.
