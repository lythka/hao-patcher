# hao-runtime

Installs the hao runtime payload so ARM64-only Golden Gate programs run on the Intel Mac. hao is developed in
[hao-runtime](https://github.com/lythka/hao-runtime); this patch only decides where in the build the payload enters the
image.

## How it works
The input row `hao_runtime` selects a complete payload built from hao-runtime; its `build.json` identifies the runtime
commit and the exact target files. The kc-hook runs after [golden-gate-userspace](../golden-gate-userspace/README.md)
and runs the payload's `hao-install.py` against the offline System. The installer copies the runtime libraries, embeds
an x86_64 launcher slice into each selected ARM64-only executable, applies the payload's dyld repair and records the
installation in `usr/lib/hao/install.json`. Native Intel donors stay native.

Programs in the App and OS cryptexes (Safari's agents, Safari's and WebKit's XPC services) have manifest entries under
`System/Volumes/Preboot/Cryptexes/{App,OS}`, where the running system grafts those images. On the offline System
volume these are empty graft points, so `install.json` lists them as `missing`; golden-gate-userspace embeds them
into the cryptex images it builds (`src/cryptex_stubs.py`), before this recipe runs. The payload's `stub-identities`
lists them with the rest.

The `HaoExec` kext and the boot configuration are installed separately by [hao-opencore](../hao-opencore/README.md).
Use a payload built without the optional AOT preseed caches for the default profile.

## Verify
Check the payload's source and OS identities in its `build.json` and the installed `usr/lib/hao/install.json`, then
boot the image in [QEMU](../../tools/qemu-vm/README.md) before installing it on hardware. hao's own unit tests live in
hao-runtime and do not replace image boot and application checks.

## Validation
Laptop 2026-11-11, image 4d1c48c (26B131): the 7415a0b payload (r59) runs the image's ARM programs; hao 790720d (this revision's payload) swapped live fixes rapportd's and Control Center's crash loops, and its AOT caches load. Not yet exercised: the u3 units' Weather/Photos cases.
Earlier: earlier payloads booted Golden Gate through translated launchd to the desktop, and a Golden
Gate-first image reached SSH; the current profile needs a fresh image and application pass.

## Revisions
- r64 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 4e68804 (746f1ec + JIT TBL/EXT/integer and FRINT forms, native-call cache ownership fix, measured Swift value rules for the Stocks/News widgets and Weather, five review fixes); same async provider, units and AOT recipe. No recipe change.
- r63 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 746f1ec (12685dd + the native-call cache signal/fork fix) with the 26B131 async contract provider. No recipe change.
- r62 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 12685dd (5651d79 + x86-64-v2 build and a startup CPU-feature check). No recipe change.
- r61 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 5651d79 (native-call cache, JIT vector FP and LD1/ST1, scalar FP and conversions, round-to-odd FP32 FMA; u3 units + the optional-data contract). No recipe change.
- r60 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 790720d (CoreUtils va_list rows, Optional-witness fix, u3 interface units). No recipe change.
- r59 2026-11-11 26B131 payload rebuilt with hao-runtime 7415a0b from a root that includes the App and OS cryptexes: 963 programs embedded (937 before; the 26 new ones are in the cryptexes, embedded by golden-gate-userspace r49), and `usr/lib/hao/stub-identities` (hao-runtime 53d26c3). Boot validation pending.
- r58 2026-11-11 Select the 26B131 payload with regenerated interface units and AOT; async provider and URLRequest.httpBody adapter remain open. Boot validation pending.
- r57 2026-11-10 Initial public release.
