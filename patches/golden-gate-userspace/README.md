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
- `io80211_compat`: the IO80211 compatibility library, built by `src/io80211/make`.
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
- r40 2026-11-10 Initial public release.
